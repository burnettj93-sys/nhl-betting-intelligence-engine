"""
Parts 13-18: deterministic game-winning-goal derivation from the FINAL
statistical score -- never from "last goal", "go-ahead goal at the time",
or a hard-coded OT/empty-net special case (Part 13's explicit ban on all
three). Reuses `normalize.reconstruct_statistical_score()` for the score
timeline rather than re-deriving it (Part 22: one source of truth).

Exact NHL statistical definition implemented (Part 13): for a game whose
final statistical score is Winner=W, Loser=L, the GWG is the goal that
gives the winning team its (L + 1)-th statistical goal. This is a pure
function of the FINAL score, so a later empty-net goal (Part 16) or an
early two-goal lead that gets partially clawed back (Part 17, comeback/
multi-lead-change games) can never change which goal it points to --
found by counting the winning team's own goals in event_sequence order
and taking the one at position (L + 1), full stop.

Shootout games (Part 14): the real statistical score stays TIED after
regulation/OT for every shootout game (confirmed project-wide -- a
shootout-deciding goal is explicitly excluded from the statistical score,
see normalize.py's Section G finding). A tied final statistical score has
no "winning team" in the statistical sense, so the GWG definition above
has no goal to point to -- there is no independent official field to
cross-check this against (Part 19: no GWG field exists anywhere in the
play-by-play or /boxscore contract this project has queried), so this is
derived from the score-timeline invariant itself, not assumed.
"""
from __future__ import annotations

from dataclasses import dataclass

from research.real_nhl_pbp import normalize
from research.real_nhl_pbp.schema import PbpEvent

STATUS_RESOLVED = "RESOLVED"
STATUS_NO_PLAYER_GWG_SHOOTOUT = "NO_PLAYER_GWG_SHOOTOUT"


@dataclass
class GwgResult:
    game_id: int
    status: str  # STATUS_RESOLVED / STATUS_NO_PLAYER_GWG_SHOOTOUT
    winning_team: int | None
    losing_team: int | None
    final_home_score: int
    final_away_score: int
    gwg_event_id: int | None = None
    gwg_event_sequence: int | None = None
    gwg_period: int | None = None
    gwg_period_type: str | None = None
    gwg_time_in_period: str | None = None
    scoring_player_id: int | None = None
    empty_net: bool = False
    strength_state: str | None = None  # EV / PP / SH / UNKNOWN


def _strength_state(event: PbpEvent, scoring_team_is_away: bool) -> str:
    """EV / PP / SH, using the same situationCode logic already
    established (and reused, not re-derived) for special-teams
    readiness: a skater-count asymmetry only means a real power play when
    BOTH goalie-in-net digits are '1' -- an asymmetry caused by a pulled
    goalie (either digit '0') is an extra-attacker situation, not a
    manpower advantage, and must not be misclassified as PP/SH."""
    sc = event.situation_code
    if not sc or len(sc) != 4:
        return "UNKNOWN"
    away_goalie, away_skaters, home_skaters, home_goalie = sc[0], sc[1], sc[2], sc[3]
    if away_goalie != "1" or home_goalie != "1":
        return "EV"  # empty-net extra-attacker situation -- not a penalty-driven strength state
    if away_skaters == home_skaters:
        return "EV"
    scoring_side_skaters = away_skaters if scoring_team_is_away else home_skaters
    other_side_skaters = home_skaters if scoring_team_is_away else away_skaters
    if scoring_side_skaters > other_side_skaters:
        return "PP"
    if scoring_side_skaters < other_side_skaters:
        return "SH"
    return "UNKNOWN"


def derive_gwg(events: list[PbpEvent], game_id: int, home_team_id: int, away_team_id: int) -> GwgResult:
    timeline = normalize.reconstruct_statistical_score(events, home_team_id, away_team_id)
    final_home = timeline[-1]["home_score"] if timeline else 0
    final_away = timeline[-1]["away_score"] if timeline else 0

    if final_home == final_away:
        # Shootout game: statistical score stays tied (Part 14). No other
        # non-SO regular-season outcome can produce a tie under NHL rules.
        return GwgResult(
            game_id=game_id, status=STATUS_NO_PLAYER_GWG_SHOOTOUT,
            winning_team=None, losing_team=None,
            final_home_score=final_home, final_away_score=final_away,
        )

    if final_home > final_away:
        winning_team, losing_team = home_team_id, away_team_id
        losing_final_goals = final_away
    else:
        winning_team, losing_team = away_team_id, home_team_id
        losing_final_goals = final_home

    gwg_ordinal = losing_final_goals + 1  # Part 13's exact rule

    winning_team_goals = sorted(
        (e for e in events if e.event_type == "goal" and e.is_statistical and e.team_id == winning_team),
        key=lambda e: e.event_sequence,
    )
    # Part 21 invariant: a winning team with N final goals must have >=
    # gwg_ordinal goals recorded; this is a structural certainty given the
    # timeline already produced `final_home`/`final_away` from these same
    # goal events, not a coincidence to guard defensively against here.
    gwg_event = winning_team_goals[gwg_ordinal - 1]

    scoring_team_is_away = (winning_team == away_team_id)
    defending_is_away = not scoring_team_is_away
    empty_net = normalize.is_empty_net_context(gwg_event, defending_team_is_away=defending_is_away)

    return GwgResult(
        game_id=game_id, status=STATUS_RESOLVED,
        winning_team=winning_team, losing_team=losing_team,
        final_home_score=final_home, final_away_score=final_away,
        gwg_event_id=gwg_event.event_id, gwg_event_sequence=gwg_event.event_sequence,
        gwg_period=gwg_event.period_number, gwg_period_type=gwg_event.period_type,
        gwg_time_in_period=gwg_event.time_in_period,
        scoring_player_id=gwg_event.players.get("scorer"),
        empty_net=empty_net,
        strength_state=_strength_state(gwg_event, scoring_team_is_away),
    )
