"""Page 21 — Today: the real-data-first landing page (Real Product Bridge
block, 2026-09-29, superseding the earlier Same-Day Demo Experience
sprint's demo-first hierarchy). Order: System Health -> Live Model Edges
(real MONEYLINE) -> Recorded Recommendations (real, paper-tracked) ->
Today's Real Slate -> Top Conviction (real eligible legs) -> Daily
Real-Market Parlays (real, cross-game) -> Model Health links. Every
section is REAL OR EMPTY -- no section is ever backfilled with simulated
content. A Demo / Model Showcase (dashboard/demo_data.py's simulated
slate) is retained for illustrating the model/combo/parlay machinery, but
lives behind its own explicit, collapsed, clearly-labeled expander -- it
does not occupy the normal Today workflow."""
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import datetime as dt

import streamlit as st

from dashboard import cloud_snapshot
from dashboard import components as comp
from dashboard import conviction as cv
from dashboard import data_access as da
from dashboard import demo_data as dd
from dashboard import eligible_bets as eb
from dashboard import formatting as fmt
from dashboard import live_dk as ldk
from operational import runtime_mode
from operational.system_health import build_system_health, odds_collection_status
from research.game_edge_parlay import engine as gep
from research.generic_prop_pricing.provider_adapter import VERIFIED_CONTRACTS
from operational.live_readiness import live_readiness
from operational import prospective_ledger as pl

st.title("Today")
comp.render_model_status_header()
comp.render_global_search(key_prefix="today")

st.markdown("### System Health")
if runtime_mode.is_community_cloud():
    # Community Cloud: health is the LOCAL ENGINE's, as published in the snapshot
    # (never computed here -- the web process has no caches/DBs/schedulers to inspect).
    try:
        _cloud_health = cloud_snapshot.health_section()
        health = {i["key"]: {"status": i["status"], "label": i["label"], "message": i.get("message", "")}
                  for i in _cloud_health["items"]}
    except cloud_snapshot.SnapshotUnavailable:
        _cloud_health, health = {}, {}
else:
    _cloud_health = None
    health = build_system_health()
_dot = {"OK": "🟢", "STALE": "🟡", "WAITING": "🔵", "ERROR": "🔴", "NOT_REQUIRED": "⚪", "UNKNOWN": "⚫",
        "DEGRADED": "🟠"}
chip_html = "".join(
    f'<span style="display:inline-block; margin:3px 6px 3px 0; padding:4px 10px; '
    f'border-radius:999px; background:#1c2330; border:1px solid #262e3d; font-size:0.78rem;">'
    f'{_dot.get(item["status"], "⚫")} {item["label"]}: {item["status"]}</span>'
    for item in health.values()
)
st.markdown(chip_html, unsafe_allow_html=True)
failing = [item for item in health.values() if item["status"] == "ERROR"]
if failing:
    st.error(" · ".join(f"{item['label']}: {item['message']}" for item in failing))

_odds_status = (_cloud_health.get("odds_status") if runtime_mode.is_community_cloud() and _cloud_health
                else None) or odds_collection_status()
oc1, oc2, oc3, oc4, oc5, oc6 = st.columns(6)
# The published snapshot deliberately omits the volatile per-sweep `last_updated_utc` (it would make every snapshot
# look changed); in Cloud the newest real price time comes from the snapshot's odds freshness component instead.
_odds_last = _odds_status.get("last_updated_utc")
if not _odds_last and runtime_mode.is_community_cloud():
    _odds_last = ((cloud_snapshot.freshness().get("components") or {}).get("odds"))
