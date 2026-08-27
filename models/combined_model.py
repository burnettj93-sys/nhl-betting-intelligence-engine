"""
Ties Model A (Elo team strength) + Model C-lite (player quality) + Model
E-lite (goalie) + a rest/back-to-back adjustment into one moneyline
probability, with an uncertainty band — all read through the point-in-time
layer (features/point_in_time.py) so nothing here can see a fact before it
was actually observed.

Two-phase reproducibility design:
  1. `_build_feature_snapshot()` does all the DB reads (point-in-time) and
     returns a plain dict of numbers — this is the only part of predict()
     that touches the database.
  2. `compute_probability_from_features()` is a PURE function over that
     dict — no DB, no randomness, no wall-clock. Calling it twice with the
     same dict always returns exactly the same numbers.
That split is what makes a stored prediction replayable: persist the
feature snapshot (pricing/decision.py does this), and
`reproduce_prediction()` below re-derives the same probability from it
without touching the database at all.

Walk-forward guarantee: predict() is read-only; only learn() mutates
model state, and only for a game whose predict() has already run. Nothing
in this module ever reads a game's own result before producing that
game's prediction.

v2.1 (temporal-hardening pass) additions:
  - `_build_feature_snapshot()` now resolves home/away/game_date/
    scheduled_start via `features.point_in_time.game_schedule_as_of()`
    (the append-only schedule history) instead of the `games` table's
    mutable cache columns, so a later schedule correction can't change
    what an earlier prediction reconstructs.
  - `learn()` now reads player/goalie postgame stats through
    `pit.player_game_stats_as_of()` / `pit.goalie_game_stats_as_of()`
    (the latest revision observed by a `learn_time_utc`, defaulting to the
    game's own `result_observed_at_utc`), so a later box-score correction
    can't retroactively change what a historical model update learned.
  - IN-MEMORY MODEL STATE (Elo ratings, player ratings, goalie ratings,
    season-maturity counters) is itself a temporal-leakage vector that no
    amount of point-in-time SQL alone can close: nothing previously
    stopped a caller from training a model instance through game 100 and
    then calling `.predict()` on that SAME instance for game 75, silently
    contaminating the "historical" game-75 prediction with information
    from games 76-100. `predict()` now tracks `trained_through_observed_at`
    and RAISES `ContaminatedModelStateError` rather than silently
    returning a wrong prediction if this happens (item 9/10). The
    sanctioned way to get a temporally-correct model instance for a given
    prediction_time_utc is `build_model_state_as_of()` below (item 11) —
    it is the ONE authoritative place that reconstructs model state,
    walking only `pit.completed_games_known_before()` (never game_id or
    game_date order).
"""
from __future__ import annotations

import datetime as dt
import json
import sqlite3
from dataclasses import dataclass, field

import config
from features import point_in_time as pit
from models.elo_model import EloModel
from models.goalie_model import GoalieRatingModel
from models.player_model import PlayerRatingModel


class ContaminatedModelStateError(RuntimeError):
    """Raised by predict() when the model instance has already learned
    from a game whose result was observed AFTER the requested
    prediction_time_utc. This is not a data problem -- it's a caller
    trying to reuse a model instance across an impossible time boundary.
    The fix is never to catch and ignore this; it's to build a fresh,
    correctly-scoped model instance via build_model_state_as_of()."""


@dataclass
class GamePrediction:
    game_id: int
    prediction_time_utc: str
    game_date: str
    scheduled_start_utc: str
    home_team: str
    away_team: str
    home_score: int | None
    away_score: int | None
    model_prob_home: float
    conservative_prob_home: float
    # NOTE (v2.1, spec item 13): ci_low/ci_high are a HEURISTIC
    # maturity-based uncertainty band -- kept under these names for
    # schema/API compatibility -- NOT a statistically validated confidence
    # interval. See config.BASE_UNCERTAINTY_BAND_HALF_WIDTH's docstring.
    ci_low: float
    ci_high: float
    home_goalie_status: str
    away_goalie_status: str
    feature_snapshot: dict = field(default_factory=dict)
    model_version: str = config.MODEL_VERSION
    feature_version: str = config.FEATURE_VERSION


