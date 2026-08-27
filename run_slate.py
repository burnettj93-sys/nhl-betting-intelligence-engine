"""
Prices real "slate" games against the DraftKings snapshots stored in the
DB — no ad hoc synthetic prices generated at call time (that was the old
version's shortcut; every price here now comes from odds_snapshots).

Usage:
    python3 run_slate.py                 # demo: a few recently-known
                                          # historical games + the upcoming
                                          # SCHEDULED slate
    python3 run_slate.py GAME_ID         # price one specific game_id at its
                                          # default prediction time (30 min
                                          # before scheduled puck drop)

v2.1.1 (spec item 1): this file used to determine training eligibility with
`[g for g in all_final if g < gid]` and `all_final[:-5]` — a game-ID
comparison and a list-position split, both explicitly prohibited proxies
for "what a historical model is allowed to know" (neither survives a
rescheduled or late-finishing game). Every game priced below now gets its
OWN independently-reconstructed model state via
models.combined_model.build_model_state_as_of(), keyed strictly on that
game's own prediction_time_utc — never on a frozen pre-built model shared
across games, and never on where a game_id falls in a list. Because
build_model_state_as_of() walks features.point_in_time.
completed_games_known_before() (itself ordered by when each result was
first observed, never by game_id), a later held-out game's reconstruction
automatically includes an earlier held-out game's result if — and only
if — that earlier result had genuinely been observed by the later game's
own prediction_time_utc. See tests/test_run_slate_temporal.py.
"""
import sys

import db
from models.combined_model import CombinedMoneylineModel, build_model_state_as_of
from pricing import decision as decision_mod
from pricing import engine as pricing_engine


def build_prediction_for_game(conn, game_id, teams=None):
    """THE sanctioned way this script prices a historical (or upcoming)
    game: reconstruct model state fresh, scoped exactly to this game's own
    prediction_time_utc, via build_model_state_as_of() — never by manually
    assembling a training-game list (no game_id comparison, no list
    slicing). Returns the resulting GamePrediction; callers that also want
    to price/print it should call price_and_print() below, which calls
    this internally.

    v2.1.2 (spec item 2): `teams` defaults to db.team_ids(conn) — the
    real, DB-derived team universe — never ingest.demo_data.TEAMS (the
    synthetic demo league). This is what lets this production pricing
    path operate correctly against a real NHL database containing teams
    the demo world never had. See tests/test_dynamic_team_universe.py."""
    if teams is None:
        teams = db.team_ids(conn)
    prediction_time = CombinedMoneylineModel.prediction_time_for_game(conn, game_id)
    model = build_model_state_as_of(conn, prediction_time, teams)
    return model.predict(conn, game_id, prediction_time)


def price_and_print(conn, game_id, teams=None):
    pred = build_prediction_for_game(conn, game_id, teams)
    label = (f"{pred.away_team} @ {pred.home_team} ({pred.game_date[:10]}) — "
             f"priced at {pred.prediction_time_utc}")
    reports = pricing_engine.evaluate_moneyline_for_game(conn, pred, label)
    for report in reports:
        print(report.format())
        print("-" * 60)
        decision_mod.persist_full_decision(conn, pred, report)
    if pred.home_score is not None:
        print(f"(actual result: {pred.away_team} {pred.away_score} @ "
              f"{pred.home_team} {pred.home_score})")
    print("=" * 60)


if __name__ == "__main__":
    conn = db.get_conn()

    if len(sys.argv) == 2:
        gid = int(sys.argv[1])
        price_and_print(conn, gid)
    else:
        # `all_final_game_ids()` is used ONLY to pick which games to show
        # in this demo printout — it is never used to gate what any model
        # instance is allowed to learn (that happens independently, per
        # game, inside build_prediction_for_game() above). Selecting "the
        # 5 most-recently-known" games for display is not a temporal
        # decision: each one's own pricing below still independently and
        # correctly reconstructs state as of its own prediction_time_utc.
        all_final = CombinedMoneylineModel.all_final_game_ids(conn)
        demo_display_ids = all_final[-5:]

        print("HELD-OUT HISTORICAL GAMES (each priced 30 min before its own puck")
        print("drop, from model state independently reconstructed as of that exact")
        print("moment — an earlier one of these games' results is included only if")
        print("it was genuinely known by the later one's own prediction time):\n")
        for gid in demo_display_ids:
            price_and_print(conn, gid)

        print("\nUPCOMING SCHEDULED SLATE (real not-yet-played games — mostly WAIT/")
        print("DATA_UNAVAILABLE is CORRECT here: this far out, goalies aren't")
        print("confirmed and DraftKings hasn't necessarily posted a line yet):\n")
        upcoming = [r["game_id"] for r in conn.execute(
            "SELECT game_id FROM games WHERE game_state='SCHEDULED' ORDER BY game_date, game_id"
        )]
        for gid in upcoming:
            price_and_print(conn, gid)