oc1.metric("Odds last updated", (_odds_last or "—")[:16])
oc2.metric("Credits remaining", _odds_status.get("credits_remaining") if _odds_status.get("credits_remaining") is not None else "—")
_next_market_refresh_text, _next_t35_text = "scheduler-driven", None
if not runtime_mode.is_community_cloud():
    # Game-Day Moneyline Freshness block (2026-09-29), Part 12: "Next refresh" used to be a static,
    # uninformative "scheduler-driven" string that blurred two different concepts -- the ordinary
    # UI-freshness refresh and the T-35 decision-policy capture. Best-effort only: never let this
    # break Today (System Health above it already covers real failure states).
    try:
        from operational import cloud_snapshot_schema as _schema
        from operational import moneyline_freshness as _mf
        from operational import moneyline_pregame as _mp
        from zoneinfo import ZoneInfo as _ZoneInfo
        _edt = _ZoneInfo("America/New_York")
        _now_utc = dt.datetime.now(dt.timezone.utc)
        _mf_status = _mf.status(_now_utc)
        _next_ord = _schema.parse_utc(_mf_status["next_ordinary_refresh_utc"])
        _next_market_refresh_text = (_next_ord.astimezone(_edt).strftime("%-I:%M %p %Z") if _next_ord
                                      else "none scheduled today")
        _upcoming_clusters = [c for c in _mp.plan_clusters(_mp.scheduled_starts(_now_utc)) if c.target_pull >= _now_utc]
        if _upcoming_clusters:
            _next_t35_text = _upcoming_clusters[0].target_pull.astimezone(_edt).strftime("%-I:%M %p %Z")
    except Exception:  # noqa: BLE001 -- this metric is informational only
        _next_market_refresh_text, _next_t35_text = "scheduler-driven", None
oc3.metric("Next market refresh", _next_market_refresh_text)
oc4.metric("Verified DK contracts", len(VERIFIED_CONTRACTS))
oc5.metric("Player prop quotes", _odds_status.get("player_prop_quotes", "—"))
oc6.metric("Tracked events", _odds_status.get("tracked_events", "—"))
if _next_t35_text:
    st.caption(f"Next execution capture (T-35, decision-policy pull — separate from the ordinary "
               f"market refresh above): {_next_t35_text}")

with st.expander("Real NHL slate + Prospective Recording (technical detail)"):
    if runtime_mode.is_community_cloud():
        # Community Cloud memory sprint: the historical NHL corpus is never loaded
        # or walked from a page render in the thin presentation layer.
        st.caption("Real-corpus slate detail is not loaded in Community Cloud mode.")
    else:
        try:
            predictions = da.compute_baseline_predictions()
            dates = da.available_dates(predictions)
            today_str = dt.date.today().isoformat()
            todays_games = da.games_on_date(predictions, today_str) if today_str in dates else []
            if not todays_games:
                comp.render_empty_state("NO_GAMES", f"No real NHL games found in the corpus for {today_str}.")
            else:
                for g in todays_games:
                    readiness = live_readiness("PLAYER_SOG", game_id=g.get("game_id"))
                    st.caption(f"**{g['away_team']} @ {g['home_team']}** — SOG market readiness: {readiness['status']}")
        except da.DataAvailabilityError as exc:
            comp.render_missing_data_page(exc)

    st.markdown("**Prospective Recording**")
    if pl.DB_PATH.exists():
        _conn = pl.open_for_dashboard(pl.DB_PATH)
        _op = pl.operational_summary(_conn)
        p1, p2, p3 = st.columns(3)
        p1.metric("Model observations today", _op["recorded_today"])
        p2.metric("Pending settlement", _op["pending_settlement"])
        p3.metric("Last recorded", (_op["last_recorded_at_utc"] or "—")[:19])
    else:
        st.caption("No prospective observations recorded yet — the ledger is created automatically "
                   "on first real recording.")

st.divider()
st.markdown(
    """
    <div style="border:1px solid #1f4d2e; border-radius:6px; padding:8px 12px;
                background:#0f2417; color:#7fd99a; font-size:0.85rem; margin-bottom:12px;">
      <b>LIVE — REAL NHL SCHEDULE / REAL MARKET DATA.</b> Every game, price, and probability
      below comes from the real engine and real DraftKings data (or is clearly marked
      unavailable) — never a simulated substitute. A Demo / Model Showcase built on a
      simulated slate is available in its own collapsed section further down, clearly
      separated from this real workflow.
    </div>
    """,
    unsafe_allow_html=True,
)

# ---- 1. Today's real slate + real Top Conviction + real Daily Parlays -----
# Real Product Bridge block (2026-09-29): the ONE canonical real-data
# structure (dashboard/real_today_view.py) every section below reads from --
# real games, real eligible legs (research/real_market_parlay), real
# cross-game parlay result. REAL OR EMPTY: an honest empty state is shown
# wherever no real opportunity qualifies -- never backfilled with demo data.
try:
    if runtime_mode.is_community_cloud():
        _real_today = cloud_snapshot.real_today()
    else:
        # dashboard/*.py must never import db.py directly (it can trigger
        # schema migrations on a read-only presentation page), and must
        # never reference the Cloud publisher/builder module -- this
        # dedicated operational-layer function owns the real connection
        # lifecycle instead, and is the SAME function the published Cloud
        # snapshot's own "real_today" section calls, so LOCAL mode and
        # Cloud mode are never two separately-derived sources of truth.
        from operational import real_today_bridge
        _real_today = real_today_bridge.open_real_today_state()
