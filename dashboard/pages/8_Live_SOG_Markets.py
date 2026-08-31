"""Page 8 — Live SOG Markets: real DraftKings SOG prices (via The Odds
API) vs. the validated SOG model. STATUS: LIVE MODEL VS MARKET (research)
— NO AUTOMATIC BETTING. See PLAYER_SOG_LIVE_PRICING_REPORT.md."""
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import streamlit as st

from dashboard import components as comp
from dashboard import data_access as da
from dashboard import live_sog_pricing_view as lv
from research.player_sog import features as pf
from research.player_sog.live_projection import project_player_sog
from research.live_sog_pricing import observation_ledger as ledger
from research import elo_comparison as ec
from research.run_player_sog_model import build_team_schedules, NHL_CORPUS_PATH


@st.cache_data(show_spinner=False)
def _load_sog_results():
    import json
    path = REPO_ROOT / "research" / "player_sog_results.json"
    if not path.exists():
        return None
    with open(path) as f:
        return json.load(f)


@st.cache_data(show_spinner="Loading real player-game SOG corpus...")
def _load_sog_rows():
    return pf.load_sog_corpus()


@st.cache_resource(show_spinner=False)
def _build_index(_rows):
    return pf.PlayerHistoryIndex(_rows)


@st.cache_resource(show_spinner=False)
def _build_opponent_history(_rows):
    totals = pf.build_team_game_totals(_rows)
    allowed = pf.build_opponent_allowed_history(totals)
    import statistics
    league_avg = statistics.fmean(v["sog_for"] for v in totals.values())
    return allowed, league_avg


@st.cache_resource(show_spinner=False)
def _load_team_schedules():
    games = ec.load_corpus(str(NHL_CORPUS_PATH))
    return build_team_schedules(games)


st.title("Live SOG Markets")
comp.render_model_status_header()
st.markdown(
    """
    <div style="border:1px solid #1a4a5c; border-radius:6px; padding:8px 12px;
                background:#0a1f24; color:#6ad0e8; font-size:0.85rem; margin-bottom:12px;">
      LIVE MODEL VS MARKET — real DraftKings prices (via The Odds API) compared against the
      validated SOG model's own probability. <b>NO AUTOMATIC BETTING</b> — this page never places
      a wager; every action shown is a research decision label (BET / WATCH / WAIT / PASS /
      DATA_UNAVAILABLE), not an order. This page reads a CACHED snapshot only — it never makes a
      network call itself. Refresh explicitly with:
      <code>python3 -m research.live_sog_pricing.refresh</code>
    </div>
    """,
    unsafe_allow_html=True,
)

cache = lv.load_board_cache()
if cache is None:
    st.info("No cached board found yet. Run `python3 -m research.live_sog_pricing.refresh` "
            "to fetch real live data (credit-conscious — only queries events within the next "
            "few days).")
else:
    summary = cache["summary"]
    board = cache["board"]
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Refreshed at (UTC)", summary["refreshed_at_utc"][:19])
    c2.metric("Real NHL events seen", summary["events_seen"])
    c3.metric("Events in near-term window", summary["events_in_near_term_window"])
    c4.metric("Priced observations", summary["observations_priced"])
    if summary.get("api_error"):
        st.error(f"Last refresh API error: {summary['api_error']}")
    st.caption(f"Odds API credits used/remaining (as last observed): "
               f"{summary.get('requests_used_last_seen')} / {summary.get('requests_remaining_last_seen')}")

    if not board:
        comp.render_empty_state("NO_LIVE_MARKETS",
                                 "No DraftKings SOG markets currently posted. This is expected — sportsbooks "
                                 "post player props only within roughly 24-72 hours of puck drop, and no "
                                 "real event was close enough to that window at last refresh. This is not a "
                                 "system failure; it is the honest, real state of the market right now.")
    else:
        sort_key = st.selectbox("Sort by", ["conservative_ev", "conservative_edge", "raw_edge"], index=0)
        priced = [r for r in board if r.get("status") == "PRICED"]
        priced.sort(key=lambda r: r.get(sort_key) or -999, reverse=True)

        def _board_row_to_card(r: dict) -> dict:
            # Section 48 fix: defensive .get() access everywhere -- a
            # missing/renamed board-cache key degrades gracefully
            # (rendered as NO LIVE PRICE / — by the shared formatters)
            # instead of raising a raw KeyError to the user.
            threshold_label = " ".join(str(x) for x in
                                        (r.get("point", r.get("threshold")), r.get("side")) if x)
            return {
                "player": r.get("player_name_raw", "?"), "team": r.get("team", ""),
                "opponent": r.get("opponent", ""), "market": r.get("market", "SOG"),
                "threshold": threshold_label, "decision": r.get("action", "PASS"),
                "confidence": r.get("confidence"),
                "raw_probability": r.get("model_probability"),
                "context_adjusted_probability": r.get("model_probability"),  # no overlay for SOG
                "conservative_probability": r.get("conservative_probability"),
                "market_no_vig_probability": r.get("market_no_vig_probability"),
                "fair_odds": r.get("model_fair_price"),
                "current_odds": r.get("draftkings_price"),
                "max_acceptable_price": r.get("maximum_acceptable_price"),
                "conservative_edge": r.get("conservative_edge"), "ev": r.get("conservative_ev"),
                "drivers": [], "risks": [f"Lineup: {r.get('lineup_status', 'UNKNOWN')}",
                                          f"Zone: {r.get('zone', '?')}"],
            }

        for r in priced:
            comp.render_opportunity_card(_board_row_to_card(r))

    obs = ledger.load_all_observations()
    st.caption(f"Live market observation ledger: {len(obs)} stored observation(s) "
               f"(append-only, research only — not a bet ledger).")

