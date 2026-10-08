"""Players — current-season and recent usage and production for every skater who has played this season, the role each is
inferred to have (line, power-play unit) with its source and timestamp, matchup-specific expected values for the next game,
and the best qualifying +100 option when one exists. History is shown as history; projections are labelled projections."""
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import streamlit as st

from dashboard import product_source as ps
from dashboard import ui

ui.header("Players", "Skaters who have played this season. Pick a player for the full picture.")
players = ui.load(ps.players, "Player data")
meta = ps.meta()
try:
    tk = ps.tickets()
    opts = {o["option_id"]: o for o in ps.options().get("options", [])}
    cash = tk["account"]["available_cash"]
    page_ts = tk["generated_at_utc"]
except ps.Unavailable:
    opts, cash, page_ts = {}, None, None

teams = sorted({p["team"] for p in players.values()})
f = st.columns([3, 2, 2, 2])
q = f[0].text_input("Search by name", key="pl_q")
team = f[1].selectbox("Team", ["All teams"] + teams, key="pl_team")
pos = f[2].selectbox("Position", ["All", "Forwards", "Defense"], key="pl_pos")
sort = f[3].selectbox("Sort by", ["Recent ice time", "Season points", "Season shots", "Name"], key="pl_sort")

rows = list(players.values())
if q:
    rows = [p for p in rows if q.lower() in p["name"].lower()]
if team != "All teams":
    rows = [p for p in rows if p["team"] == team]
if pos != "All":
    want = "F" if pos == "Forwards" else "D"
    rows = [p for p in rows if (p["position"] == "D") == (want == "D")]
key = {"Recent ice time": lambda p: -p["recent_avg"]["toi"], "Season points": lambda p: -(p["season"].get("points") or 0),
       "Season shots": lambda p: -(p["season"].get("shots") or 0), "Name": lambda p: p["name"]}[sort]
rows.sort(key=key)
st.caption(f"{len(rows)} of {len(players)} skaters. Season = {meta['current_season'][:4]}-{meta['current_season'][6:]} regular season to date; recent = last up to 6 games.")

table = [{"Player": p["name"], "Team": p["team"], "Pos": p["position"], "Est. usage tier": ui.est_tier(p), "Est. PP usage": ui.est_pp(p), "Reported line": ui.reported_line(p), "Reported PP": ui.reported_pp(p),
          "GP": p["season"].get("games", 0), "G": int(p["season"].get("goals") or 0), "A": int(p["season"].get("assists") or 0),
          "Pts": int(p["season"].get("points") or 0), "SOG": int(p["season"].get("shots") or 0),
          "TOI": round(p["recent_avg"]["toi"], 1), "Option": "yes" if p.get("option_id") in opts else ""} for p in rows[:400]]
ev = st.dataframe(table, hide_index=True, width="stretch", on_select="rerun", selection_mode="single-row", key="pl_table", height=320)
ids = [p["player_id"] for p in rows[:400]]
sel = None
if ev and ev.selection.rows:
    sel = ids[ev.selection.rows[0]]
elif st.query_params.get("player") in players:
    sel = st.query_params["player"]
if len(rows) > 400:
    st.caption("Showing the first 400; narrow the filters to see the rest.")
if not sel:
    st.info("Select a row to open that player.")
    st.stop()

st.query_params["player"] = sel
p = players[sel]
st.divider()
st.subheader(f"{p['name']} — {p['team']} · {p['position']}")
s = p["season"]
n = max(s.get("games", 0), 1)
a = st.columns(6)
a[0].metric("Games", s.get("games", 0))
a[1].metric("Goals / Assists", f"{int(s.get('goals') or 0)} / {int(s.get('assists') or 0)}")
a[2].metric("Points", int(s.get("points") or 0), f"{(s.get('points') or 0) / n:.2f} per game", delta_color="off")
a[3].metric("Shots on goal", int(s.get("shots") or 0), f"{(s.get('shots') or 0) / n:.2f} per game", delta_color="off")
a[4].metric("Hits / Blocks", f"{int(s.get('hits') or 0)} / {int(s.get('blocks') or 0)}")
a[5].metric("Avg ice time", f"{s.get('toi_avg', 0):.1f} min", f"PP {s.get('toi_pp_avg', 0):.1f} min", delta_color="off")