except cloud_snapshot.SnapshotUnavailable as _exc:
    _real_today = None
    st.caption(f"Real Today data is not available in this snapshot ({_exc}).")

if _real_today is not None:
    st.markdown("## 1 · Today's Real Slate")
    st.caption(f"{_real_today['provenance']} — today's actual NHL games. Generated "
               f"{_real_today['generated_at_utc'][:16]}.")
    _real_games = _real_today["games"]
    if not _real_games:
        comp.render_empty_state("NO_GAMES", "No real NHL games found for today.")
    else:
        _rgame_cols = st.columns(2)
        for i, g in enumerate(_real_games):
            with _rgame_cols[i % 2]:
                with st.container(border=True):
                    st.markdown(f"**{g['away_team']} @ {g['home_team']}**")
                    st.caption(f"{g['scheduled_start_utc']} UTC · {g['game_state']}")
                    leg = g.get("strongest_leg")
                    if leg:
                        st.caption(f"Strongest real opportunity: {leg['participant_name']} "
                                   f"{leg['market_family']} {leg['threshold'] or ''} — "
                                   f"conservative P {fmt.format_probability(leg['conservative_probability'])} "
                                   f"({leg['data_label']})")
                    else:
                        st.caption("No qualifying real opportunity for this game right now.")
                    if st.button("Game Detail", key=f"real_game_{g['game_id']}", width="stretch"):
                        st.session_state["selected_game_id"] = g["game_id"]
                        st.switch_page("pages/2_Game_Detail.py")

    st.divider()
    st.markdown("## 2 · Top Conviction")
    st.caption("Ranked from real, currently eligible market legs (real DraftKings price + real model "
               "probability + real contract verification + real identity resolution + real freshness). "
               "A real single-sided market (e.g. PLAYER_SOG_ALTERNATE) is ranked by its own real "
               "conservative probability — a two-sided no-vig edge cannot be computed for a one-sided "
               "market, so this is never a workaround, it is the honest available signal.")
    _top = _real_today["top_conviction"]
    if not _top or isinstance(_top, str):
        comp.render_empty_state("NO_QUALIFYING_REAL_OPPORTUNITIES",
                                 "No real, currently eligible market opportunity qualifies right now — "
                                 "that is a real, honest result, not an error.")
    else:
        _tc_cols = st.columns(min(len(_top), 5))
        for col, leg in zip(_tc_cols, _top):
            with col:
                with st.container(border=True):
                    st.markdown(f"**{leg['participant_name']}**")
                    st.caption(f"{leg['market_family']} {leg['threshold'] or ''} · {leg['data_label']}")
                    st.metric("Conservative P", fmt.format_probability(leg["conservative_probability"]))
                    st.caption(f"Price {fmt.format_american_odds(leg['american_price'])} · "
                               f"{leg['sportsbook']}")
                    st.caption(f"Captured {leg.get('captured_at_utc') or 'unknown'}")

    st.divider()
    st.markdown("## 3 · Daily Real-Market Parlays")
    st.caption("Cross-game only (V1 forbids same-game combinations — no correlation model exists for "
               "that yet): 3-4 real eligible legs, conservative joint probability ≥70%, positive combo "
               "edge. A real combined DraftKings price does not exist for this and is never fabricated.")
    _parlay = _real_today["parlay"]
    if _parlay["status"] != "QUALIFIED":
        comp.render_empty_state("NO_QUALIFYING_REAL_PARLAY", _parlay.get("reason") or
                                 "No qualifying real-market parlay right now.")
    else:
        _combo = _parlay["combo"]
        _legs_desc = " + ".join(f"{l['participant_name']} {l['market_family']} {l['threshold'] or ''}"
                                for l in _combo["legs"])
        st.markdown(f"**{_combo['recommended_legs']}-leg:** {_legs_desc}")
        rpc1, rpc2, rpc3 = st.columns(3)
        rpc1.metric("Joint P", fmt.format_probability(_combo["joint_probability"]))
        rpc2.metric("Fair price", fmt.format_american_odds(_combo["fair_combo_price"]))
        rpc3.metric("Est. combo price", fmt.format_american_odds(_combo["estimated_combo_price"]))
        st.caption(f"Offered parlay price: {_combo['offered_parlay_price'] or 'NULL — not genuinely observed'}. "
                   f"{_combo['data_label']}.")

    if _real_today["excluded_count"]:
        with st.expander(f"Why {_real_today['excluded_count']} real candidate(s) were excluded"):
            for reason, count in sorted(_real_today["excluded_by_reason"].items(), key=lambda kv: -kv[1]):
                st.caption(f"{count} — {reason}")

