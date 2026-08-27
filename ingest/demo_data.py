"""
Synthetic multi-season dataset so the pipeline can run end to end without
network access (see README — the real NHL API is unreachable from this
sandbox). Populates the FULL temporal schema (team membership, roster
status, lineup, goalie status events, DraftKings odds snapshots), not just
a flat games table, so the point-in-time layer has real data to exercise.

Honesty notes (fixed from the v1 scaffold, per explicit review feedback):
  - This is a SHORTENED schedule — each team plays every other team
    `games_per_pairing` times (default 2 => 22 games/team/season). It is
    NOT "82-ish" and is never described that way. See SEASON_GAMES_NOTE.
  - A team can never be scheduled twice on the same date (see
    _schedule_games's greedy same-day-conflict avoidance).
  - OT/SO games always produce exactly one winner (asserted below).
  - A player can be injured, recover, and be injured again — the old bug
    where `out_until` staying non-None blocked all future injury rolls
    is fixed by keying eligibility off `status == 'ACTIVE'`, not `out_until
    is None`.
  - Fully deterministic: everything is drawn from a `random.Random(seed)`
    instance threaded through explicitly — nothing touches the global
    `random` module, so two calls with the same seed produce byte-identical
    databases (see tests/test_demo_data.py::test_determinism).

The generator holds a hidden "true_strength" per team and "true_skill" per
player that the model never sees directly — only the resulting game
scores, player stats, roster events, and odds it produces. That's what
lets backtest.py's calibration check mean something.
"""
from __future__ import annotations

import datetime as dt
import random

TEAMS = ["TOR", "MTL", "BOS", "TBL", "FLA", "OTT", "BUF", "DET", "NYR", "NYI", "NJD", "WSH"]

GAMES_PER_PAIRING = 2   # each ORDERED (home, away) pair meets this many times per season
# Each team meets each of the other (len(TEAMS)-1) opponents GAMES_PER_PAIRING
# times as HOME and GAMES_PER_PAIRING times as AWAY -> factor of 2.
GAMES_PER_TEAM_PER_SEASON = GAMES_PER_PAIRING * 2 * (len(TEAMS) - 1)
SEASON_GAMES_NOTE = (
    f"{GAMES_PER_TEAM_PER_SEASON} games/team/season "
    f"(a deliberately shortened round-robin, NOT a full 82-game NHL season)"
)


def _make_roster(rng: random.Random, team: str) -> list[dict]:
    roster = []
    for i in range(12):
        skill = rng.gauss(0.55, 0.22)
        roster.append({"player_id": f"{team}_F{i+1}", "team_id": team,
                        "full_name": f"{team} Forward {i+1}", "position": "F", "skill": max(skill, 0.05)})
    for i in range(6):
        skill = rng.gauss(0.30, 0.14)
        roster.append({"player_id": f"{team}_D{i+1}", "team_id": team,
                        "full_name": f"{team} Defense {i+1}", "position": "D", "skill": max(skill, 0.02)})
    for i in range(2):
        sv_delta = rng.gauss(0.0, 0.012) if i == 0 else rng.gauss(-0.010, 0.012)
        roster.append({"player_id": f"{team}_G{i+1}", "team_id": team,
                        "full_name": f"{team} Goalie {i+1}", "position": "G", "sv_delta": sv_delta})
    return roster


def _schedule_games(rng: random.Random, teams: list[str], season_start: dt.date
                     ) -> list[tuple[str, str, dt.date]]:
    """Greedy day-by-day scheduling: each day, pack as many non-conflicting
    pairings as fit without any team appearing twice that day. Guarantees
    the one-game-per-team-per-day invariant by construction."""
    pairings = []
    for _ in range(GAMES_PER_PAIRING):
        for a in teams:
            for b in teams:
                if a != b:
                    pairings.append((a, b))
    rng.shuffle(pairings)

    scheduled = []
    remaining = list(pairings)
    day = season_start
    safety_days = 0
    while remaining and safety_days < 2000:
        used_today: set[str] = set()
        i = 0
        while i < len(remaining):
            h, a = remaining[i]
            if h not in used_today and a not in used_today:
                used_today.add(h)
                used_today.add(a)
                scheduled.append((h, a, day))
                remaining.pop(i)
            else:
                i += 1
        day += dt.timedelta(days=1)
        safety_days += 1
    return scheduled


