"""
Part 4/5: manpower-state parsing from the NHL's real `situationCode` field.

situationCode is a 4-character string: [awayGoalieInNet, awaySkaters,
homeSkaters, homeGoalieInNet] (each a single digit) -- confirmed already
in research/real_nhl_pbp/normalize.py::is_empty_net_context's own
docstring, reused here as the single source of truth for the format
rather than re-deriving it.

A team dressing 6 skaters is only physically possible with its own
goalie pulled (max 6 players total on ice), so the skater-count digits
alone already determine every "extra attacker" state (6v5, 5v6, 6v4,
4v6) without needing the goalie digit -- the goalie digit is kept only
as an independent cross-check (Part 5's "malformed/unknown" detection):
a code claiming 6 skaters for a team AND that team's own goalie in net
simultaneously is a real, physically-impossible contradiction and is
classified MALFORMED rather than silently trusted.
"""
from __future__ import annotations

from collections import Counter

# Manpower states this project expects to see routinely (Part 4's named
# list) -- used only to separate "common, expected" from "rare" in the
# validation counts (Part 5); every valid (away, home) skater pair is
# still classified and labeled, never forced into this set.
COMMON_STATES = {
    "5v5", "5v4", "4v5", "5v3", "3v5", "4v4", "3v3",
    "6v5", "5v6", "6v4", "4v6",
}

MIN_VALID_SKATERS = 3   # NHL minimum on-ice skaters (two 2-minute minors max stacked to 3v5)
MAX_VALID_SKATERS = 6   # full strength with goalie pulled


def parse_situation_code(code: str | None) -> dict | None:
    """Returns {"away_goalie_in": bool, "away_skaters": int, "home_skaters":
    int, "home_goalie_in": bool}, or None if the code is missing/not
    exactly 4 digits (Part 5's "missingness" bucket)."""
    if code is None or len(code) != 4 or not code.isdigit():
        return None
    return {
        "away_goalie_in": code[0] == "1", "away_skaters": int(code[1]),
        "home_skaters": int(code[2]), "home_goalie_in": code[3] == "1",
    }


def classify_manpower_state(code: str | None) -> str:
    """Returns a canonical label:
      - "{away}v{home}" for any physically-consistent skater pair (e.g.
        "5v5", "5v4", "6v5") -- both COMMON_STATES and rare-but-valid
        combinations (e.g. "3v3" with a delayed extra attacker) get a
        real label, never forced into a bucket.
      - "MALFORMED" if the goalie digit contradicts the skater count
        (6 skaters implies that team's goalie must be pulled) or a
        skater count falls outside the physically valid 3-6 range.
      - "UNKNOWN" if situationCode is missing or not 4 digits at all.
    """
    parsed = parse_situation_code(code)
    if parsed is None:
        return "UNKNOWN"
    away_sk, home_sk = parsed["away_skaters"], parsed["home_skaters"]
    away_goalie, home_goalie = parsed["away_goalie_in"], parsed["home_goalie_in"]

    if not (MIN_VALID_SKATERS <= away_sk <= MAX_VALID_SKATERS):
        return "MALFORMED"
    if not (MIN_VALID_SKATERS <= home_sk <= MAX_VALID_SKATERS):
        return "MALFORMED"
    if away_sk == MAX_VALID_SKATERS and away_goalie:
        return "MALFORMED"   # 6 skaters + goalie in net is physically impossible
    if home_sk == MAX_VALID_SKATERS and home_goalie:
        return "MALFORMED"
    if away_sk < MAX_VALID_SKATERS and not away_goalie:
        return "MALFORMED"   # fewer than 6 skaters but goalie also pulled -- not a real state
    if home_sk < MAX_VALID_SKATERS and not home_goalie:
        return "MALFORMED"

    return f"{away_sk}v{home_sk}"


def is_empty_net_state(state: str) -> bool:
    """True for any state where either team is dressing 6 skaters (the
    only way to reach 6 is with that team's own goalie pulled)."""
    if state in ("UNKNOWN", "MALFORMED"):
        return False
    away_sk, home_sk = (int(x) for x in state.split("v"))
    return away_sk == MAX_VALID_SKATERS or home_sk == MAX_VALID_SKATERS


def is_even_strength(state: str) -> bool:
    return state in ("5v5", "4v4", "3v3")


def is_power_play_for_home(state: str) -> bool | None:
    """True if HOME has more skaters than AWAY (home on the power play),
    False if away has more, None for even-strength/empty-net/unknown/
    malformed states where "power play" isn't a meaningful label."""
    if state in ("UNKNOWN", "MALFORMED") or is_empty_net_state(state) or is_even_strength(state):
        return None
    away_sk, home_sk = (int(x) for x in state.split("v"))
    return home_sk > away_sk


def manpower_state_counts(codes: list[str | None]) -> dict[str, int]:
    """Part 5: raw counts of every classified state -- the caller decides
    how to bucket UNKNOWN/MALFORMED/rare from this, nothing is dropped
    silently."""
    return dict(Counter(classify_manpower_state(c) for c in codes))


def manpower_validation_summary(codes: list[str | None]) -> dict:
    """Part 5's required validation output: total count, missing count,
    malformed count, rare-state count (valid but outside COMMON_STATES),
    and the full per-state breakdown -- so "no unexplained large missing
    category" can be checked directly against real numbers."""
    counts = manpower_state_counts(codes)
    total = sum(counts.values())
    unknown = counts.get("UNKNOWN", 0)
    malformed = counts.get("MALFORMED", 0)
    rare = sum(n for state, n in counts.items()
               if state not in COMMON_STATES and state not in ("UNKNOWN", "MALFORMED"))
    return {
        "total_events": total,
        "unknown_count": unknown, "unknown_pct": unknown / total if total else None,
        "malformed_count": malformed, "malformed_pct": malformed / total if total else None,
        "rare_valid_count": rare, "rare_valid_pct": rare / total if total else None,
        "state_counts": counts,
    }
