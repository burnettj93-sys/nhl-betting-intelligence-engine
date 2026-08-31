"""
Parts 28-33: market data-readiness classification. NO MODEL BUILDING
happens here (Part 34) -- this module only classifies, for each market
named in the prompt, whether the play-by-play event foundation built in
this slice already carries the fields a future model would need.

Three levels, used consistently:
  READY   -- every field a derivation needs is confirmed present on the
             normalized event schema (schema.py / normalize.py), backed by
             a real finding from the 30-game pilot.
  PARTIAL -- the core fields exist but either (a) a real, non-trivial
             reconstruction step is still required (not yet built, per
             Part 34's explicit ban), or (b) an edge case was found that
             needs deliberate handling before the market can be trusted.
  NOT READY -- a required field/endpoint was checked for and not found
             within THIS slice's scope (play-by-play + boxscore only; the
             /landing endpoint was never queried).

Every entry's `evidence` string names the concrete pilot finding behind
the classification -- see NHL_PLAY_BY_PLAY_FOUNDATION_REPORT.md Sections
AD-AJ for the full narrative.

Multi-Season PBP Expansion slice: every verdict below was RE-CONFIRMED,
not re-derived, across a real contract-drift audit spanning 2022-23,
2023-24, and 2024-25 (see NHL_PBP_FOUR_SEASON_CORPUS_REPORT.md Section H)
-- every field these classifications depend on was found identical across
all four seasons, so no verdict changed. A handful of markets named
explicitly in that slice's prompt (Player Point by Period, Period Winning
Margin, Highest-Scoring Period, First Goal Method, Race to 1-4 enumerated,
Lead After Every Period) were added as new entries using the exact same
already-established fields -- none required new evidence.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class ReadinessEntry:
    market_label: str
    readiness: str  # READY / PARTIAL / NOT READY
    evidence: str


PERIOD_MARKET_READINESS = [
    ReadinessEntry("PLAYER GOAL BY PERIOD", "READY",
                   "goal events carry period_number and scorer directly (Part 5/16)."),
    ReadinessEntry("PLAYER SOG BY PERIOD", "READY",
                   "shot-on-goal events carry period_number and shooter directly; "
                   "a player's own goals also count toward SOG (confirmed via boxscore reconciliation)."),
    ReadinessEntry("TEAM GOALS BY PERIOD", "READY",
                   "sum of statistical goal events per team per period_number."),
    ReadinessEntry("GAME GOALS BY PERIOD", "READY", "sum of both teams' per-period goal totals."),
    ReadinessEntry("TEAM TO SCORE BY PERIOD", "READY",
                   "team_id of the first statistical goal event_sequence within a period."),
    ReadinessEntry("FIRST TEAM TO SCORE", "READY",
                   "team_id of the first statistical goal by event_sequence across the whole game."),
    ReadinessEntry("BOTH TEAMS SCORE BY PERIOD", "READY", "derived directly from TEAM GOALS BY PERIOD."),
    ReadinessEntry("CORRECT SCORE BY PERIOD", "READY",
                   "reconstruct_statistical_score() bucketed at each period boundary event_sequence."),
    ReadinessEntry("GOALIE SAVES BY PERIOD", "READY",
                   "Event-Timing Utility Closure slice: research/real_nhl_pbp/{goalie_tenure,period_saves}.py "
                   "correctly handle mid-period substitutions (387 real cases found across the 4-season "
                   "corpus) and empty-net pulls/returns (671 real return cases). Corpus-validated: 5,248/5,248 "
                   "(100%) exact full-game save matches against the official /boxscore, 0 period-to-full-game "
                   "coherence violations. See EVENT_TIMING_UTILITY_CLOSURE_REPORT.md Section G/H."),
    ReadinessEntry("PLAYER POINT BY PERIOD", "READY",
                   "sum of PLAYER GOAL BY PERIOD (scorer) and assist1/assist2 roles bucketed the same way."),
    ReadinessEntry("PERIOD WINNING MARGIN", "READY",
                   "difference between each team's TEAM GOALS BY PERIOD total for a given period."),
    ReadinessEntry("HIGHEST-SCORING PERIOD", "READY",
                   "argmax over GAME GOALS BY PERIOD across a game's periods."),
]

EVENT_TIME_MARKET_READINESS = [
    ReadinessEntry("FIRST GOAL SCORER", "READY", "scorer of the first statistical goal by event_sequence."),
    ReadinessEntry("LAST GOAL SCORER", "READY", "scorer of the last statistical goal by event_sequence."),
    ReadinessEntry("TEAM FIRST GOAL SCORER", "READY", "team_id of the first statistical goal."),
    ReadinessEntry("FIRST GOAL TIMING", "READY", "regulation_elapsed_seconds of the first statistical goal."),
    ReadinessEntry("GAME-WINNING GOAL", "READY",
                   "Event-Timing Utility Closure slice: research/real_nhl_pbp/gwg.py implements the exact "
                   "final-score-dependent definition and was corpus-validated with 0 invariant violations "
                   "across all 4,875 non-shootout games in the 4-season corpus (EVENT_TIMING_UTILITY_"
                   "CLOSURE_REPORT.md Section O). Shootout games correctly return no player GWG (373/373 "
                   "confirmed tied statistical score); a later empty-net goal was confirmed to never "
                   "falsely become the GWG on real corpus data."),
    ReadinessEntry("FIRST GOAL METHOD", "READY",
                   "goal events carry a real shotType field (wrist/slap/deflected/backhand/tip-in/...), "
                   "and EV/PP/SH/empty-net classification is READY via situationCode (Section AF) -- "
                   "'method' is answerable either as shot type or as manpower-state-at-goal, both present."),
    ReadinessEntry("TEAM TO SCORE FIRST", "READY", "same as FIRST TEAM TO SCORE."),
    ReadinessEntry("TEAM TO SCORE LAST", "READY", "team_id of the last statistical goal."),
    ReadinessEntry("RACE TO 1 / RACE TO 2 / RACE TO 3 / RACE TO 4", "READY",
                   "first event_sequence at which either team's running goal count reaches N, for any N."),
    ReadinessEntry("LEAD 1-0 / LEAD 2-0", "READY", "derived directly from the reconstructed score timeline."),
    ReadinessEntry("LARGEST LEAD", "READY", "max abs(home_score - away_score) across the score timeline."),
    ReadinessEntry("LEAD AFTER EVERY PERIOD", "READY",
                   "reconstructed score timeline read at each period-boundary event_sequence."),
    ReadinessEntry("COMEBACK WIN", "READY",
                   "detect a timeline point where the eventual winner trailed, from the same score timeline."),
]

SPECIAL_TEAMS_READINESS = [
    ReadinessEntry("PP GOAL / PP POINT / PP POINTS", "READY",
                   "situationCode alone (both defending-team AND attacking-team goalie-in-net digits "
                   "both '1', skater-count digits unequal) distinguishes a real power play from an "
                   "empty-net extra-attacker situation -- confirmed via the real '0651'/'1560' pulled-"
                   "goalie codes found in the pilot vs. the '1541'/'1451' PP/PK codes. This is a real "
                   "upgrade over the market-architecture slice's assumption that manpower state needs "
                   "penalty-duration reconstruction: it does not -- it is DIRECTLY AVAILABLE per event."),
    ReadinessEntry("SH GOAL / SH POINT", "READY", "same situationCode logic, opposite skater-count direction."),
    ReadinessEntry("TEAM PP GOALS / ANY PP GOAL / TOTAL PP GOALS", "READY", "aggregated from the same per-event flag."),
    ReadinessEntry("TEAM SH GOAL / ANY SH GOAL", "READY", "aggregated from the same per-event flag."),
]

PENALTY_MARKET_READINESS = [
    ReadinessEntry("PLAYER PIM", "READY",
                   "penalty.committedByPlayerId + duration, summed per player. Confirmed real nuance: "
                   "bench-assessed penalties (too-many-men, some delay-of-game types) carry NO "
                   "committedByPlayerId, only servedByPlayerId + team -- and the official boxscore does "
                   "NOT credit any individual skater's personal PIM for these either (confirmed via 0 "
                   "PIM mismatches across all 30 pilot games, 3 of which contained bench minors). This "
                   "is a real NHL scoring convention, not a data gap."),
    ReadinessEntry("RECEIVE PENALTY (yes/no)", "READY", "same field."),
    ReadinessEntry("TEAM PIM / TEAM PENALTIES / TOTAL PIM / TOTAL PENALTIES", "READY",
                   "team-level aggregation includes bench-assessed penalties via eventOwnerTeamId."),
]

HIT_FACEOFF_READINESS = [
    ReadinessEntry("HITS / HIT ALTERNATES", "READY",
                   "hittingPlayerId + hitteePlayerId present on 1,240/1,240 (100%) of hit events in the pilot."),
    ReadinessEntry("FACEOFF WINS / FACEOFFS TAKEN / FACEOFF PERCENTAGE", "READY",
                   "winningPlayerId + losingPlayerId present on 1,753/1,753 (100%) of faceoff events in the pilot."),
    ReadinessEntry("TEAM FACEOFF WINS / TOTAL FACEOFFS", "READY", "aggregated from the same fields."),
]
HIT_FACEOFF_SCORER_BIAS_CAVEAT = (
    "Publicly documented in the wider hockey-analytics community (MoneyPuck/Corsica/Natural Stat "
    "Trick project notes): official in-arena scorers show measurable rink-to-rink bias in hit and "
    "giveaway/takeaway recording specifically (goals, assists, shots, and faceoffs do not show this "
    "pattern to the same degree). This is a known caveat about the SOURCE data, not something "
    "independently re-verified against this project's own 30-game pilot -- flagged here for future "
    "modeling awareness, not as a new finding of this slice."
)

GOALIE_MARKET_READINESS = [
    ReadinessEntry("FULL-GAME SAVES / ALTERNATE SAVES", "READY",
                   "reconstructible from shot-on-goal + goal events' goalieInNetId; also directly present "
                   "on /boxscore's per-goalie saves field (cross-checked, not just assumed)."),
    ReadinessEntry("PERIOD SAVES", "READY", "same corpus-validated result as GOALIE SAVES BY PERIOD above."),
    ReadinessEntry("GOALS ALLOWED", "READY", "goal events' goalieInNetId, excluding shootout and empty-net goals."),
    ReadinessEntry("SHUTOUT", "READY", "goals_allowed == 0 for the goalie who played the entire statistical game."),
    ReadinessEntry("GOALIE WIN", "NOT READY",
                   "no explicit W/L/OTL 'decision' field was found on EITHER the play-by-play or "
                   "/boxscore endpoint (both explicitly checked this slice). The real NHL win-crediting "
                   "rule has edge cases (e.g. a starter pulled mid-game can still be awarded the win "
                   "depending on the score at time of departure) that are not purely derivable from shot/"
                   "goal counting. The /landing endpoint was never queried this slice -- out of scope -- "
                   "and may carry this field; that is future work, not confirmed here."),
    ReadinessEntry("GOALIE ALLOWED FIRST GOAL", "READY", "first statistical goal's goalieInNetId."),
    ReadinessEntry("BOTH GOALIES X+ SAVES", "READY", "joint condition over FULL-GAME SAVES."),
]


GAME_STATE_RECONSTRUCTION_READINESS = [
    ReadinessEntry("PERIOD", "READY", "every event carries period_number + period_type directly."),
    ReadinessEntry("GAME CLOCK", "READY",
                   "seconds_elapsed_in_period / regulation_elapsed_seconds computed for every REG/OT event; "
                   "SO is correctly None (attempt-indexed, not clock-indexed)."),
    ReadinessEntry("HOME SCORE / AWAY SCORE", "READY",
                   "reconstruct_statistical_score() gives an exact running score after every statistical goal, "
                   "verified against the real final score on every reconciled game across all 4 seasons."),
    ReadinessEntry("MANPOWER STATE", "READY", "situationCode, directly available, no reconstruction (Section N)."),
    ReadinessEntry("GOALIE-PRESENT STATE", "READY",
                   "goalieInNetId presence/absence + situationCode's goalie digit, jointly (Section O)."),
]
GAME_STATE_RECONSTRUCTION_READY = "YES"  # every one of the 5 components above is READY, not PARTIAL/NO

OT_SHOOTOUT_READINESS = [
    ReadinessEntry("OT YES/NO", "READY", "gameOutcome.lastPeriodType / presence of a period_type == 'OT' event."),
    ReadinessEntry("SHOOTOUT YES/NO", "READY", "presence of a period_type == 'SO' event, or shootout-complete."),
    ReadinessEntry("METHOD OF VICTORY", "READY",
                   "REG / OT / SO derivable directly from gameOutcome.lastPeriodType, confirmed identical "
                   "in shape across all 4 seasons in the contract-drift audit (Section H)."),
    ReadinessEntry("EXACT SCORE", "READY",
                   "reconstructed statistical score, plus the confirmed +1 shootout-bonus-goal rule for SO games."),
    ReadinessEntry("WINNING MARGIN", "READY", "difference of the final reconstructed/boxscore score."),
]


def summarize(entries: list[ReadinessEntry]) -> dict:
    out = {"READY": 0, "PARTIAL": 0, "NOT READY": 0}
    for e in entries:
        out[e.readiness] += 1
    return out


ALL_SECTIONS = {
    "period_markets": PERIOD_MARKET_READINESS,
    "event_time_markets": EVENT_TIME_MARKET_READINESS,
    "special_teams_markets": SPECIAL_TEAMS_READINESS,
    "penalty_markets": PENALTY_MARKET_READINESS,
    "hit_faceoff_markets": HIT_FACEOFF_READINESS,
    "goalie_markets": GOALIE_MARKET_READINESS,
    "game_state_reconstruction": GAME_STATE_RECONSTRUCTION_READINESS,
    "ot_shootout": OT_SHOOTOUT_READINESS,
}