comp.render_provenance_panel()
st.divider()
st.markdown("### Player drilldown")
st.caption("Reconstructs WHY the model has its probability for any real player, on any real date "
           "(historical or near-future) — the same computation the live board above uses.")

try:
    predictions = da.compute_baseline_predictions()
except da.DataAvailabilityError as exc:
    comp.render_missing_data_page(exc)
    st.stop()

results = _load_sog_results()
if results is None:
    st.warning("research/player_sog_results.json not found.")
    st.stop()

rows = _load_sog_rows()
index = _build_index(rows)
opponent_allowed_history, league_avg_sog_allowed = _build_opponent_history(rows)
team_schedules = _load_team_schedules()
weights = [results["stage_weights"][results["headline_stage"]][n] for n in results["config"]["feature_names"]]
alpha = results["negbinom_alpha_fitted"] if results["negbinom_alpha_fitted"] > 0.01 else None

teams = da.all_teams(predictions)
seasons = da.available_seasons(predictions)
col1, col2 = st.columns(2)
with col1:
    team = st.selectbox("Team", teams, index=teams.index("TOR") if "TOR" in teams else 0, key="drill_team")
with col2:
    season = st.selectbox("Season", seasons, index=len(seasons) - 1, format_func=da.format_season, key="drill_season")

team_games = [g for g in da.games_for_team(predictions, team) if g["season"] == season]
if not team_games:
    st.info(f"No real {da.format_season(season)} games found for {team}.")
    st.stop()

labels = [f"{g['game_date']} — vs {g['away_team'] if g['home_team']==team else g['home_team']}" for g in team_games]
gidx = st.selectbox("Game", range(len(team_games)), index=len(team_games) - 1, format_func=lambda i: labels[i])
game = team_games[gidx]
opponent_team = game["away_team"] if game["home_team"] == team else game["home_team"]

team_players = sorted({r["player_id"]: r["player_name"] for r in rows
                        if r["team"] == team and r["game_date"] < game["game_date"]}.items(),
                       key=lambda kv: kv[1])
if not team_players:
    st.info(f"No prior real player-game data for {team} before {game['game_date']}.")
    st.stop()

player_id = st.selectbox("Player", [pid for pid, _n in team_players],
                          format_func=lambda pid: dict(team_players)[pid], key="drill_player")

view = project_player_sog(rows, index, team_schedules, opponent_allowed_history, league_avg_sog_allowed,
                           weights, alpha, player_id, team, opponent_team, game["game_date"], game["season"])

if view["status"] != "PROJECTED_ACTIVE":
    st.info(f"Status: {view['status']}")
else:
    st.markdown(f"#### {dict(team_players)[player_id]} — {team} vs {opponent_team}")
    m1, m2, m3 = st.columns(3)
    m1.metric("Expected SOG", f"{view['expected_sog']:.2f}")
    m2.metric("Conservative expected SOG", f"{view['conservative_sog']:.2f}")
    m3.metric("Confidence", view["confidence"])

    st.markdown("**Full validated SOG distribution**")
    pcols = st.columns(6)
    for i, n in enumerate([1, 2, 3, 4, 5, 6]):
        pcols[i].metric(f"P({n}+)", f"{view['probs'][str(n)]*100:.1f}%",
                         help=f"conservative {view['conservative_probs'][str(n)]*100:.1f}%")

    st.markdown("**Model drivers vs. context**")
    d1, d2 = st.columns(2)
    with d1:
        st.markdown(f"{comp.label_badge('MODEL DRIVER', 'input')} TOI / role", unsafe_allow_html=True)
        st.caption(f"Recent (last-10) TOI: {view['recent_toi_minutes']:.1f} min/game" if view["recent_toi_minutes"] else "—")
        st.markdown(f"{comp.label_badge('MODEL DRIVER', 'input')} Head-to-head (shrunk)", unsafe_allow_html=True)
        st.caption(f"{view['h2h_games']} prior game(s) vs {opponent_team} — shrunk rate {view['h2h_rate']:.2f} SOG/game "
                   f"(small samples are heavily pulled toward the {team} baseline; see H2H_SHRINKAGE_GAMES=10)")
    with d2:
        st.markdown(f"{comp.label_badge('CONTEXT ONLY', 'unavailable')} Recent form (not a validated driver)", unsafe_allow_html=True)
        st.caption(f"Last-5 average: {view['recent_rate5']:.2f} SOG/game — fitted weight ~0; "
                   f"tested and found NOT to add credible incremental value (see report Section V)")
        st.markdown(f"{comp.label_badge('CONTEXT ONLY', 'unavailable')} Opponent shot environment (not a validated driver)", unsafe_allow_html=True)
        st.caption(f"{(view['opponent_factor']*100):.0f}% of league-average SOG allowed" if view["opponent_factor"] else "—",
                   )
        st.caption("Tested and found NOT to add credible incremental value (see report Section Y)")

    st.markdown(f"**LINEUP STATUS:** {comp.label_badge('PROJECTED / UNCONFIRMED', 'research')}", unsafe_allow_html=True)
    if view["confidence"] == "LOW":
        st.caption("LOW confidence — a live pricing decision on this player would be capped at WAIT, "
                   "regardless of any apparent edge (see decision policy in the report).")
    for r in view["confidence_risks"]:
        st.caption(f"Risk: {r}")
