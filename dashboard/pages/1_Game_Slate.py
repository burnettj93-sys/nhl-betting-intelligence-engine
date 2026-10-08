"""Games — every game on one Eastern-time date. The date defaults to today (or the next game day); earlier and later dates are
reached with the date picker, and a different season only through the season filter. Selecting a game opens Game Detail for
exactly that game."""
from __future__ import annotations

import datetime as dt
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import streamlit as st

from dashboard import product_source as ps
from dashboard import ui

ui.header("Games", "Games by Eastern-time date. Open a game for lineups, goalies and projections.")
data = ui.load(ps.games, "The schedule")
all_games = data["games"]

seasons = sorted({g["season_label"] for g in all_games}, reverse=True)
current = next((g["season_label"] for g in all_games if g["season"] == "20262027"), seasons[0])
f1, f2, f3 = st.columns([2, 2, 3])
season = f1.selectbox("Season", seasons, index=seasons.index(current), key="games_season",
                      help="The current season is the default. Another season appears only if you pick it.")
kinds = sorted({g["type"] for g in all_games if g["season_label"] == season})
kind = f2.selectbox("Game type", kinds, index=kinds.index("REGULAR") if "REGULAR" in kinds else 0, key="games_kind",
                    format_func=lambda k: {"REGULAR": "Regular season", "PRESEASON": "Preseason", "PLAYOFF": "Playoffs"}.get(k, k.title()))
pool = [g for g in all_games if g["season_label"] == season and g["type"] == kind]
dates = sorted({g["date_et"] for g in pool})
if not dates:
    ui.unavailable(f"No {kind.lower()} games are on file for {season}.", "This list")
    st.stop()

default = data["default_date"] if data["default_date"] in dates else (dates[-1] if season != current else dates[0])
if "games_date" not in st.session_state or st.session_state["games_date"] not in dates:
    st.session_state["games_date"] = default
idx = dates.index(st.session_state["games_date"])
n1, n2, n3 = f3.columns([1, 3, 1])
if n1.button("◀", key="games_prev", disabled=idx == 0, help="Previous game day"):
    st.session_state["games_date"] = dates[idx - 1]
    st.rerun()
if n3.button("▶", key="games_next", disabled=idx == len(dates) - 1, help="Next game day"):
    st.session_state["games_date"] = dates[idx + 1]
    st.rerun()
chosen = n2.selectbox("Date (Eastern)", dates, index=idx, key="games_date_box", label_visibility="collapsed")
if chosen != st.session_state["games_date"]:
    st.session_state["games_date"] = chosen
    st.rerun()
date = st.session_state["games_date"]

day = sorted((g for g in pool if g["date_et"] == date), key=lambda g: (g["start_utc"] or "", g["game_id"]))
tag = "today" if date == data["et_today"] else ("in the future" if date > data["et_today"] else "in the past")
st.subheader(f"{dt.date.fromisoformat(date):%A, %B %-d, %Y} — {len(day)} game(s)")
st.caption(f"This date is {tag} (today is {data['et_today']} Eastern). Game days follow the Eastern calendar, so a late game that starts after midnight UTC stays on its Eastern date.")

for g in day:
    with st.container(border=True):
        c = st.columns([3, 2, 3, 2])
        c[0].markdown(f"**{g['away']} @ {g['home']}**")
        c[0].caption(f"{g['start_et'] or 'time n/a'} · {g['type'].title()} · game {g['game_id']}")
        c[0].markdown(ui.status_chip(g["state"]), unsafe_allow_html=True)
        if g["state"] == "FINAL":
            suffix = f" ({g['period_type']})" if g.get("period_type") in ("OT", "SO") else ""
            c[1].metric("Final", f"{g['away']} {g['away_score']} – {g['home']} {g['home_score']}{suffix}")
        elif g.get("win_probability"):
            wp = g["win_probability"]
            c[1].metric("Model win chance", f"{g['home']} {ui.pct(wp['home'])}", f"{g['away']} {ui.pct(wp['away'])}", delta_color="off")
        else:
            c[1].caption("In progress or awaiting the final result.")
        ml = g.get("moneyline")
        if ml and ml.get("home") and ml.get("away"):
            c[2].markdown(f"DraftKings: **{g['away']} {ui.american(ml['away']['american'])}** / **{g['home']} {ui.american(ml['home']['american'])}**")
            c[2].caption(f"Quote captured {ui.et_time(ml['home']['quote_captured_at_utc'], True)} ({ui.age_text(ml['home']['quote_captured_at_utc'])}). US feed.")
        elif g["state"] == "SCHEDULED":
            c[2].caption("No DraftKings moneyline quote on file.")
        goalies = g.get("goalies") or {}
        if g["state"] == "FINAL" and goalies.get("home"):
            c[2].caption(f"Starters: {goalies['away']['name']} ({g['away']}), {goalies['home']['name']} ({g['home']})" if goalies.get("away") else "")
        elif g["state"] == "SCHEDULED" and goalies.get("home"):
            h, a = goalies["home"][0], goalies["away"][0] if goalies.get("away") else None
            c[2].caption("Likeliest goalies (unconfirmed estimate): " + f"{h['name']} {ui.pct(h['start_probability'])} ({g['home']})"
                         + (f", {a['name']} {ui.pct(a['start_probability'])} ({g['away']})" if a else ""))
        if g.get("tickets"):
            c[3].caption("On tickets: " + ", ".join(g["tickets"]))
        if c[3].button("Open game", key=f"open_{g['game_id']}"):
            st.session_state["selected_game_id"] = g["game_id"]
            st.query_params["game"] = g["game_id"]
            st.switch_page("pages/2_Game_Detail.py")