def generate(conn, seasons: list[tuple[str, dt.date]], seed: int = 42,
             upcoming_scheduled_games: int = 6) -> None:
    """seasons: list of (season_label, season_start_date).
    upcoming_scheduled_games: how many SCHEDULED (not-yet-played) games to
    append after the last season, so the pipeline has real "tonight's
    slate" data to price (rest features, DATA_UNAVAILABLE/WAIT paths)."""
    rng = random.Random(seed)
    now_placeholder = None  # never use wall-clock inside a deterministic generator

    true_strength = {t: rng.gauss(1500, 110) for t in TEAMS}
    rosters = {t: _make_roster(rng, t) for t in TEAMS}

    for t in TEAMS:
        conn.execute("INSERT OR IGNORE INTO teams (team_id, full_name) VALUES (?,?)", (t, t))
        for p in rosters[t]:
            conn.execute("INSERT OR IGNORE INTO players (player_id, full_name, position) VALUES (?,?,?)",
                         (p["player_id"], p["full_name"], p["position"]))

    # Initial (and, in this pass, only) team membership: known from before
    # the first season starts. Trades are supported by the schema and
    # exercised in tests/test_temporal_integrity.py with a hand-built
    # scenario; this generator doesn't simulate any this pass.
    first_season_start = seasons[0][1]
    membership_known_at = dt.datetime.combine(
        first_season_start - dt.timedelta(days=60), dt.time(12, 0)).isoformat()
    for t in TEAMS:
        for p in rosters[t]:
            conn.execute(
                """INSERT INTO team_membership_events
                   (player_id, team_id, effective_at_utc, observed_at_utc, event_type, source)
                   VALUES (?,?,?,?,?,?)""",
                (p["player_id"], t, membership_known_at, membership_known_at,
                 "INITIAL_ROSTER", "demo_generator"),
            )

    # per-player availability state machine (fixes the recur-after-recovery bug)
    player_status: dict[str, str] = {p["player_id"]: "ACTIVE"
                                      for t in TEAMS for p in rosters[t] if p["position"] != "G"}
    out_until: dict[str, dt.date] = {}

    game_id = 1_000_000
    last_played: dict[str, dt.date] = {}
    all_final_game_rows: list[tuple] = []   # for post-loop true-prob bookkeeping (odds gen)

    def schedule_time(game_date: dt.date) -> dt.datetime:
        return dt.datetime.combine(game_date, dt.time(23, 30))  # ~7:30pm ET in UTC, approx

    all_seasons_schedules = []
    for season_label, season_start in seasons:
        sched = _schedule_games(rng, TEAMS, season_start)
        sched.sort(key=lambda x: x[2])
        all_seasons_schedules.append((season_label, season_start, sched))

    for season_label, season_start, schedule in all_seasons_schedules:
        for t in TEAMS:
            true_strength[t] += rng.gauss(0, 25)   # mild season-to-season drift

        schedule_published_at = dt.datetime.combine(
            season_start - dt.timedelta(days=60), dt.time(12, 0)).isoformat()

        for home, away, game_date in schedule:
            game_id += 1
            start_dt = schedule_time(game_date)

            # --- roster status: recover if due, then independently roll a
            # fresh injury only for players currently ACTIVE (fixes the bug) ---
            for team in (home, away):
                for p in rosters[team]:
                    if p["position"] == "G":
                        continue
                    pid = p["player_id"]
                    if player_status[pid] == "OUT" and out_until.get(pid) is not None \
                            and game_date >= out_until[pid]:
                        recovered_at = dt.datetime.combine(out_until[pid], dt.time(9, 0)).isoformat()
                        conn.execute(
                            """INSERT INTO roster_status_events
                               (player_id, team_id, status, effective_at_utc, observed_at_utc,
                                expected_return_at, confidence, source)
                               VALUES (?,?,?,?,?,?,?,?)""",
                            (pid, team, "ACTIVE", recovered_at, recovered_at, None, 1.0,
                             "demo_generator"),
                        )
                        player_status[pid] = "ACTIVE"
                        out_until[pid] = None
                    if player_status[pid] == "ACTIVE" and rng.random() < 0.006:
                        duration = rng.randint(3, 14)
                        reported_at = dt.datetime.combine(game_date, dt.time(9, 0)).isoformat()
                        return_date = game_date + dt.timedelta(days=duration)
                        conn.execute(
                            """INSERT INTO roster_status_events
                               (player_id, team_id, status, effective_at_utc, observed_at_utc,
                                expected_return_at, confidence, source)
                               VALUES (?,?,?,?,?,?,?,?)""",
                            (pid, team, "OUT", reported_at, reported_at,
                             return_date.isoformat(), 0.9, "demo_generator"),
                        )
                        player_status[pid] = "OUT"
                        out_until[pid] = return_date

            available = {
                team: [p for p in rosters[team] if p["position"] != "G"
                       and player_status[p["player_id"]] == "ACTIVE"]
                for team in (home, away)
            }

            # --- rest context (for the SIMULATION only — the model derives
            # its own rest features independently via point_in_time.py) ---
            ctx = {}
            for team in (home, away):
                prev = last_played.get(team)
                rest_days = (game_date - prev).days if prev else 5
                ctx[team] = dict(back_to_back=1 if rest_days <= 1 else 0)
                last_played[team] = game_date

            # --- goalie: pick the actual starter, then emit an EXPECTED
            # status ~1 day ahead (sometimes wrong) and a CONFIRMED status
            # ~90 minutes before puck drop (always right, matching the
            # actual box score) ---
            starters = {}
            for team in (home, away):
                g_roster = [p for p in rosters[team] if p["position"] == "G"]
                use_backup = ctx[team]["back_to_back"] or rng.random() < 0.35
                starters[team] = g_roster[1] if use_backup else g_roster[0]

                expected_guess = starters[team]
                if rng.random() < 0.20:
                    expected_guess = g_roster[1] if expected_guess is g_roster[0] else g_roster[0]
                expected_at = (start_dt - dt.timedelta(days=1)).isoformat()
                conn.execute(
                    """INSERT INTO goalie_status_events
                       (game_id, team_id, player_id, status, effective_at_utc, observed_at_utc, source)
                       VALUES (?,?,?,?,?,?,?)""",
                    (game_id, team, expected_guess["player_id"], "EXPECTED",
                     expected_at, expected_at, "demo_generator"),
                )
                confirmed_at = (start_dt - dt.timedelta(minutes=90)).isoformat()
                conn.execute(
                    """INSERT INTO goalie_status_events
                       (game_id, team_id, player_id, status, effective_at_utc, observed_at_utc, source)
                       VALUES (?,?,?,?,?,?,?)""",
                    (game_id, team, starters[team]["player_id"], "CONFIRMED",
                     confirmed_at, confirmed_at, "demo_generator"),
                )

            # --- simulate result from hidden true strength + goalie/injury effects ---
            def team_effective_strength(team):
                base = true_strength[team]
                full_roster_skill = sum(p["skill"] for p in rosters[team] if p["position"] != "G")
                avail_skill = sum(p["skill"] for p in available[team])
                base += (avail_skill - full_roster_skill) * 60.0
                base += starters[team]["sv_delta"] * 3000.0
                if ctx[team]["back_to_back"]:
                    base -= 15.0
                return base

            home_eff = team_effective_strength(home) + 35.0
            away_eff = team_effective_strength(away)
            p_home_true = 1.0 / (1.0 + 10 ** (-(home_eff - away_eff) / 400.0))

            home_win = rng.random() < p_home_true
            went_ot = rng.random() < 0.23
            if went_ot:
                went_so = rng.random() < 0.45
                final_type = "SO" if went_so else "OT"
                base = rng.randint(2, 4)   # single draw, then apply the winning goal —
                if home_win:               # two independent draws could otherwise tie
                    home_score, away_score = base + 1, base
                else:
                    home_score, away_score = base, base + 1
            else:
                final_type = "REG"
                margin = max(1, round(rng.gauss(2.0, 1.1)))
                loser = rng.randint(1, 3)
                if home_win:
                    home_score, away_score = loser + margin, loser
                else:
                    home_score, away_score = loser, loser + margin
            assert home_score != away_score, "OT/SO/REG games must always have exactly one winner"

            result_observed_at = (start_dt + dt.timedelta(hours=2, minutes=30)).isoformat()
            conn.execute(
                """INSERT INTO games (game_id, season, game_date, scheduled_start_utc, home_team,
                                       away_team, venue, schedule_observed_at_utc, game_state,
                                       home_score, away_score, final_period_type,
                                       result_observed_at_utc, source)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (game_id, season_label, game_date.isoformat(), start_dt.isoformat(), home, away,
                 f"{home} Arena", schedule_published_at, "FINAL",
                 home_score, away_score, final_type, result_observed_at, "demo_generator"),
            )
            # v2.1: the append-only schedule-history row -- authoritative
            # source for features.point_in_time.game_schedule_as_of(). The
            # `games` row above is only a latest-known convenience cache.
            conn.execute(
                """INSERT INTO game_schedule_events
                   (game_id, game_date, scheduled_start_utc, home_team, away_team, venue,
                    effective_at_utc, observed_at_utc, source, data_provider)
                   VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (game_id, game_date.isoformat(), start_dt.isoformat(), home, away, f"{home} Arena",
                 schedule_published_at, schedule_published_at, "demo_generator", "demo_generator"),
            )
            # v2.1.1: the append-only result-history row -- authoritative
            # source for features.point_in_time.game_result_as_of() /
            # completed_games_known_before(). The `games` row above is
            # only a current-state convenience cache; this generator only
            # ever produces revision 1 (observed the moment the result
            # itself was observed) -- tests/test_result_revision.py
            # hand-inserts a revision 2 to prove a later correction can't
            # leak backward.
            conn.execute(
                """INSERT INTO game_result_events
                   (game_id, home_score, away_score, final_period_type, game_state,
                    effective_at_utc, observed_at_utc, revision_number, source, data_provider)
                   VALUES (?,?,?,?,'FINAL',?,?,1,?,?)""",
                (game_id, home_score, away_score, final_type,
                 result_observed_at, result_observed_at, "demo_generator", "demo_generator"),
            )
            all_final_game_rows.append((game_id, home, away, p_home_true, start_dt))

            # --- lineup snapshots: who actually dressed, confirmed pregame ---
            lineup_confirmed_at = (start_dt - dt.timedelta(hours=2)).isoformat()
            for team in (home, away):
                for p in available[team]:
                    conn.execute(
                        """INSERT INTO lineup_snapshots
                           (game_id, team_id, player_id, role, status, effective_at_utc,
                            observed_at_utc, source)
                           VALUES (?,?,?,?,?,?,?,?)""",
                        (game_id, team, p["player_id"], "DRESSED", "CONFIRMED",
                         lineup_confirmed_at, lineup_confirmed_at, "demo_generator"),
                    )

            # --- postgame box score (never read pregame — see schema.sql note) ---
            for team, goals in ((home, home_score), (away, away_score)):
                skaters = available[team] or [p for p in rosters[team] if p["position"] != "G"]
                weights = [p["skill"] for p in skaters]
                total_w = sum(weights) or 1.0
                for p in skaters:
                    share = p["skill"] / total_w
                    p_goals = sum(1 for _ in range(goals) if rng.random() < share * 1.3)
                    p_shots = max(p_goals, round(rng.gauss(share * 22, 1.2)))
                    p_assists = sum(1 for _ in range(goals) if rng.random() < share * 1.1)
                    # v2.1: revision_number/effective_at_utc/observed_at_utc
                    # -- this generator only ever produces revision 1
                    # (observed the moment the result itself was observed);
                    # tests/test_stat_revision.py hand-inserts a revision 2
                    # to prove a later correction can't leak backward.
                    conn.execute(
                        """INSERT INTO player_game_stats
                           (game_id, player_id, team_id, toi_minutes, goals, assists, shots, played,
                            revision_number, effective_at_utc, observed_at_utc, source)
                           VALUES (?,?,?,?,?,?,?,1,1,?,?,?)""",
                        (game_id, p["player_id"], team,
                         round(rng.gauss(15 if p["position"] == "F" else 19, 3), 1),
                         p_goals, p_assists, max(p_shots, 0),
                         result_observed_at, result_observed_at, "demo_generator"),
                    )
                for p in rosters[team]:
                    if p["position"] != "G" and p not in skaters:
                        conn.execute(
                            """INSERT INTO player_game_stats
                               (game_id, player_id, team_id, toi_minutes, goals, assists, shots,
                                played, revision_number, effective_at_utc, observed_at_utc, source)
                               VALUES (?,?,?,0,0,0,0,0,1,?,?,?)""",
                            (game_id, p["player_id"], team,
                             result_observed_at, result_observed_at, "demo_generator"),
                        )

            for team, opp_score in ((home, away_score), (away, home_score)):
                g = starters[team]
                shots_against = max(opp_score + rng.randint(18, 32), opp_score)
                saves = shots_against - opp_score
                conn.execute(
                    """INSERT INTO goalie_game_stats
                       (game_id, player_id, team_id, started, shots_against, saves, goals_against,
                        revision_number, effective_at_utc, observed_at_utc, source)
                       VALUES (?,?,?,1,?,?,?,1,?,?,?)""",
                    (game_id, g["player_id"], team, shots_against, saves, opp_score,
                     result_observed_at, result_observed_at, "demo_generator"),
                )

    conn.commit()
    _generate_odds(conn, rng, all_final_game_rows)

    # --- a handful of not-yet-played SCHEDULED games after the data set,
    # so the pipeline has real "tonight's slate" data to exercise ---
    if upcoming_scheduled_games > 0:
        last_season_label, _, last_schedule = all_seasons_schedules[-1]
        last_date = max(d for _, _, d in last_schedule)
        upcoming_start = last_date + dt.timedelta(days=3)
        upcoming_pairs = rng.sample([(a, b) for a in TEAMS for b in TEAMS if a != b],
                                     upcoming_scheduled_games)
        schedule_published_at = dt.datetime.combine(
            upcoming_start - dt.timedelta(days=30), dt.time(12, 0)).isoformat()
        for i, (home, away) in enumerate(upcoming_pairs):
            game_id += 1
            game_date = upcoming_start + dt.timedelta(days=i // 2)
            start_dt = schedule_time(game_date)
            conn.execute(
                """INSERT INTO games (game_id, season, game_date, scheduled_start_utc, home_team,
                                       away_team, venue, schedule_observed_at_utc, game_state,
                                       source)
                   VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (game_id, last_season_label, game_date.isoformat(), start_dt.isoformat(),
                 home, away, f"{home} Arena", schedule_published_at, "SCHEDULED", "demo_generator"),
            )
            conn.execute(
                """INSERT INTO game_schedule_events
                   (game_id, game_date, scheduled_start_utc, home_team, away_team, venue,
                    effective_at_utc, observed_at_utc, source, data_provider)
                   VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (game_id, game_date.isoformat(), start_dt.isoformat(), home, away, f"{home} Arena",
                 schedule_published_at, schedule_published_at, "demo_generator", "demo_generator"),
            )
            # partial pregame info: an EXPECTED goalie a day out, nothing confirmed yet,
            # and one DraftKings snapshot from this morning — deliberately incomplete,
            # to exercise the WAIT / DATA_UNAVAILABLE paths.
            for team in (home, away):
                g_roster = [p for p in rosters[team] if p["position"] == "G"]
                expected_at = (start_dt - dt.timedelta(days=1)).isoformat()
                conn.execute(
                    """INSERT INTO goalie_status_events
                       (game_id, team_id, player_id, status, effective_at_utc, observed_at_utc, source)
                       VALUES (?,?,?,?,?,?,?)""",
                    (game_id, team, g_roster[0]["player_id"], "EXPECTED",
                     expected_at, expected_at, "demo_generator"),
                )
            _insert_dk_snapshot(conn, game_id, home, away, 0.55, start_dt,
                                 start_dt - dt.timedelta(hours=20), "OVERNIGHT", rng)
    conn.commit()


def _generate_odds(conn, rng: random.Random, all_final_game_rows: list[tuple]) -> None:
    """DraftKings-labeled synthetic odds across a pregame timeline for
    every FINAL game, with realistic small line movement and an
    occasional missing/suspended snapshot so the DATA_UNAVAILABLE /
    staleness paths have something real to reject."""
    for game_id, home, away, p_home_true, start_dt in all_final_game_rows:
        if rng.random() < 0.03:
            continue   # ~3% of games: no DraftKings data at all

        labels_offsets = [
            ("OPEN", dt.timedelta(hours=26)),
            ("OVERNIGHT", dt.timedelta(hours=14)),
            ("MORNING", dt.timedelta(hours=8)),
            ("T-60", dt.timedelta(minutes=60)),
            # v2.1: fresh enough (5 min before the default prediction
            # anchor of "30 min before puck drop") to clear the new
            # DYNAMIC odds-staleness policy's tightest relevant tier
            # (10 min allowed at that horizon) -- without this, the
            # default demo/backtest prediction anchor would see every
            # game as DATA_UNAVAILABLE once staleness stopped being a
            # flat 180-minute window. T-60 above is kept too, so the
            # freshness enforcement is visible (T-60 correctly falls out).
            ("T-35", dt.timedelta(minutes=35)),
            ("CLOSE", dt.timedelta(minutes=5)),
        ]
        drift = rng.gauss(0, 0.01)
        suspend_close = rng.random() < 0.03
        for label, offset in labels_offsets:
            noisy_p = min(max(p_home_true + drift + rng.gauss(0, 0.02), 0.05), 0.95)
            drift += rng.gauss(0, 0.006)
            status = "SUSPENDED" if (label == "CLOSE" and suspend_close) else "ACTIVE"
            _insert_dk_snapshot(conn, game_id, home, away, noisy_p, start_dt,
                                 start_dt - offset, label, rng, status=status)


def _insert_dk_snapshot(conn, game_id, home, away, home_prob, start_dt, captured_dt, label,
                         rng: random.Random, status: str = "ACTIVE") -> None:
    from pricing import odds_math

    vig = 0.045
    home_price = odds_math.prob_to_american(min(home_prob + vig / 2, 0.97))
    away_price = odds_math.prob_to_american(min((1 - home_prob) + vig / 2, 0.97))
    received_dt = captured_dt + dt.timedelta(minutes=2)
    for selection, price in ((home, home_price), (away, away_price)):
        conn.execute(
            """INSERT OR IGNORE INTO odds_snapshots
               (game_id, sportsbook, data_provider, market, selection, event_start_utc, line,
                price_american, status, captured_at_utc, received_at_utc, snapshot_label)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (game_id, "DraftKings", "demo_generator", "MONEYLINE", selection,
             start_dt.isoformat(), None, price, status,
             captured_dt.isoformat(), received_dt.isoformat(), label),
        )
