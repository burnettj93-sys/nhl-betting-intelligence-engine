"""Tomorrow — the next Eastern day's games and whatever early DraftKings prices exist. Early prices are shown with their age and are marked STALE when they are old or were
fetched on an earlier day; player props are usually not posted a day ahead, and this page says so (and says when that was last checked) instead of leaving a blank."""
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

ui.header("Tomorrow", "Tomorrow's games and any early DraftKings prices. Nothing here is recorded or can be added to a log.")
data = ui.load(ps.games, "The schedule")
tk = ui.load(ps.tickets, "The ticket board")
today = data["et_today"]
tomorrow = (dt.date.fromisoformat(today) + dt.timedelta(days=1)).isoformat()
pool = [g for g in data["games"] if g["type"] == "REGULAR" and g["season"] == "20262027"]
dates = sorted({g["date_et"] for g in pool if g["date_et"] > today})
if not dates:
    st.info("No later regular-season game days are on file yet.")
    st.stop()
date = tomorrow if tomorrow in dates else dates[0]
if date != tomorrow:
    ui.banner(f"No games are scheduled for {tomorrow}. The next game day is shown instead: {date}.", "info")
day = sorted((g for g in pool if g["date_et"] == date), key=lambda g: (g["start_utc"] or "", g["game_id"]))
st.subheader(f"{dt.date.fromisoformat(date):%A, %B %-d, %Y} — {len(day)} game(s)")

av = ui.availability_block(tk)
tom = av.get("tomorrow") or {}
sched = av.get("schedule") or {}
last = av.get("tomorrow_last_check_utc")
st.markdown("**Player props for these games**")
if date == tomorrow and tom.get("games"):
    ui.banner(ui.esc(tom.get("sentence") or ""), "info")
    st.caption(f"The engine asks DraftKings once each evening (after {sched.get('tomorrow_check_et', '20:15')} ET) whether it has posted tomorrow's player prices; last check "
               f"{ui.et_time(last, True) if last else 'not yet'}. Asking about a market that is not posted costs no credit, so this check is nearly free until DraftKings posts. "
               "From 8:00 AM ET on the game day the normal morning look takes over.")
    ui.availability_table(tom)
else:
    st.caption("DraftKings has not been asked about these games yet: the first check is the evening before (after "
               f"{sched.get('tomorrow_check_et', '20:15')} ET). DraftKings has so far posted player props on the morning of the game, not a day ahead (docs/MORNING_WORKFLOW.md).")

st.markdown("**Games and early moneylines**")
n_stale = 0
rows = []
for g in day:
    text, stale = ui.early_moneyline_text(g)
    n_stale += int(stale)
    wp = (g.get("win_probability") or {}).get("home")
    rows.append({"Time": g["start_et"], "Game": f"{g['away']} @ {g['home']}", "Model home win": ui.pct(wp), "DK moneyline (away / home)": text})
if n_stale:
    ui.banner(f"<b>{n_stale} of {len(day)} early moneyline prices are stale</b> (older than {ui.EARLY_MAX_AGE_MIN / 60:.0f} hours, or fetched on an earlier day). They are shown as the last quote only; "
              "the next league-wide moneyline pull refreshes them.", "warn")
st.dataframe(rows, hide_index=True, width="stretch")
st.caption("Early moneylines come from the same league-wide DraftKings pull as today's (one credit, however many games it returns): the morning pull, the display refresh, and the pre-game decision pulls. "
           "A game with no price has not been posted by DraftKings yet. Prices are US-feed quotes, not verified for Ontario, and are never used for a ticket until the day of the game.")
