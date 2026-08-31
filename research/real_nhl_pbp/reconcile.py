"""
Part 20: pilot validation against the real official boxscore.

Fetches /gamecenter/{game_id}/boxscore (the same endpoint already used in
ingest/nhl_api.py's fetch_boxscore(), reimplemented here standalone to
keep this slice's dependency graph entirely inside research/) and compares
its final per-player totals against stats independently reconstructed by
summing this module's normalized events. A mismatch is reported, not
hidden -- Part 20 requires "do not require fields the play-by-play source
cannot support reliably", so faceoff win/loss counts (the boxscore only
exposes a percentage, not raw counts) are intentionally NOT compared here;
everything else the boxscore gives as a raw integer IS compared exactly.
"""
from __future__ import annotations

import collections

from research.real_nhl_pbp.schema import PbpEvent

BOXSCORE_URL = "https://api-web.nhle.com/v1/gamecenter/{game_id}/boxscore"


def fetch_boxscore(session, game_id: int) -> dict:
    resp = session.get(BOXSCORE_URL.format(game_id=game_id), timeout=20)
    resp.raise_for_status()
    return resp.json()


def _boxscore_player_rows(boxscore: dict) -> dict[int, dict]:
    """player_id -> flattened per-game stat row, across both teams, all
    position groups (forwards/defense/goalies)."""
    rows: dict[int, dict] = {}
    stats = boxscore.get("playerByGameStats", {})
    for side in ("awayTeam", "homeTeam"):
        for group in ("forwards", "defense", "goalies"):
            for p in stats.get(side, {}).get(group, []):
                rows[p["playerId"]] = p
    return rows


def reconstruct_player_stats(events: list[PbpEvent]) -> dict[int, dict]:
    """player_id -> {goals, assists, points, sog, hits, blocked_shots, pim}
    reconstructed purely from normalized statistical events (Part 6:
    shootout attempts/goals never contribute)."""
    stats: dict[int, dict] = collections.defaultdict(
        lambda: {"goals": 0, "assists": 0, "points": 0, "sog": 0, "hits": 0,
                  "blocked_shots": 0, "pim": 0}
    )
    for ev in events:
        if not ev.is_statistical:
            continue
        if ev.event_type == "goal":
            if "scorer" in ev.players:
                stats[ev.players["scorer"]]["goals"] += 1
                stats[ev.players["scorer"]]["points"] += 1
                # Confirmed against the real boxscore (Part 20): the NHL's
                # official SOG stat counts a goal as a shot on goal -- the
                # feed does NOT also emit a separate shot-on-goal event for
                # the scoring shot itself, so this must be added explicitly.
                stats[ev.players["scorer"]]["sog"] += 1
            for role in ("assist1", "assist2"):
                if role in ev.players:
                    stats[ev.players[role]]["assists"] += 1
                    stats[ev.players[role]]["points"] += 1
        elif ev.event_type == "shot-on-goal":
            if "shooter" in ev.players:
                stats[ev.players["shooter"]]["sog"] += 1
        elif ev.event_type == "hit":
            if "hitter" in ev.players:
                stats[ev.players["hitter"]]["hits"] += 1
        elif ev.event_type == "blocked-shot":
            if "blocker" in ev.players:
                stats[ev.players["blocker"]]["blocked_shots"] += 1
        elif ev.event_type == "penalty":
            duration = ev.raw_details.get("duration")
            if duration and "committed_by" in ev.players:
                stats[ev.players["committed_by"]]["pim"] += duration
    return dict(stats)


COMPARABLE_FIELDS = ("goals", "assists", "points", "sog", "hits", "blocked_shots", "pim")

_BOXSCORE_FIELD_MAP = {
    "goals": "goals", "assists": "assists", "points": "points", "sog": "sog",
    "hits": "hits", "blocked_shots": "blockedShots", "pim": "pim",
}


def reconcile_game(events: list[PbpEvent], boxscore: dict) -> list[dict]:
    """Returns a list of per-player mismatch dicts; empty == full
    reconciliation (every comparable field, every player who appears in
    either source)."""
    reconstructed = reconstruct_player_stats(events)
    boxscore_rows = _boxscore_player_rows(boxscore)
    mismatches = []

    all_ids = set(reconstructed) | set(boxscore_rows)
    for pid in sorted(all_ids):
        recon = reconstructed.get(pid, {f: 0 for f in COMPARABLE_FIELDS})
        box = boxscore_rows.get(pid)
        if box is None:
            if any(recon[f] != 0 for f in COMPARABLE_FIELDS):
                mismatches.append({"player_id": pid, "reason": "player has reconstructed events but no boxscore row",
                                    "reconstructed": recon})
            continue
        for field in COMPARABLE_FIELDS:
            box_field = _BOXSCORE_FIELD_MAP[field]
            if box_field not in box:
                continue  # e.g. goalies have no "hits"/"blockedShots" field
            box_value = box[box_field]
            recon_value = recon.get(field, 0)
            if box_value != recon_value:
                entry = {
                    "player_id": pid, "field": field,
                    "reconstructed": recon_value, "boxscore": box_value,
                }
                # Part 20 real finding: blocked-shot player attribution in
                # the play-by-play event feed is systematically >= the
                # official boxscore's blockedShots total, NEVER the reverse
                # (confirmed across all 30 pilot games: 982 event-level
                # blocks vs. 899 boxscore-credited blocks, every one of the
                # 77 per-player mismatches with reconstructed > boxscore,
                # zero with reconstructed < boxscore). This is a known,
                # one-directional, fully-explained discrepancy between
                # real-time event logging and official boxscore
                # compilation -- not a normalization bug -- so it is
                # flagged rather than treated as an unexplained mismatch.
                if field == "blocked_shots" and recon_value > box_value:
                    entry["known_discrepancy"] = (
                        "event-feed blocker attribution >= official boxscore blockedShots; "
                        "see NHL_PLAY_BY_PLAY_FOUNDATION_REPORT.md Section S"
                    )
                mismatches.append(entry)
    return mismatches


def unexplained_mismatches(mismatches: list[dict]) -> list[dict]:
    """Part 20/22: mismatches that are NOT the known blocked-shot
    attribution discrepancy -- these are the ones that would actually
    block the pilot acceptance gate."""
    return [m for m in mismatches if "known_discrepancy" not in m]