# ---- 0. Live Model Edges (real DraftKings, when a verified contract exists) ----
# SNAPSHOT freshness and MARKET freshness are separate: each price is judged on its own capture time
# (<= 3 h, or <= 90 min when its game starts within 4 h); a stale snapshot only makes it stricter.
_live_rows = ldk.build_live_moneyline_comparisons()
_live_priced = [(r, comp.market_freshness(r)) for r in _live_rows if r.get("status") == "PRICED"]
if _live_priced:
    _any_current = any(f["state"] == "CURRENT" for _, f in _live_priced)
    st.markdown("## Live Model Edges" if _any_current else "## Model Edges — ODDS STALE (not live)")
    _snap_state = _live_priced[0][1]["snapshot_state"]
    st.caption(f"{comp.live_label(ldk.LIVE_SOURCE_LABEL)} — real DraftKings MONEYLINE prices, captured via a real "
               f"Odds API pull and compared against this engine's real Elo win model. This is not simulated. "
               f"SNAPSHOT FRESHNESS: {_snap_state.replace('_', ' ')} (separate from each price's own freshness below).")
    for r, _f in sorted(_live_priced, key=lambda rf: -abs(rf[0].get("raw_edge") or 0.0))[:6]:
        lc1, lc2, lc3, lc4 = st.columns([2, 1, 1, 1])
        lc1.markdown(f"**{r['side']}** ({r['away_team']} @ {r['home_team']} moneyline)")
        lc2.caption(f"Model {fmt.format_probability(r['model_probability'])}")
        lc3.caption(f"Edge {fmt.format_edge(r['raw_edge'])}")
        # A price that is not CURRENT never shows an actionable badge (the stored decision is unchanged).
        lc4.markdown(comp.label_badge(r["decision"] if _f["state"] == "CURRENT" else "STALE", "input"),
                     unsafe_allow_html=True)
        if r["decision"] == "WAIT" and r.get("elo_staleness_days"):
            st.caption(f"⚠ Elo rating is {r['elo_staleness_days']:.0f} days stale for this game -- "
                       f"real edge, not presented as actionable. {r['decision_reason']}")
        st.caption(f"Captured {r['captured_at_utc']} · DK price {fmt.format_american_odds(r['current_odds'])} "
                   f"· Fair {fmt.format_american_odds(r['fair_odds'])}")
        st.caption(comp.market_freshness_text(_f))

# ---- 0b. Recorded recommendations (real market, paper-tracked) --------------------
st.markdown("## Recorded Recommendations")
try:
    if runtime_mode.is_community_cloud():
        _recs = cloud_snapshot.real_recommendations()
    else:
        from dashboard import real_recommendations_view as _rrv
        _recs = {"provenance": "REAL MARKET", "moneyline": _rrv.real_moneyline_recommendations()[-300:],
                 "props": _rrv.real_prop_recommendations_for_conviction_and_parlay()[-300:],
                 "game_edge_parlays": []}
except cloud_snapshot.SnapshotUnavailable as _exc:
    _recs = None
    st.caption(f"Recorded recommendations are not available in this snapshot ({_exc}).")