def compute_probability_from_features(fs: dict) -> dict:
    """PURE function: feature snapshot -> probabilities. No DB access.
    See module docstring — this is the reproducibility contract."""
    home_r = (fs["elo_home"] + fs["home_advantage"] + fs["player_quality_home"]
              + fs["goalie_adj_home"] + fs["rest_adj_home"])
    away_r = (fs["elo_away"] + fs["player_quality_away"]
              + fs["goalie_adj_away"] + fs["rest_adj_away"])
    p_home = 1.0 / (1.0 + 10 ** (-(home_r - away_r) / 400.0))

    half_width = fs["ci_half_width_base"] * fs["goalie_uncertainty_widening"]
    ci_low = max(p_home - half_width, 0.01)
    ci_high = min(p_home + half_width, 0.99)
    # Spec sec.35/47's worked example uses the CI lower bound itself as the
    # conservative probability used for wager eligibility.
    conservative = ci_low

    return {
        "model_prob_home": p_home,
        "conservative_prob_home": conservative,
        "ci_low": ci_low,
        "ci_high": ci_high,
    }


class CombinedMoneylineModel:
    def __init__(self, teams: list[str]):
        self.elo = EloModel(teams)
        self.player_model = PlayerRatingModel()
        self.goalie_model = GoalieRatingModel()
        self._games_played_this_season: dict[str, int] = {t: 0 for t in teams}
        self._current_season: str | None = None
        # v2.1: the latest observed_at_utc of any fact this instance has
        # actually learned from. None means untrained. See
        # ContaminatedModelStateError and _check_not_contaminated().
        #
        # v2.1.1a (spec item 2), extended v2.1.2 (spec item 3): despite
        # the name (kept for backward compatibility -- see learn()'s
        # docstring), this is NOT simply "the game's first-observed
        # result time." It is the true knowledge watermark: the latest
        # observed_at_utc across every result/schedule/player-stat/
        # goalie-stat revision actually consumed by learn(). For ordinary
        # walk-forward learning (learn_time_utc left at its default)
        # these are numerically identical, so nothing changes for the
        # common case -- they diverge only when a caller explicitly
        # passes a later learn_time_utc to deliberately consume a
        # correction, which is exactly the case this watermark must get
        # right. See learn()'s docstring for the full rationale.
        self.trained_through_observed_at: str | None = None

    def _check_not_contaminated(self, prediction_time_utc: str) -> None:
        """Refuse to predict from future-trained state rather than
        silently doing it (spec item 9/10).

        v2.1.1 (exact-timestamp semantics, item 4): the authoritative
        training-eligibility query (completed_games_known_before) uses
        STRICT-BEFORE semantics by default -- a result first observed at
        exactly prediction_time_utc is NOT eligible to have been learned
        for that prediction. This guard must therefore reject
        `trained_through_observed_at == prediction_time_utc` too, not only
        `>` -- otherwise a model that legitimately learned a result at
        precisely T would be silently allowed to predict AT T, even though
        build_model_state_as_of(T) would never have learned that same
        result itself. See tests/test_model_state_integrity.py's
        exact-timestamp-tie cases."""
        if (self.trained_through_observed_at is not None
                and self.trained_through_observed_at >= prediction_time_utc):
            raise ContaminatedModelStateError(
                f"this model instance has already learned from a result observed at "
                f"{self.trained_through_observed_at}, which is at-or-after the requested "
                f"prediction_time_utc {prediction_time_utc} -- under the strict-before "
                f"training-eligibility policy that result was never eligible for this "
                f"prediction, so it cannot be used to produce it. Use "
                f"build_model_state_as_of() to reconstruct a correctly-scoped model "
                f"instance instead."
            )

    def _maybe_new_season(self, season_label: str) -> None:
        """Spec item 7: season-specific counters must reset at each season
        boundary, not accumulate across seasons forever."""
        if self._current_season is not None and season_label != self._current_season:
            for t in self._games_played_this_season:
                self._games_played_this_season[t] = 0
        self.elo.maybe_regress_new_season(season_label)
        self._current_season = season_label

    # ------------------------------------------------------------ predict --

    def _build_feature_snapshot(self, conn: sqlite3.Connection, game_id: int,
                                 prediction_time_utc: str) -> dict:
        row = conn.execute("SELECT season FROM games WHERE game_id=?", (game_id,)).fetchone()
        if row is None:
            raise ValueError(f"unknown game_id {game_id}")
        season = row["season"]   # stable identity, never revised -- see schema.sql
        # v2.1: home/away/game_date/scheduled_start come from the
        # point-in-time schedule history, NOT games' mutable cache columns
        # -- a schedule correction observed after prediction_time_utc must
        # not be visible here.
        sched = pit.game_schedule_as_of(conn, game_id, prediction_time_utc)
        if sched is None:
            raise ValueError(f"no schedule observed for game_id {game_id} as of {prediction_time_utc}")
        home, away = sched["home_team"], sched["away_team"]
        self._maybe_new_season(season)

        def skaters_only(conn, ids):
            if not ids:
                return []
            placeholders = ",".join("?" * len(ids))
            rows = conn.execute(
                f"SELECT player_id FROM players WHERE player_id IN ({placeholders}) "
                f"AND position != 'G'", ids,
            ).fetchall()
            return [r["player_id"] for r in rows]

        avail_home_all = pit.available_roster(conn, home, prediction_time_utc)
        avail_away_all = pit.available_roster(conn, away, prediction_time_utc)
        avail_home_skaters = skaters_only(conn, avail_home_all)
        avail_away_skaters = skaters_only(conn, avail_away_all)

        player_quality_home = self.player_model.team_available_quality_elo(avail_home_skaters)
        player_quality_away = self.player_model.team_available_quality_elo(avail_away_skaters)

        g_home = pit.goalie_status(conn, game_id, home, prediction_time_utc)
        g_away = pit.goalie_status(conn, game_id, away, prediction_time_utc)
        goalie_adj_home, widen_home = self.goalie_model.rating_adjustment_elo(
            g_home.player_id, confirmed=(g_home.status == "CONFIRMED"))
        goalie_adj_away, widen_away = self.goalie_model.rating_adjustment_elo(
            g_away.player_id, confirmed=(g_away.status == "CONFIRMED"))

        rest_home = pit.rest_context(conn, game_id, home, prediction_time_utc)
        rest_away = pit.rest_context(conn, game_id, away, prediction_time_utc)
        rest_adj_home = self._rest_penalty(rest_home)
        rest_adj_away = self._rest_penalty(rest_away)

        maturity = min(self._games_played_this_season.get(home, 0),
                        self._games_played_this_season.get(away, 0))
        frac_mature = min(maturity / config.UNCERTAINTY_BAND_GAMES_TO_MATURITY, 1.0)
        ci_half_width_base = config.BASE_UNCERTAINTY_BAND_HALF_WIDTH - frac_mature * (
            config.BASE_UNCERTAINTY_BAND_HALF_WIDTH - config.MIN_UNCERTAINTY_BAND_HALF_WIDTH
        )

        return {
            "game_id": game_id,
            "prediction_time_utc": prediction_time_utc,
            "home_team": home, "away_team": away, "season": season,
            "game_date": sched["game_date"], "scheduled_start_utc": sched["scheduled_start_utc"],
            "elo_home": self.elo.ratings[home], "elo_away": self.elo.ratings[away],
            "home_advantage": config.ELO_HOME_ADVANTAGE,
            "player_quality_home": player_quality_home, "player_quality_away": player_quality_away,
            "goalie_adj_home": goalie_adj_home, "goalie_adj_away": goalie_adj_away,
            "rest_adj_home": rest_adj_home, "rest_adj_away": rest_adj_away,
            "goalie_uncertainty_widening": max(widen_home, widen_away),
            "ci_half_width_base": ci_half_width_base,
            "home_goalie_status": g_home.status, "away_goalie_status": g_away.status,
            "home_goalie_id": g_home.player_id, "away_goalie_id": g_away.player_id,
            "rest_home": rest_home, "rest_away": rest_away,
            "season_maturity_games": maturity,
            "model_version": config.MODEL_VERSION, "feature_version": config.FEATURE_VERSION,
        }

    @staticmethod
    def _rest_penalty(rest_ctx: dict) -> float:
        penalty = 0.0
        if rest_ctx.get("back_to_back"):
            penalty -= config.BACK_TO_BACK_PENALTY
        elif rest_ctx.get("three_in_four"):
            penalty -= config.THREE_IN_FOUR_PENALTY
        if rest_ctx.get("four_in_six"):
            penalty -= config.FOUR_IN_SIX_PENALTY
        return penalty

    def predict(self, conn: sqlite3.Connection, game_id: int, prediction_time_utc: str
                ) -> GamePrediction:
        # v2.1: refuse rather than silently mispredict from future-trained
        # state -- see ContaminatedModelStateError / _check_not_contaminated.
        self._check_not_contaminated(prediction_time_utc)
        fs = self._build_feature_snapshot(conn, game_id, prediction_time_utc)
        probs = compute_probability_from_features(fs)

        # home_score/away_score below are the ACTUAL final result, included
        # purely for display/reporting convenience (e.g. run_slate.py's
        # "(actual result: ...)" line) -- they are NEVER part of
        # feature_snapshot and never feed compute_probability_from_features,
        # so including them here creates no leakage risk.
        row = conn.execute("SELECT home_score, away_score FROM games WHERE game_id=?",
                            (game_id,)).fetchone()
        return GamePrediction(
            game_id=game_id, prediction_time_utc=prediction_time_utc,
            game_date=fs["game_date"], scheduled_start_utc=fs["scheduled_start_utc"],
            home_team=fs["home_team"], away_team=fs["away_team"],
            home_score=row["home_score"], away_score=row["away_score"],
            model_prob_home=probs["model_prob_home"],
            conservative_prob_home=probs["conservative_prob_home"],
            ci_low=probs["ci_low"], ci_high=probs["ci_high"],
            home_goalie_status=fs["home_goalie_status"], away_goalie_status=fs["away_goalie_status"],
            feature_snapshot=fs,
        )

    # -------------------------------------------------------------- learn --

    def learn(self, conn: sqlite3.Connection, game_id: int, learn_time_utc: str | None = None
              ) -> None:
        """Update every sub-model with what actually happened. Only ever
        call this AFTER predict() for the same game, and only once the
        game is FINAL — never before.

        v2.1: `learn_time_utc` controls which REVISION of the player/goalie
        postgame stats is used (see pit.player_game_stats_as_of /
        goalie_game_stats_as_of) -- a later box-score correction observed
        after learn_time_utc is correctly ignored.

        v2.1.1: the same discipline now applies to the RESULT itself
        (spec item 2/6). `learn_time_utc` defaults to
        pit.game_result_first_observed_at() -- the earliest legitimate
        moment this model is allowed to learn from the game at all -- and
        the actual score/period-type used is read through
        pit.game_result_as_of(game_id, learn_time_utc), NEVER from the
        mutable games.home_score/away_score cache columns. A score
        correction observed after learn_time_utc is therefore correctly
        ignored, exactly like a stat correction. `season` is still read
        from the CURRENT games row deliberately -- game_id/season are
        stable identity, never revised (see schema.sql).

        v2.1.1a (spec item 4, Policy A): home_team/away_team are NOT
        read from the current games row either -- schema.sql explicitly
        documents those columns as a latest-known CACHE ONLY, and
        game_schedule_events (the append-only history) is revision-
        capable for exactly these fields, same as venue/start time. A
        model instance that predicted this game via _build_feature_snapshot
        already resolved home/away through pit.game_schedule_as_of() at
        its own prediction_time_utc; learn() now resolves the SAME way,
        via pit.game_schedule_as_of(conn, game_id, learn_time_utc) --
        the latest schedule revision known by the moment this model is
        recording the result -- so Elo/season-games-played can never
        credit the wrong team even if a schedule correction changed
        home/away identity between prediction and learning. See
        tests/test_home_away_revision_consistency.py."""
        row = conn.execute("SELECT * FROM games WHERE game_id=?", (game_id,)).fetchone()
        if row is None:
            raise ValueError(f"unknown game_id {game_id}")
        if learn_time_utc is None:
            learn_time_utc = pit.game_result_first_observed_at(conn, game_id)
        result = pit.game_result_as_of(conn, game_id, learn_time_utc) if learn_time_utc else None
        if result is None:
            raise ValueError(
                f"cannot learn from game {game_id}: no FINAL result had been observed "
                f"by {learn_time_utc}")
        sched = pit.game_schedule_as_of(conn, game_id, learn_time_utc)
        if sched is None:
            raise ValueError(
                f"cannot learn from game {game_id}: no schedule fact had been observed "
                f"by {learn_time_utc}")
        self._maybe_new_season(row["season"])
        home, away = sched["home_team"], sched["away_team"]
        home_won = result["home_score"] > result["away_score"]
        self.elo.update(home, away, home_won)
        self._games_played_this_season[home] = self._games_played_this_season.get(home, 0) + 1
        self._games_played_this_season[away] = self._games_played_this_season.get(away, 0) + 1

        # v2.1.1a (spec item 2): track the latest observed_at_utc of ANY
        # fact actually consumed below -- the result revision used, and
        # every player/goalie stat revision used -- not just the game's
        # first-observed result time. When learn_time_utc is the default
        # (None -> game_result_first_observed_at()), every one of these
        # observation timestamps is guaranteed <= that default value by
        # construction (game_result_as_of/player_game_stats_as_of/
        # goalie_game_stats_as_of all filter observed_at_utc <=
        # learn_time_utc), so knowledge_through_utc collapses to exactly
        # the old first-observed-time behavior and nothing changes for
        # ordinary walk-forward learning. It only differs when a caller
        # EXPLICITLY passes a later learn_time_utc to deliberately
        # consume a correction (see learn_time_utc's docstring above) --
        # in that case the watermark must reflect how far forward the
        # consumed information actually reaches, or a model that just
        # learned Tuesday's corrected result could still be asked to
        # predict Monday night, silently contaminating that "historical"
        # prediction with Tuesday's information. See
        # ContaminatedModelStateError / _check_not_contaminated() and
        # tests/test_model_knowledge_watermark.py.
        #
        # v2.1.2 (spec item 3): this v2.1.1a fix was itself incomplete --
        # learn() ALSO consumes `sched` (via pit.game_schedule_as_of(),
        # spec item 4/Policy A), but sched["observed_at_utc"] was never
        # folded into the watermark, leaving exactly the same leakage
        # class item 2 was supposed to close: a model that explicitly
        # consumed a Tuesday home/away schedule correction could still be
        # allowed to predict Monday night. `sched` is now included from
        # the start, not just result/player/goalie revisions. See
        # tests/test_model_knowledge_watermark.py::
        # TestScheduleRevisionContaminatesTheWatermark.
        knowledge_through_utc = result["observed_at_utc"]
        if sched["observed_at_utc"] > knowledge_through_utc:
            knowledge_through_utc = sched["observed_at_utc"]

        for pgs in pit.player_game_stats_as_of(conn, game_id, learn_time_utc):
            self.player_model.update(pgs["player_id"], pgs["goals"], pgs["assists"])
            if pgs["observed_at_utc"] > knowledge_through_utc:
                knowledge_through_utc = pgs["observed_at_utc"]

        for gg in pit.goalie_game_stats_as_of(conn, game_id, learn_time_utc):
            self.goalie_model.update(gg["player_id"], gg["saves"], gg["shots_against"])
            if gg["observed_at_utc"] > knowledge_through_utc:
                knowledge_through_utc = gg["observed_at_utc"]

        if (self.trained_through_observed_at is None
                or knowledge_through_utc > self.trained_through_observed_at):
            self.trained_through_observed_at = knowledge_through_utc

    # --------------------------------------------------------- orchestration --

    @staticmethod
    def all_final_game_ids(conn: sqlite3.Connection) -> list[int]:
        """v2.1: delegates to the single sanctioned training-eligibility
        function (features.point_in_time.completed_games_known_before),
        which orders by result_observed_at_utc -- NEVER game_id or
        game_date -- so a rescheduled or late-finishing game resolves
        correctly. Kept as a method here only for call-site compatibility;
        do not reimplement this ordering anywhere else."""
        return pit.completed_games_known_before(conn)

    @staticmethod
    def prediction_time_for_game(conn: sqlite3.Connection, game_id: int,
                                  offset_minutes_before_start: int = 30) -> str:
        """Default backtest anchor: N minutes before scheduled puck drop —
        matches the spec's pregame timeline (sec.49/58). NOTE: this
        deliberately reads the CURRENT/latest-known games cache, not a
        point-in-time schedule reconstruction -- it is choosing an
        operational "when would we have priced this" anchor using
        everything known now, not reconstructing what was known at some
        earlier time. Once an anchor is chosen, every downstream feature
        read for it IS point-in-time gated (see _build_feature_snapshot)."""
        row = conn.execute("SELECT scheduled_start_utc FROM games WHERE game_id=?",
                            (game_id,)).fetchone()
        start = dt.datetime.fromisoformat(row["scheduled_start_utc"])
        return (start - dt.timedelta(minutes=offset_minutes_before_start)).isoformat()

    def process_games(self, conn: sqlite3.Connection, game_ids: list[int],
                       learn: bool = True, store_predictions: bool = True,
                       offset_minutes_before_start: int = 30) -> list[GamePrediction]:
        """v2.1 (spec item 20): walks a CHRONOLOGICALLY MERGED event stream
        of (predict at prediction_time_utc) and (learn at
        result_observed_at_utc) events across ALL given games, strictly in
        time order -- NOT the naive "for each game: predict, then
        immediately learn its own result" loop this used to be. That naive
        per-game loop is unsafe whenever two games' prediction/result
        windows overlap: e.g. several games the same night, where
        learning game A's result (only observed ~hours after ITS puck
        drop) could otherwise happen before predicting game B, even
        though B's own prediction_time preceded A's result becoming
        known -- exactly the scenario ContaminatedModelStateError exists
        to catch (see tests/test_model_state_integrity.py and
        tests/test_game_id_independence.py). The returned list is
        reordered back to match the input game_ids order for caller
        convenience; the PROCESSING order (which is what determines what
        each prediction can see) is always the safe chronological merge."""
        from pricing import decision as decision_mod  # local import avoids a cycle

        predict_events = []   # (prediction_time_utc, game_id)
        learn_events = []     # (result_observed_at_utc, game_id)
        for gid in game_ids:
            p_time = self.prediction_time_for_game(conn, gid, offset_minutes_before_start)
            predict_events.append((p_time, gid))
            if learn:
                # v2.1.1: the learn event's timestamp is the game's
                # first-observed result time from the append-only
                # game_result_events table -- never games.
                # result_observed_at_utc (current-state cache only, not
                # authoritative -- see schema.sql / point_in_time.py).
                first_observed = pit.game_result_first_observed_at(conn, gid)
                if first_observed is None:
                    raise ValueError(f"cannot learn from non-FINAL game {gid}")
                learn_events.append((first_observed, gid))

        # kind=0 (predict) sorts before kind=1 (learn) at an exact tie --
        # a game predicts itself before it could ever learn its own result.
        events = sorted(
            [(ts, 0, gid) for ts, gid in predict_events]
            + [(ts, 1, gid) for ts, gid in learn_events],
            key=lambda e: (e[0], e[1]),
        )

        predictions_by_gid: dict[int, GamePrediction] = {}
        for ts, kind, gid in events:
            if kind == 0:
                pred = self.predict(conn, gid, ts)
                predictions_by_gid[gid] = pred
                if store_predictions:
                    decision_mod.persist_bare_prediction(conn, pred)
            else:
                self.learn(conn, gid, learn_time_utc=ts)
        conn.commit()
        return [predictions_by_gid[gid] for gid in game_ids if gid in predictions_by_gid]

    def process_all_games(self, conn: sqlite3.Connection, store_predictions: bool = True
                           ) -> list[GamePrediction]:
        return self.process_games(conn, self.all_final_game_ids(conn),
                                   learn=True, store_predictions=store_predictions)


