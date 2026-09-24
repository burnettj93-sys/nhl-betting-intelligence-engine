"""
Yahoo connection diagnostic (Production Readiness Audit / Yahoo
compliance rebuild, 2026-09-24) -- the Phase 9 "First Test": prove
connectivity and retrieve exactly what's needed to reach
YAHOO_CONNECTION_CERTIFIED, before any recommendation feature is built
on top of it.

Every fetch here is transient: the ApiResult from client.get_resource()
is read, a small sanitized summary is extracted, and the raw Yahoo
response is discarded when this function returns. Nothing in this
module writes to a file, a database, or a log line -- matching
~/yahoo-fantasy-cockpit's own proven, AST-structurally-tested
"no persistence of Yahoo data" guarantee. The returned dict is for
immediate, one-time display only; a caller that stores it defeats the
whole point.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from fantasy.yahoo.client import YahooFantasyClient
from fantasy.yahoo.contracts import NHL_GAME_CODE, VERIFIED_ENDPOINTS
from fantasy.yahoo.parser import extract_league_settings


@dataclass
class DiagnosticStep:
    step: str
    ok: bool
    detail: str = ""


@dataclass
class DiagnosticReport:
    steps: list[DiagnosticStep] = field(default_factory=list)
    identity_guid: str | None = None
    game_key: str | None = None
    league_keys: list[str] = field(default_factory=list)
    selected_league_key: str | None = None
    league_name: str | None = None
    scoring_type: str | None = None
    roster_position_count: int | None = None
    stat_category_count: int | None = None
    team_key: str | None = None
    team_name: str | None = None

    @property
    def certified(self) -> bool:
        """YAHOO_CONNECTION_CERTIFIED (Phase 9): every step must have
        succeeded, and the diagnostic must have actually resolved a real
        league and team -- not just "the identity call returned 200"."""
        return (bool(self.steps) and all(s.ok for s in self.steps)
                and self.identity_guid is not None and self.selected_league_key is not None
                and self.team_key is not None)


def _first(value):
    """Yahoo's XML->dict parser (parser.py::parse_fantasy_content)
    represents a repeated element as a list -- this project's own
    convention elsewhere (extract_standings, extract_roster_players) is
    to walk that list explicitly rather than guess; here we only need
    the first entry to prove connectivity, not a full collection."""
    if isinstance(value, list):
        return value[0] if value else None
    return value


def run_connection_diagnostic(client: YahooFantasyClient) -> DiagnosticReport:
    report = DiagnosticReport()

    # 1. Identity
    result = client.get_resource(VERIFIED_ENDPOINTS["my_identity"])
    if not result.ok:
        report.steps.append(DiagnosticStep("identity", False, f"{result.status}: {result.error}"))
        return report
    try:
        user = _first(result.data.get("users", {}).get("user"))
        report.identity_guid = user.get("guid") if user else None
    except (AttributeError, TypeError):
        report.identity_guid = None
    report.steps.append(DiagnosticStep("identity", report.identity_guid is not None,
                                        "resolved a Yahoo GUID" if report.identity_guid else
                                        "response shape didn't contain a guid -- see NOT_VERIFIED_THIS_SESSION "
                                        "note in contracts.py"))

    # 2. NHL fantasy game for the current season
    result = client.get_resource(VERIFIED_ENDPOINTS["my_games"].format(game_key=NHL_GAME_CODE))
    if not result.ok:
        report.steps.append(DiagnosticStep("nhl_game", False, f"{result.status}: {result.error}"))
        return report
    try:
        game = _first(result.data.get("users", {}).get("user", {}).get("games", {}).get("game"))
        report.game_key = game.get("game_key") if game else None
    except (AttributeError, TypeError):
        report.game_key = None
    report.steps.append(DiagnosticStep("nhl_game", report.game_key is not None,
                                        f"game_key={report.game_key}" if report.game_key else
                                        "no NHL fantasy game found for this account this season"))
    if report.game_key is None:
        return report

    # 3. My leagues within that game
    result = client.get_resource(VERIFIED_ENDPOINTS["my_leagues"].format(game_key=NHL_GAME_CODE))
    if not result.ok:
        report.steps.append(DiagnosticStep("my_leagues", False, f"{result.status}: {result.error}"))
        return report
    try:
        game_node = _first(result.data.get("users", {}).get("user", {}).get("games", {}).get("game"))
        leagues_node = game_node.get("leagues", {}).get("league", []) if game_node else []
        leagues = leagues_node if isinstance(leagues_node, list) else [leagues_node]
        report.league_keys = [lg.get("league_key") for lg in leagues if lg and lg.get("league_key")]
    except (AttributeError, TypeError):
        report.league_keys = []
    report.steps.append(DiagnosticStep("my_leagues", len(report.league_keys) > 0,
                                        f"{len(report.league_keys)} league(s) found" if report.league_keys
                                        else "no leagues found for this account this season"))
    if not report.league_keys:
        return report
    report.selected_league_key = report.league_keys[0]  # Phase 9: prove connectivity, not choose a league

    # 4. League settings + scoring categories, for the selected league
    result = client.get_resource(f"/league/{report.selected_league_key}/settings")
    if result.ok:
        try:
            settings = extract_league_settings(result.data)
            report.league_name = settings.get("name")
            report.scoring_type = settings.get("scoring_type")
            report.roster_position_count = len(settings.get("roster_positions", []) or [])
            report.stat_category_count = len(settings.get("stat_categories", []) or [])
        except (AttributeError, TypeError, KeyError):
            pass
    report.steps.append(DiagnosticStep("league_settings", result.ok,
                                        f"scoring_type={report.scoring_type}, "
                                        f"{report.roster_position_count} roster position(s), "
                                        f"{report.stat_category_count} stat categor(y/ies)" if result.ok
                                        else f"{result.status}: {result.error}"))

    # 5. My team within this game
    result = client.get_resource(VERIFIED_ENDPOINTS["my_teams"].format(game_key=NHL_GAME_CODE))
    if result.ok:
        try:
            game_node = _first(result.data.get("users", {}).get("user", {}).get("games", {}).get("game"))
            teams_node = game_node.get("teams", {}).get("team", []) if game_node else []
            teams = teams_node if isinstance(teams_node, list) else [teams_node]
            team = teams[0] if teams else None
            report.team_key = team.get("team_key") if team else None
            report.team_name = team.get("name") if team else None
        except (AttributeError, TypeError):
            pass
    report.steps.append(DiagnosticStep("my_team", report.team_key is not None,
                                        f"team_key={report.team_key}" if report.team_key
                                        else (f"{result.status}: {result.error}" if not result.ok
                                              else "no team found for this account in this league")))

    return report


def sanitized_summary(report: DiagnosticReport) -> dict:
    """A display-safe dict -- no raw Yahoo payload, no token content.
    Team/league NAMES are included (they're what Phase 9 asks the
    diagnostic to show), but nothing beyond identity/settings/team-level
    summary fields -- never a full roster or player list, which belongs
    to the (not-yet-built) recommendation layer, not this diagnostic."""
    return {
        "certified": report.certified,
        "identity_guid": report.identity_guid,
        "game_key": report.game_key,
        "league_count": len(report.league_keys),
        "selected_league_key": report.selected_league_key,
        "league_name": report.league_name,
        "scoring_type": report.scoring_type,
        "roster_position_count": report.roster_position_count,
        "stat_category_count": report.stat_category_count,
        "team_key": report.team_key,
        "team_name": report.team_name,
        "steps": [{"step": s.step, "ok": s.ok, "detail": s.detail} for s in report.steps],
    }