if _recs is not None:
    st.caption(f"{_recs['provenance']} — recommendations the real engine recorded against real prices "
               f"(paper-tracked, never real money). Kept separate from the simulated demo board below."
               + f"  SNAPSHOT FRESHNESS: {comp.live_data_state().replace('_', ' ')}. Each row's own MARKET FRESHNESS "
               f"is shown below; the recorded action is never altered by staleness.")
    if _recs["moneyline"]:
        st.dataframe([{"Game": f"{r.get('opponent')} @ {r.get('team')}" if r.get("side") == r.get("team") else r.get("team"),
                       "Side": r.get("side"), "Status": r.get("prospective_status"),
                       "Model P": fmt.format_probability(r["conservative_probability"])
                       if r.get("conservative_probability") is not None else "—",
                       "Odds": fmt.format_american_odds(r["odds_american"]) if r.get("odds_american") else "—",
                       "Confidence": r.get("confidence"), "Recorded": (r.get("created_at_utc") or "")[:16],
                       "Price captured": (_f["market_captured_at"] or "")[:16],
                       "Game start": (_f["game_start_utc"] or "")[:16],
                       "Market freshness": _f["state"],
                       "Shown as": r.get("prospective_status") if _f["state"] == "CURRENT" else "STALE",
                       "Source": r.get("source")}
                      for r in _recs["moneyline"][-30:] for _f in [comp.market_freshness(r)]], width="stretch")
    else:
        st.caption("No real-market MONEYLINE recommendation has been recorded yet.")
    if _recs["props"]:
        st.dataframe([{"Player": o.get("player"), "Market": o.get("market"), "Threshold": o.get("threshold"),
                       "Decision": o.get("decision") if comp.market_freshness(o)["state"] == "CURRENT" else "STALE",
                       "Market freshness": comp.market_freshness(o)["state"],
                       "Price captured": (comp.market_freshness(o)["market_captured_at"] or "")[:16],
                       "Model P": fmt.format_probability(o["coherent_probability"])
                       if o.get("coherent_probability") is not None else "—"} for o in _recs["props"][-30:]],
                     width="stretch")
    for _p in _recs.get("game_edge_parlays", []):
        _legs = " + ".join(f"{l.get('player')} {l.get('market')} {l.get('threshold')}" for l in _p["combo"]["legs"])
        _pf = comp.parlay_freshness(_p["combo"]["legs"])
        if _pf["state"] == "CURRENT":
            st.markdown(f"**Real Game Edge Parlay — {_p['opponent']} @ {_p['team']}:** {_legs}")
        else:
            st.markdown(f"**Game Edge Parlay — {_p['opponent']} @ {_p['team']} — {_pf['state'].replace('_', ' ')} "
                        f"(not a current opportunity):** {_legs}")
        if _pf["limiting_leg"]:
            st.caption("Parlay freshness = its stalest leg. " + comp.market_freshness_text(_pf["limiting_leg"]))

