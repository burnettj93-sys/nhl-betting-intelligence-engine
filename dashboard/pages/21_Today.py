"""Page 21 — Today: the real operational command center (Preseason
Operationalization sprint, Section 38-40). Uses actual system health and
live-readiness data -- no demo game cards, no fabricated prices. When
there are no real games, or no real live markets, this page says so
plainly rather than showing anything fake."""
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import datetime as dt

import streamlit as st

from dashboard import components as comp
from dashboard import data_access as da
from dashboard import demo_data as dd
from operational.system_health import build_system_health
from operational.live_readiness import live_readiness
from operational import prospective_ledger as pl

st.title("Today")
comp.render_model_status_header()
comp.render_global_search(key_prefix="today")

st.markdown("### System Health")
health = build_system_health()
_dot = {"OK": "🟢", "STALE": "🟡", "WAITING": "🔵", "ERROR": "🔴", "NOT_REQUIRED": "⚪", "UNKNOWN": "⚫"}
# 13 real components don't fit legibly in one row of st.columns at any
# reasonable width (Preseason Closing sprint, Track 8: cramped-column
# fix) -- render as wrapping chips instead.
chip_html = "".join(
    f'<span style="display:inline-block; margin:3px 6px 3px 0; padding:4px 10px; '
    f'border-radius:999px; background:#1c2330; border:1px solid #262e3d; font-size:0.78rem;">'
    f'{_dot.get(item["status"], "⚫")} {item["label"]}: {item["status"]}</span>'
    for item in health.values()
)
st.markdown(chip_html, unsafe_allow_html=True)

# Section AN: one clear global banner per failing major dependency, not
# repeated per-page.
failing = [item for item in health.values() if item["status"] == "ERROR"]
if failing:
    st.error(" · ".join(f"{item['label']}: {item['message']}" for item in failing))

st.divider()

try:
    predictions = da.compute_baseline_predictions()
    dates = da.available_dates(predictions)
except da.DataAvailabilityError as exc:
    comp.render_missing_data_page(exc)
    st.stop()

today_str = dt.date.today().isoformat()
todays_games = da.games_on_date(predictions, today_str) if today_str in dates else []

st.markdown("### Today's Slate")
if not todays_games:
    comp.render_empty_state("NO_GAMES", f"No real NHL games found in the corpus for {today_str}.")
else:
    for g in todays_games:
        with st.container(border=True):
            st.markdown(f"**{g['away_team']} @ {g['home_team']}**")
            readiness = live_readiness("PLAYER_SOG", game_id=g.get("game_id"))
            st.caption(f"SOG market readiness: {readiness['status']}"
                       + (f" — {readiness['message']}" if readiness["status"] != "READY" else ""))

st.divider()
st.markdown("### Prospective Recording")
if pl.DB_PATH.exists():
    _conn = pl.init_db(pl.DB_PATH)
    _op = pl.operational_summary(_conn)
    _summary = pl.summary_metrics(_conn)
    p1, p2, p3, p4 = st.columns(4)
    p1.metric("Model observations today", _op["recorded_today"])
    p2.metric("Shadow observations", _summary["SHADOW_POLICY_OBSERVATION"]["n"])
    p3.metric("Pending settlement", _op["pending_settlement"])
    p4.metric("Last recorded", (_op["last_recorded_at_utc"] or "—")[:19])
else:
    st.caption("No prospective observations have been recorded yet. The ledger database is created "
               "automatically the first time a prediction is recorded — see Ledger.")

st.divider()
st.markdown("### Demo Slate")
st.caption(dd.DEMO_MODE_LABEL)
for g in dd.build_demo_games():
    with st.container(border=True):
        gc1, gc2 = st.columns([3, 1])
        with gc1:
            st.markdown(f"**{g.away} @ {g.home}** · {g.start_time}")
            st.caption(f"Model: {g.model_ready} · Starters: {g.starter_ready} · Market: {g.market_ready}")
        with gc2:
            if st.button("Open Game Detail", key=f"today_game_{g.game_id}"):
                st.session_state["selected_game_id"] = g.game_id
                st.switch_page("pages/2_Game_Detail.py")

st.divider()
st.markdown("### Top Actionable Opportunities")
comp.render_empty_state("NO_LIVE_MARKETS",
                         "No live sportsbook markets are currently connected for today's slate. "
                         "Only Player SOG has a live-tested DraftKings payload contract "
                         "(see Live SOG Markets) — every other family will show real opportunities "
                         "here once its own live pricing contract is verified.")

st.divider()
st.markdown("### Waiting on Data / Confirmation")
waiting_items = [item for item in health.values() if item["status"] in ("WAITING", "STALE")]
if not waiting_items:
    st.caption("Nothing is currently waiting on data.")
else:
    for item in waiting_items:
        st.markdown(f"- **{item['label']}** ({item['status']}): {item['message']}")

comp.render_provenance_panel()
