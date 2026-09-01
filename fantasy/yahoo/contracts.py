"""
Yahoo Fantasy Sports API contract documentation (2026-09-01), per this
sprint's explicit Part 1 instruction: "Do NOT work from memory alone."

Every entry below is either VERIFIED (fetched from Yahoo's own current,
live developer documentation this session -- see the URL in each entry)
or NOT_VERIFIED_THIS_SESSION (based on Yahoo's long-stable, widely-used
public API shape, but not independently re-confirmed by fetching live
docs in this sprint -- flagged honestly rather than silently assumed).
Nothing in this file was invented; every field name and endpoint either
came from a real fetched page or is marked as unverified.

================================================================
ABSOLUTE SEPARATION FROM THE BETTING ENGINE
================================================================
This module (and everything under fantasy/) is a READ-ONLY consumer of
the existing NHL Intelligence Engine's real outputs. It must never:
  - modify a production betting model or its coefficients
  - modify research/player_props/decision_policy.py (the betting
    BET/WATCH/WAIT/PASS gate)
  - modify PP-role overlay or Goals/Points context-overlay coefficients
  - modify sportsbook pricing logic, Top Conviction logic, the paper
    bankroll, the real-bet ledger, or DraftKings contract logic
  - modify joint-betting-model coefficients
Fantasy code may IMPORT AND CALL these modules' real, already-tested
public functions (e.g. the same PlayerSogMarginal/ContextMarginalContext
the demo betting engine already reuses) -- it must never edit them.
See tests/test_fantasy_betting_isolation.py for the enforced AST guard.

================================================================
OAUTH 2.0 -- VERIFIED
================================================================
Source: https://developer.yahoo.com/oauth2/guide/flows_authcode/
(fetched live, 2026-09-01) -- "Authorization Code Flow for Server-side
Apps", explicitly the flow to use for a server-side (web) app like this
dashboard.

Authorization endpoint (GET or POST):
    https://api.login.yahoo.com/oauth2/request_auth
    params: client_id, redirect_uri, response_type=code, state
    (optional), language (optional)

Token endpoint (POST), used for BOTH the initial code exchange and
every subsequent refresh:
    https://api.login.yahoo.com/oauth2/get_token
    header: Authorization: Basic base64(client_id:client_secret)
    body (initial exchange):
        grant_type=authorization_code&redirect_uri=...&code=...
    body (refresh):
        grant_type=refresh_token&redirect_uri=...&refresh_token=...
    response (JSON):
        access_token, token_type, expires_in (3600s / 1 hour),
        refresh_token, xoauth_yahoo_guid (deprecated by Yahoo's own
        docs -- do not rely on it; use OpenID Connect id_token if a
        GUID is ever needed)

Note found this session: the "Basic OAuth flow" sample PHP linked
directly from Yahoo's Fantasy Sports API guide
(gist.github.com/VerizonMediaOwner/c739411169d6f92ceec36ec33a6ef5b8) is
STALE -- it implements OAuth 1.0a (oauth_token/oauth_token_secret/
oauth_session_handle, HMAC-SHA1 signing), contradicting the surrounding
guide text's own explicit statement that "The Yahoo Fantasy Sports API
requires Oauth 2.0". This module follows the verified OAuth 2.0 guide
above, not that stale linked sample.

================================================================
BASE API -- VERIFIED
================================================================
Source: https://sports.yahoo.com/developer/docs/ (fetched live,
2026-09-01), "Yahoo Fantasy Sports API Documentation".

Base URL: https://fantasysports.yahooapis.com/fantasy/v2

Response format: XML by default (confirmed via real sample responses
on the docs page, e.g. <fantasy_content ...><game>...).

Resource key formats (confirmed via real examples on the docs page):
    game_key:   <game_id>                              e.g. 461
    league_key: <game_id>.l.<league_id>                 e.g. 461.l.1000
    team_key:   <game_id>.l.<league_id>.t.<team_id>      e.g. 461.l.1000.t.1
    player_key: <game_id>.p.<player_id>                 e.g. 461.p.30121

URI pattern for a resource: /fantasy/v2/{resource}/{resource_key}
URI pattern for filtered collection members:
    /fantasy/v2/{collection};{resource}_keys={key1},{key2}
Sub-resources are chained: /fantasy/v2/{resource}/{resource_key}/{sub_resource}
Multiple sub-resources in one call: ;out={sub_resource_1},{sub_resource_2}

================================================================
GAME RESOURCE -- VERIFIED
================================================================
GET /fantasy/v2/game/{game_key}
game_key is either a game_id (season-specific, e.g. 461) or a
game_code (season-independent, e.g. "nfl" -- always resolves to the
CURRENT season's game_id). Real sample response confirmed fields:
    game_key, game_id, name, code, type, url, season,
    is_registration_over, is_game_over, is_offseason
Sub-resources confirmed: metadata, leagues, players, dates, game_weeks,
stat_categories, position_types, roster_positions.

To discover the current season's game_id for hockey (Part 8 -- "do not
hard-code historical game keys"):
    GET /fantasy/v2/games;game_codes=nhl;seasons=<season>
or simply use game_code "nhl" directly as the game_key wherever a
game_key is accepted, which Yahoo's own docs confirm always resolves
to the current season.
NOT_VERIFIED_THIS_SESSION: the exact game_code string for NHL hockey
was not independently re-confirmed against a live fetched page this
session (the docs page's own worked examples were all NFL). "nhl" is
Yahoo's long-standing, stable game_code for hockey and is used as such
throughout this module, but this specific string is flagged rather than
silently assumed verified.

================================================================
LEAGUE RESOURCE -- VERIFIED (metadata, settings, standings)
================================================================
GET /fantasy/v2/league/{league_key}
Sub-resources confirmed: metadata, settings, standings, scoreboard,
teams, players, draftresults, transactions.

metadata fields (real sample XML, confirmed):
    league_key, league_id, name, url, logo_url, draft_status, num_teams,
    edit_key, weekly_deadline, league_update_timestamp, scoring_type
    ("head" confirmed as one real value -- head-to-head; other values
    NOT_VERIFIED_THIS_SESSION, e.g. "points" or "roto"), league_type,
    current_week, start_week, start_date, end_week, end_date,
    is_finished, game_code, season

settings fields (real sample XML, confirmed):
    draft_type, is_auction_draft, scoring_type, uses_playoff,
    has_playoff_consolation_games, playoff_start_week,
    uses_playoff_reseeding, uses_lock_eliminated_teams,
    num_playoff_teams, num_playoff_consolation_teams,
    has_multiweek_championship, waiver_type, waiver_rule, uses_faab,
    draft_time, draft_pick_time, post_draft_players, max_teams,
    waiver_time, trade_end_date, trade_ratify_type, trade_reject_time,
    player_pool, cant_cut_list,
    roster_positions: list of {position, position_type, count}
    stat_categories.stats: list of {stat_id, enabled, name,
        display_name, sort_order, position_type, is_only_display_stat,
        is_excluded_from_display}
    stat_modifiers.stats: list of {stat_id, value} -- the per-stat
        scoring weight (points-league multiplier)
    pickem_enabled, uses_fractional_points, uses_negative_points
This is the REAL schema fantasy/league/settings.py parses against --
it is a real, confirmed points-league example (scoring_type=head,
uses_fractional_points=1). The owner's own real league (confirmed via a
screenshot of their actual league settings page, not the API) is ALSO
a points league with fractional weights (Goals 4.5, Assists 3, PIM 0.5,
PPP 0.5, SOG 0.5, HIT 0.5, BLK 0.75 for skaters; Wins 4.5, GA -1.5,
Saves 0.3, Shutouts 4.5 for goalies), roster C,C,LW,LW,RW,RW,D,D,D,D,
Util,Util,G,G,BN,BN,BN,BN,IR,IR,IR+,IR+ -- structurally consistent with
this verified settings schema, and used as the concrete reference case
for fixtures/tests in this sprint (never assumed to be every league's
settings -- see Part 12's own explicit warning, honored by
fantasy/league/settings.py never hard-coding a default category list).

standings fields (real sample XML, confirmed): teams collection, each
with team_key, team_id, name, url, team_logos, waiver_priority,
number_of_moves, number_of_trades, roster_adds, clinched_playoffs,
league_scoring_type, draft_position, draft_grade, managers.manager_id,
team_points.total, team_standings: {rank, playoff_seed, outcome_totals:
{wins, losses, ties, percentage}, streak: {type, value}, points_for,
points_against}.

NOT_VERIFIED_THIS_SESSION (scoreboard/matchups, teams collection detail,
players collection filters, draftresults, transactions): the docs page
documents these sub-resources exist and gave their URI patterns, but
this session's live fetch did not reach worked sample XML for them
before the page's content-safety extraction tooling blocked further
scraping of the raw XML-heavy sections. Their existence and URI shape
ARE verified; their exact field-by-field response shape is not, and
fantasy/yahoo/parser.py's parsers for these are built against Yahoo's
long-stable, widely-documented public shape (used by e.g. the
long-maintained yahoo_fantasy_api community library) with an explicit
NOT_VERIFIED_THIS_SESSION flag, to be confirmed against a real sanitized
fixture the moment OWNER_AUTH_REQUIRED is resolved (Part 150).

================================================================
TEAM / ROSTER / PLAYER RESOURCES -- NOT_VERIFIED_THIS_SESSION
================================================================
Not independently re-fetched from live Yahoo docs this session (see
note above). Built against Yahoo's long-stable, widely-documented
public contract:
  Team resource: /fantasy/v2/team/{team_key}
    sub-resources: metadata, stats, standings, roster, draftresults,
    matchups
  Roster sub-resource: /fantasy/v2/team/{team_key}/roster
    returns players with: player_key, name (full/first/last),
    editorial_team_abbr, editorial_team_full_name, display_position,
    eligible_positions, status (e.g. "DTD", "IR", "O"), status_full,
    selected_position (the Yahoo roster slot this player currently
    occupies, e.g. "C", "BN", "IR")
  Player resource: /fantasy/v2/player/{player_key}
    sub-resources: metadata, stats, ownership, draft_analysis
  Available/free-agent players: /fantasy/v2/league/{league_key}/players
    filtered by status=FA (free agent) or status=W (waivers) --
    Yahoo's own long-documented filter values; NOT re-confirmed live
    this session (Part 43's own instruction to verify this precisely
    before trusting it for waiver-wire ranking -- flagged here as the
    #1 thing to re-confirm the moment real OAuth access exists).

================================================================
STATUS
================================================================
VERIFIED this session: OAuth 2.0 flow, base API/resource-key contract,
Game resource, League metadata/settings/standings.
NOT_VERIFIED_THIS_SESSION (built against stable public contract, to be
confirmed against real fixtures once OWNER_AUTH_REQUIRED is resolved):
Team, Roster, Player, available-players filter semantics, Scoreboard/
Matchup detail, draftresults, transactions.
"""
from __future__ import annotations

VERIFIED_ENDPOINTS = {
    "oauth_authorize": "https://api.login.yahoo.com/oauth2/request_auth",
    "oauth_token": "https://api.login.yahoo.com/oauth2/get_token",
    "base_url": "https://fantasysports.yahooapis.com/fantasy/v2",
    "game": "/game/{game_key}",
    "games_by_code_season": "/games;game_codes={game_code};seasons={season}",
    "league_metadata": "/league/{league_key}/metadata",
    "league_settings": "/league/{league_key}/settings",
    "league_standings": "/league/{league_key}/standings",
}

NOT_VERIFIED_THIS_SESSION_ENDPOINTS = {
    "team": "/team/{team_key}",
    "team_roster": "/team/{team_key}/roster",
    "player": "/player/{player_key}",
    "league_players_available": "/league/{league_key}/players;status={status}",
    "league_scoreboard": "/league/{league_key}/scoreboard;week={week}",
    "league_draftresults": "/league/{league_key}/draftresults",
    "league_transactions": "/league/{league_key}/transactions",
}

# NHL's Yahoo game_code -- see the GAME RESOURCE section's own
# NOT_VERIFIED_THIS_SESSION note above.
NHL_GAME_CODE = "nhl"