st.markdown("#### Reported lineup (from a lineup source)")
rep = p.get("reported")
if rep:
    bits = [f"Line/pair **{rep['line']}**" if rep.get("line") else "no forward line or pair listed",
            f"power play **{rep['pp']}**" if rep.get("pp") else "no power-play unit listed"]
    if rep.get("pk"):
        bits.append(f"penalty kill **{rep['pk']}**")
    st.write(" · ".join(bits))
    if rep.get("injury_status") or rep.get("game_time_decision"):
        st.warning(f"Injury status on the report: {rep.get('injury_status') or 'game-time decision'}.")
    link = f" — [{rep['reported_by']}]({rep['source_url']})" if rep.get("source_url") else f" — {rep.get('reported_by') or 'reporter not named'}"
    st.caption(f"{rep['source']}{link}. Report updated {ui.age_text(rep['updated_at_utc'])}; fetched {rep['fetched_at_utc']}. "
               "This is the lineup a reporter published for the next game, not a confirmed lineup and not what the player did last game.")
else:
    st.caption("No reported line or power-play unit is available for this player (the lineup source did not list them or has not been read yet).")

st.markdown("#### Estimated usage (inferred from ice time — not an assigned line)")
if p.get("usage_tier"):
    kind = "Defense usage tier" if p["position"] == "D" else "Forward usage tier"
    st.write(f"**Estimated usage tier: {kind.split()[0]} {p['usage_tier']}**" + (f" · **Estimated PP usage: {ui.est_pp(p).lower()}**" if p.get("pp_usage") else " · Estimated PP usage: none regular"))
    st.caption(f"{p['usage_source']} Newest game used: {p['last_game_date']}. Recent averages — ice time {p['recent_avg']['toi']:.1f} min, power play {p['recent_avg']['toi_pp']:.1f} min.")
else:
    st.caption("Not enough recent games to estimate usage (needs at least 2 this season).")

st.markdown("#### Recent games (history)")
st.dataframe([{"Date": r["date"], "Season": r.get("season", ""), "Opp": ("vs " if r["home"] else "@ ") + r["opp"], "TOI": r["toi"], "PP": r["toi_pp"], "SOG": int(r["shots"]),
               "G": int(r["goals"]), "A": int(r["assists"]), "Hits": int(r["hits"]), "Blk": int(r["blocks"])} for r in p["recent_games"]],
             hide_index=True, width="stretch")

st.markdown("#### Next game (projection)")
ng, pr = p.get("next_game"), p.get("projection")
if not ng:
    st.caption("No upcoming regular-season game is scheduled for this team in the schedule on file.")
elif not pr:
    st.caption("A projection could not be built for this player.")
else:
    st.write(f"**{ng['opp']}** {'at home' if ng['home'] else 'away'} · {ui.et_time(ng['start_utc'], True)} · game {ng['game_id']}")
    e = pr["expected"]
    b = st.columns(6)
    b[0].metric("Expected ice time", f"{e['toi']:.1f} min")
    b[1].metric("Expected PP time", f"{e['toi_pp']:.1f} min")
    b[2].metric("Expected shots", f"{e['shots']:.2f}")
    b[3].metric("Expected goals", f"{e['goals']:.2f}")
    b[4].metric("Expected assists", f"{e['assists']:.2f}")
    b[5].metric("Expected points", f"{e['points']:.2f}")
    c = st.columns(2)
    c[0].metric("Expected hits", f"{e['hits']:.2f}")
    c[1].metric("Expected blocks", f"{e['blocks']:.2f}")
    pb = pr["probabilities"]
    st.dataframe([{"Event": k.replace(">=", " ") + "+", "Calibrated chance": ui.pct(v, 1)} for k, v in pb.items()], hide_index=True, width="stretch")
    note = f"Model {pr['model_version']}, {pr['games_observed']} prior games."
    if pr["limited_history"]:
        note += " Limited history: probabilities for players with fewer than 20 prior games over-predicted in testing and are not used for pricing."
    st.caption(note + " Expected values and chances are projections for this matchup, not past results. Validation: docs/validation/skater_projection_validation.json.")

st.markdown("#### Best qualifying +100 option")
opt = opts.get(p.get("option_id"))
if opt:
    ui.option_card(opt, key=f"pl_{opt['option_id']}", cash=cash, page_generated_at=page_ts)
else:
    st.caption("No qualifying option for this player right now: either no fresh DraftKings price is on file for them, or no single or cross-game pairing reaches +100 with positive value under the ticket policy.")
