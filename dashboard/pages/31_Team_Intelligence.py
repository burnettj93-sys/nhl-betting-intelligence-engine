"""Team Intelligence — one team's record, form, upcoming games, skaters and goalies, from observed results."""
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import streamlit as st

from dashboard import product_source as ps
from dashboard import ui

ui.header("Team Intelligence", "Record, recent results, upcoming schedule, skaters and goalies for one team.")
teams = ui.load(ps.teams, "Team data")
names = sorted(teams)
default = st.query_params.get("team") if st.query_params.get("team") in names else st.session_state.get("selected_team")
team = st.selectbox("Team", names, index=names.index(default) if default in names else 0, key="ti_team")
st.query_params["team"] = team
t = teams[team]
rec = t.get("record")
c = st.columns(5)
if rec:
    c[0].metric("Record (W-L-OTL)", f"{rec['w']}-{rec['l']}-{rec['otl']}", f"{rec['gp']} games", delta_color="off")
    c[1].metric("Goals for / against", f"{rec['gf']} / {rec['ga']}")
else:
    c[0].metric("Record", "no games yet")
c[2].metric("Strength rating", f"{t['strength_rating']:+.2f}", help="Decayed goal differential per game, shrunk toward zero — the input to the win-probability model.")
c[3].metric("Next game", f"{'vs' if t['upcoming'][0]['home'] else '@'} {t['upcoming'][0]['opp']}" if t["upcoming"] else "none scheduled")
c[4].metric("Last 5", " ".join(x["result"] for x in t["last5"]) or "—")

st.markdown("#### Recent results")
if t["last5"]:
    st.dataframe([{"Date": r["date_et"], "Opponent": ("vs " if r["home"] else "@ ") + r["opp"], "Result": r["result"], "Score": f"{r['gf']}–{r['ga']}"
                   + (f" ({r['period_type']})" if r["period_type"] in ("OT", "SO") else "")} for r in t["last5"]], hide_index=True, width="stretch")
else:
    st.caption("No regular-season games played yet this season.")
st.markdown("#### Upcoming")
if t["upcoming"]:
    st.dataframe([{"Date": r["date_et"], "Time": r["start_et"], "Opponent": ("vs " if r["home"] else "@ ") + r["opp"], "Game": r["game_id"]} for r in t["upcoming"]], hide_index=True, width="stretch")
else:
    st.caption("No upcoming regular-season games on file.")
st.markdown("#### Skaters (by recent ice time)")
if t["skaters"]:
    st.dataframe([{"Player": p["name"], "Pos": p["position"], "Line": str(p["line"]) if p["line"] else "—", "PP": f"PP{p['pp_unit']}" if p["pp_unit"] else "—", "GP": p["season"].get("games", 0),
                   "G": int(p["season"].get("goals") or 0), "A": int(p["season"].get("assists") or 0), "SOG": int(p["season"].get("shots") or 0),
                   "Recent TOI": round(p["toi_recent"], 1)} for p in t["skaters"]], hide_index=True, width="stretch")
    st.caption("Lines and power-play units are inferred from recent ice time; open Players for the source and games used.")
else:
    st.caption("No skater logs yet this season.")
st.markdown("#### Goalies")
if t["goalies"]:
    st.dataframe([{"Goalie": g["name"], "Start chance (est.)": ui.pct((g.get("start") or {}).get("probability")) if g.get("start") else "—", "Status": g["confirmation"]["status"].title(),
                   "W-L-OTL": (f"{g['season']['wins']}-{g['season']['losses']}-{g['season']['ot_losses']}" if (g.get("season") or {}).get("games") else "—"),
                   "SV%": f"{g['season']['save_pct']:.3f}" if (g.get("season") or {}).get("save_pct") is not None else "—"} for g in t["goalies"]], hide_index=True, width="stretch")
else:
    st.caption("No goalies on file.")