# ---- Demo / Model Showcase (SIMULATED) -------------------------------------
# Complete Market Validation / Live UI Certification sprint (2026-09-29), then
# Real Product Bridge block (2026-09-29): this entire showcase is now behind
# an explicit, collapsed, clearly-labeled toggle -- it no longer occupies the
# normal Today workflow (the real sections above are the primary product).
# No data, matching, or downstream logic changed inside it.
st.divider()
with st.expander("Demo / Model Showcase — SIMULATED, not the real product (click to expand)"):
    st.caption("Everything below uses a fixed SIMULATED slate to illustrate the model/combo/parlay "
               "machinery. Never DraftKings, never live/verified. For the real product, see the "
               "sections above.")
    st.markdown("### Demo Slate — SIMULATED")
    opportunities = eb.all_opportunities()
    best_by_game: dict[str, dict] = {}
    for o in opportunities:
        if not o.get("actionable", True) or o["decision"] not in ("BET", "WATCH"):
            continue
        key = tuple(sorted((o["team"], o["opponent"])))
        cur = best_by_game.get(key)
        score = cv.conviction_score(o)
        if cur is None or score > cur[0]:
            best_by_game[key] = (score, o)

    game_cols = st.columns(2)
    for i, g in enumerate(dd.build_demo_games()):
        key = tuple(sorted((g.away, g.home)))
        strongest = best_by_game.get(key)
        with game_cols[i % 2]:
            with st.container(border=True):
                st.markdown(f"**{g.away} @ {g.home}**")
                st.caption(f"{g.start_time} · SIMULATED — DEMO ONLY (not today's real schedule) · "
                           f"Model: {g.model_ready} · Starters: {g.starter_ready}")
                if strongest:
                    _, o = strongest
                    st.caption(f"Strongest: {o['player']} {o['market']} {o['threshold']} — "
                               f"{fmt.format_probability(o['conservative_probability'])} conservative, "
                               f"{o['decision']}")
                if st.button("Game Detail", key=f"today_game_{g.game_id}", width="stretch"):
                    st.session_state["selected_game_id"] = g.game_id
                    st.switch_page("pages/2_Game_Detail.py")
                gc2, gc3 = st.columns(2)
                if gc2.button(f"{g.away} Hub", key=f"today_away_{g.game_id}", width="stretch"):
                    st.session_state["selected_team"] = g.away
                    st.switch_page("pages/31_Team_Intelligence.py")
                if gc3.button(f"{g.home} Hub", key=f"today_home_{g.game_id}", width="stretch"):
                    st.session_state["selected_team"] = g.home
                    st.switch_page("pages/31_Team_Intelligence.py")

    st.markdown("### Demo Top Conviction")
    top = cv.top_conviction(opportunities)
    if not top:
        comp.render_empty_state("NO_QUALIFYING_OPPORTUNITIES",
                                 "No opportunity on today's simulated slate clears the Top Conviction bar "
                                 "right now — that's a real, honest result, not an error.")
    else:
        cols = st.columns(min(len(top), 5))
        for col, o in zip(cols, top):
            with col:
                with st.container(border=True):
                    if st.button(o["player"], key=f"conv_{o['player_id']}_{o['prop']}_{o['threshold']}"):
                        st.session_state["selected_player_id"] = o["player_id"]
                        st.switch_page("pages/25_Player_Intelligence.py")
                    st.caption(f"{o['market']} {o['threshold']} · SIMULATED — DEMO ONLY")
                    st.metric("Model", fmt.format_probability(o["coherent_probability"]))
                    st.caption(f"Conservative {fmt.format_probability(o['conservative_probability'])}")
                    st.caption(f"Fair {fmt.format_american_odds(o['fair_odds'])} · "
                               f"Sim. Market {fmt.format_american_odds(o['current_odds'])}")
                    st.caption(f"Edge {fmt.format_edge(o['conservative_edge'])} · EV {fmt.format_ev(o['ev'])}")
                    st.markdown(comp.label_badge(o["decision"], "input"), unsafe_allow_html=True)
                    st.caption(f"Confidence: {o['confidence']}")

    st.markdown("### Demo High-Confidence Combos")
    combo_board = cv.build_combo_board(opportunities)

    def _render_combo(c: dict) -> None:
        with st.container(border=True):
            legs_desc = " + ".join(f"{l['player']} {l['market']} {l['threshold']}" for l in c["legs"])
            st.markdown(f"**{legs_desc}**")
            for l in c["legs"]:
                st.caption(f"{l['player']} {l['market']} {l['threshold']} — marginal P "
                           f"{fmt.format_probability(l['coherent_probability'])}, conservative P "
                           f"{fmt.format_probability(l['conservative_probability'])}, fair "
                           f"{fmt.format_american_odds(l['fair_odds'])}, current "
                           f"{fmt.format_american_odds(l['current_odds'])}, edge "
                           f"{fmt.format_edge(l['raw_edge'])}")
            cc1, cc2, cc3, cc4 = st.columns(4)
            cc1.metric("Joint P", fmt.format_probability(c["joint_probability"]))
            cc2.metric("Fair combo price", fmt.format_american_odds(c["fair_combo_price"]))
            cc3.metric("Sim. combo price", fmt.format_american_odds(c["simulated_combo_price"]))
            cc4.metric("Combo edge", fmt.format_edge(c["combo_edge"]))
            st.caption(f"Dependency: {c['pairwise'][0]['method']}")

    if not combo_board["high_confidence"]:
        comp.render_empty_state(
            "NO_QUALIFYING_OPPORTUNITIES",
            "No combo on today's simulated slate clears the HIGH-CONFIDENCE bar (every leg "
            "individually >= 65% conservative probability with real positive value, plus real "
            "positive combined value) — that's a real, honest result, not an error. See Value "
            "Combinations below for what does exist today.")
    else:
        for c in combo_board["high_confidence"]:
            _render_combo(c)

    if combo_board["value"]:
        with st.expander(f"Value Combinations — {len(combo_board['value'])} combo(s) with real "
                          f"joint-dependence support but not individually high-probability "
                          f"favorites (not HIGH-CONFIDENCE)"):
            for c in combo_board["value"]:
                _render_combo(c)

    if combo_board["research"]:
        with st.expander(f"Research combinations — {len(combo_board['research'])} combo(s) with "
                          f"unsupported dependence (not actionable)"):
            for c in combo_board["research"]:
                legs_desc = " + ".join(f"{l['player']} {l['market']} {l['threshold']}" for l in c["legs"])
                st.caption(f"{legs_desc} — JOINT DEPENDENCE NOT VALIDATED")

    st.markdown("### Demo Game Parlays")
    st.caption("One card per game — a 3-4 leg Game Edge Parlay only where one genuinely qualifies. "
               "Never manufactured: most games on most nights correctly show NO QUALIFYING PARLAY.")
    for g in dd.build_demo_games():
        parlay_result = gep.build_game_edge_parlay(opportunities, g.away, g.home)
        with st.container(border=True):
            st.markdown(f"**{g.away} @ {g.home}**")
            if parlay_result["status"] == "NO_QUALIFYING_GAME_EDGE_PARLAY":
                comp.render_empty_state("NO_QUALIFYING_GAME_EDGE_PARLAY", parlay_result["reason"])
            else:
                combo = parlay_result["combo"]
                legs_desc = " + ".join(f"{l['player']} {l['market']} {l['threshold']}" for l in combo.legs)
                st.markdown(f"**{parlay_result['recommended_legs']}-leg:** {legs_desc}")
                pc1, pc2, pc3, pc4 = st.columns(4)
                pc1.metric("Joint P", fmt.format_probability(combo.joint_probability))
                pc2.metric("Fair price", fmt.format_american_odds(combo.fair_combo_price))
                pc3.metric("Est. combo price", fmt.format_american_odds(combo.estimated_combo_price))
                pc4.metric("Edge", fmt.format_edge(combo.combo_edge))
                st.caption("Estimated from individual leg prices — never presented as a real DraftKings "
                           "parlay quote (no live SGP price has been observed).")
                calibration = gep.calibration_snapshot(combo)
                st.caption(f"Target ≈{calibration['target_joint_probability']:.0%} · "
                           f"gap to target {calibration['gap_to_target']:+.1%}")

    st.markdown("### Demo Best Player Props")
    player_props = sorted(
        [o for o in opportunities if o["entity_kind"] == "PLAYER" and o.get("actionable", True)
         and o["decision"] in ("BET", "WATCH")],
        key=lambda o: -cv.conviction_score(o))[:8]
    if not player_props:
        st.caption("No actionable player props on today's simulated slate.")
    else:
        st.dataframe([{"Player": o["player"], "Team": o["team"], "Market": o["market"],
                       "Threshold": o["threshold"], "Model P": fmt.format_probability(o["coherent_probability"]),
                       "Edge": fmt.format_edge(o["conservative_edge"]), "Action": o["decision"]}
                      for o in player_props], width='stretch')

    st.markdown("### Demo Best Team Bets")
    st.caption("Team SOG has no live demo projection wired this sprint — shown as real historical "
               "context on each Team Hub's Overview tab, not as a priced bet here. Moneyline is not "
               "wired to a live demo projection for the simulated slate either — see the Model Learning "
               "page's own honest limitations.")

    st.markdown("### Demo Goalie Opportunities")
    goalie_opps = sorted([o for o in eb.build_goalie_saves_opportunities() if o["actionable"]
                          and o["decision"] in ("BET", "WATCH")], key=lambda o: -cv.conviction_score(o))
    if not goalie_opps:
        st.caption("No actionable goalie saves opportunity on today's simulated slate.")
    else:
        for o in goalie_opps[:5]:
            gcol1, gcol2, gcol3 = st.columns([2, 1, 1])
            gcol1.markdown(f"**{o['player']}** ({o['team']}) — {o['threshold']} saves")
            gcol2.caption(f"Model {fmt.format_probability(o['coherent_probability'])}")
            gcol3.markdown(comp.label_badge(o["decision"], "input"), unsafe_allow_html=True)

# ---- Model Health -------------------------------------------------------
st.divider()
st.markdown("## Model Health")
mh1, mh2, mh3 = st.columns(3)
if mh1.button("Open Model Health"):
    st.switch_page("pages/22_Model_Health.py")
if mh2.button("Open Model Learning"):
    st.switch_page("pages/32_Model_Learning.py")
if mh3.button("Open Paper Performance"):
    st.switch_page("pages/33_Paper_Performance.py")

comp.render_provenance_panel()
