"""
Part 16: normalized play-by-play schema.

Three flat, clean objects rather than one sparse giant table (Part 16's own
guidance): a game, an event, and the event's structured detail payload.
Player-role attribution (Part 18's "PLAYER IDENTITY" fields) lives inside
`PbpEvent.players`, a role-name -> player_id dict, rather than a fourth
join table -- the real role set differs enormously by event type (a goal
has up to 3 roles, a hit has 2, a stoppage has 0) and a fixed-column
pbp_event_players table would be exactly the sparse-column anti-pattern
Part 16 warns against. `players` is still trivially queryable/joinable
since role names are drawn from a small fixed vocabulary (see
PLAYER_ROLE_NAMES below).

Every player_id used here is the real NHL player ID as returned by the
feed -- confirmed project-wide (MONEYPUCK_DATA_CONTRACT_REVIEW.md) that
MoneyPuck's own player IDs ARE NHL IDs, so no crosswalk is introduced.
"""
from __future__ import annotations

from dataclasses import dataclass, field

PLAYER_ROLE_NAMES = (
    "scorer", "assist1", "assist2",           # goal
    "shooter",                                 # shot-on-goal / missed-shot / blocked-shot / SO attempt
    "blocker",                                 # blocked-shot
    "hitter", "hittee",                        # hit
    "committed_by", "drawn_by", "served_by",   # penalty
    "winner", "loser",                         # faceoff
    "goalie",                                  # goalie in net (any shot-type event, defending team)
    "giveaway_by", "takeaway_by",               # giveaway / takeaway
)


@dataclass
class PbpGame:
    game_id: int
    season: str
    game_type: int
    game_date: str
    away_team_id: int
    away_team_abbrev: str
    home_team_id: int
    home_team_abbrev: str
    final_period_type: str          # REG / OT / SO -- from gameOutcome.lastPeriodType
    ot_periods: int                 # 0 unless final_period_type == "OT"
    reg_periods: int                # regPeriods, e.g. 3
    raw_source_url: str
    raw_sha256: str
    retrieved_at_utc: str
    provider: str = "api-web.nhle.com"
    archival_status: str = "ARCHIVAL_RESEARCH"


@dataclass
class PbpEvent:
    game_id: int
    event_id: int
    event_sequence: int              # canonical order key -- see normalize.py Part 4 finding
    event_type: str                  # normalized typeDescKey, e.g. "goal"
    type_code: int
    period_number: int
    period_type: str                 # REG / OT / SO
    time_in_period: str               # raw "MM:SS", preserved
    seconds_elapsed_in_period: int
    seconds_remaining_in_period: int | None
    regulation_elapsed_seconds: int | None   # None for SO (Part 3: SO has no game-clock time)
    team_id: int | None               # eventOwnerTeamId if present
    situation_code: str | None
    zone_code: str | None
    x_coord: int | None
    y_coord: int | None
    is_statistical: bool              # False for any event inside periodType == "SO" (Part 6)
    players: dict = field(default_factory=dict)   # role_name -> player_id
    raw_details: dict = field(default_factory=dict)  # preserved verbatim (Part 16: "retain raw source reference")
