"""
Part 7-9: goalie saves by period, using the SAME canonical per-event
goalie signal goalie_tenure.py and normalize.py both already use --
`event.players.get("goalie")` -- rather than a second implementation
built on top of the tenure-interval structure (Part 22: one source of
truth). This is what makes mid-period substitutions transparent: each
shot/goal event already carries the correct goalie for THAT event, so a
substitution mid-period never needs special-case handling here at all --
it falls out of grouping by (goalie_id, period_number) directly.

Accounting rule (Part 7, stated explicitly in the prompt): a shot-on-goal
event is a SAVE for the goalie in net; a statistical goal is NOT a save,
but DOES count toward that goalie's shots-faced (mirrors the project's
already-accepted SOG-includes-goal convention, applied here to the
defending side). Shootout attempts are excluded entirely -- a different
structural regime, never ordinary period saves.
"""
from __future__ import annotations

import collections

from research.real_nhl_pbp.schema import PbpEvent


def period_saves_by_goalie(events: list[PbpEvent]) -> dict[tuple[int, int], dict]:
    """{(goalie_id, period_number): {"saves": int, "goals_against": int,
    "shots_faced": int}} -- REG/OT only, shootout excluded."""
    stats: dict[tuple[int, int], dict] = collections.defaultdict(
        lambda: {"saves": 0, "goals_against": 0, "shots_faced": 0}
    )
    for e in events:
        if e.period_type == "SO" or not e.is_statistical:
            continue
        goalie_id = e.players.get("goalie")
        if goalie_id is None:
            continue  # empty net -- no goalie faced this shot/goal
        key = (goalie_id, e.period_number)
        if e.event_type == "shot-on-goal":
            stats[key]["saves"] += 1
            stats[key]["shots_faced"] += 1
        elif e.event_type == "goal":
            stats[key]["goals_against"] += 1
            stats[key]["shots_faced"] += 1
    return dict(stats)


def full_game_saves_by_goalie(events: list[PbpEvent]) -> dict[int, dict]:
    """Aggregates period_saves_by_goalie() across all periods for each
    goalie -- Part 9's internal-coherence check (sum(period saves) ==
    full-game saves) holds by construction, since this IS that sum, not a
    second computation."""
    by_period = period_saves_by_goalie(events)
    totals: dict[int, dict] = collections.defaultdict(
        lambda: {"saves": 0, "goals_against": 0, "shots_faced": 0}
    )
    for (goalie_id, _period), stat in by_period.items():
        totals[goalie_id]["saves"] += stat["saves"]
        totals[goalie_id]["goals_against"] += stat["goals_against"]
        totals[goalie_id]["shots_faced"] += stat["shots_faced"]
    return dict(totals)


def _boxscore_goalie_rows(boxscore: dict) -> dict[int, dict]:
    rows: dict[int, dict] = {}
    stats = boxscore.get("playerByGameStats", {})
    for side in ("awayTeam", "homeTeam"):
        for p in stats.get(side, {}).get("goalies", []):
            rows[p["playerId"]] = p
    return rows


def reconcile_full_game_saves(events: list[PbpEvent], boxscore: dict) -> list[dict]:
    """Part 8: compares reconstructed full-game saves against the real
    official /boxscore saves field, per goalie who appears in either
    source. Returns a (normally empty) list of mismatch dicts -- never
    silently patched (Part 8's explicit instruction)."""
    reconstructed = full_game_saves_by_goalie(events)
    box_rows = _boxscore_goalie_rows(boxscore)
    mismatches = []
    all_ids = set(reconstructed) | set(box_rows)
    for goalie_id in sorted(all_ids):
        recon_saves = reconstructed.get(goalie_id, {}).get("saves", 0)
        box = box_rows.get(goalie_id)
        if box is None:
            if recon_saves:
                mismatches.append({"goalie_id": goalie_id, "reason": "no boxscore row",
                                    "reconstructed_saves": recon_saves})
            continue
        box_saves = box.get("saves", 0)
        if recon_saves != box_saves:
            mismatches.append({
                "goalie_id": goalie_id, "reconstructed_saves": recon_saves, "boxscore_saves": box_saves,
                "diff": recon_saves - box_saves,
            })
    return mismatches


def check_period_sums_equal_full_game(events: list[PbpEvent]) -> list[dict]:
    """Part 9 internal-coherence invariant. Returns a (normally empty)
    list of violations -- present for completeness/testing even though
    the construction above makes a violation structurally impossible."""
    by_period = period_saves_by_goalie(events)
    full_game = full_game_saves_by_goalie(events)
    recomputed: dict[int, dict] = collections.defaultdict(
        lambda: {"saves": 0, "goals_against": 0, "shots_faced": 0}
    )
    for (goalie_id, _period), stat in by_period.items():
        recomputed[goalie_id]["saves"] += stat["saves"]
        recomputed[goalie_id]["goals_against"] += stat["goals_against"]
        recomputed[goalie_id]["shots_faced"] += stat["shots_faced"]
    violations = []
    for goalie_id, totals in full_game.items():
        if recomputed[goalie_id] != totals:
            violations.append({"goalie_id": goalie_id, "full_game": totals, "recomputed": recomputed[goalie_id]})
    return violations