def reproduce_prediction(feature_snapshot: dict) -> dict:
    """Re-derive probabilities from a STORED feature snapshot, touching no
    database. Used by tests/test_reproducibility.py to prove that replaying
    the same inputs through the same model_version yields the same output."""
    return compute_probability_from_features(feature_snapshot)


def build_model_state_as_of(conn: sqlite3.Connection, prediction_time_utc: str,
                             teams: list[str]) -> "CombinedMoneylineModel":
    """THE single authoritative way to reconstruct model state (Elo,
    player ratings, goalie ratings, season-maturity counters) as of a
    given timestamp (spec item 11). Initializes a clean model, then walks
    ONLY the games whose results were genuinely known before
    prediction_time_utc (pit.completed_games_known_before -- never
    game_id/game_date order), learning from each in the order its result
    actually became known, and stops there. The returned model is
    guaranteed safe to call .predict() on for any game at
    prediction_time_utc (or later) -- see ContaminatedModelStateError,
    which would otherwise be the failure mode of hand-rolling this.

    Callers should prefer this over manually calling process_games()/
    learn() when the goal is specifically "give me a model instance whose
    state matches what existed at time T" -- e.g. reconstructing or
    auditing a historical prediction. It intentionally does NOT call
    predict() or persist anything; it exists purely to produce state."""
    model = CombinedMoneylineModel(teams)
    for game_id in pit.completed_games_known_before(conn, prediction_time_utc, strict=True):
        model.learn(conn, game_id)
    return model
