"""Game Detail — exactly the game that was selected (by link, by the Games page, or by the pickers here); never a substitute.
Both teams' form, goalies with start estimates and confirmation status, the skaters who are likely to play with roles and
matchup projections, prices and tickets."""
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import streamlit as st

from dashboard import product_source as ps
from dashboard import ui

ui.header("Game Detail", "One game: teams, goalies, players, prices and tickets.")
games = ui.load(ps.games, "The schedule")
details = ui.load(ps.game_details, "Game detail")
all_games = {g["game_id"]: g for g in games["games"]}

wanted = st.query_params.get("game") or st.session_state.get("selected_game_id")
if wanted and str(wanted) not in all_games:
    ui.unavailable(f"Game {wanted} is not in the schedule on file. Nothing else is shown in its place — pick a game below.", "That game")
    wanted = None

# --- pickers: date then game, defaulting to the requested game or the current day
reg = [g for g in games["games"] if g["season"] == "20262027" and g["type"] == "REGULAR"]
dates = sorted({g["date_et"] for g in reg})
start_date = all_games[str(wanted)]["date_et"] if wanted else games["default_date"]
if start_date not in dates:
    dates = sorted(set(dates) | {start_date})
c1, c2 = st.columns(2)
date = c1.selectbox("Date (Eastern)", dates, index=dates.index(start_date) if start_date in dates else 0, key="gd_date")
day = sorted((g for g in games["games"] if g["date_et"] == date and (g["type"] == "REGULAR" or str(wanted) == g["game_id"])),
             key=lambda g: (g["start_utc"] or "", g["game_id"]))
if not day:
    st.info("No games on that date.")
    st.stop()
ids = [g["game_id"] for g in day]
default_id = str(wanted) if wanted and str(wanted) in ids else ids[0]
gid = c2.selectbox("Game", ids, index=ids.index(default_id), key=f"gd_game_{date}",
                   format_func=lambda i: f"{all_games[i]['away']} @ {all_games[i]['home']} · {all_games[i]['start_et']}")
st.session_state["selected_game_id"] = gid
st.query_params["game"] = gid
g = all_games[gid]

st.subheader(f"{g['away']} @ {g['home']}")
st.markdown(ui.status_chip(g["state"]) + ui.chip(g["type"].title(), "muted") + ui.chip(f"Game {gid}", "muted"), unsafe_allow_html=True)
st.caption(f"{ui.et_time(g['start_utc'], True) if g['start_utc'] else 'time n/a'} · Eastern date {g['date_et']} · {g['season_label']}")

d = details.get(gid)
if g["state"] == "FINAL":
    suffix = f" ({g['period_type']})" if g.get("period_type") in ("OT", "SO") else ""
    st.metric("Final score", f"{g['away']} {g['away_score']} – {g['home']} {g['home_score']}{suffix}")
    st.caption(f"Result recorded {ui.et_time(g.get('result_observed_at_utc'), True)}.")
    gs = g.get("goalies") or {}
    if gs.get("home") or gs.get("away"):
        rows = []
        for side, team in (("away", g["away"]), ("home", g["home"])):
            s = gs.get(side)
            if s:
                rows.append({"Team": team, "Goalie (started)": s["name"], "Shots against": int(s["shots_against"]), "Saves": int(s["saves"]),
                             "Goals against": int(s["goals_against"]), "Minutes": s["toi"]})
        st.markdown("#### Goalies")
        st.dataframe(rows, hide_index=True, width="stretch")
elif g.get("win_probability"):
    wp = g["win_probability"]
    m = st.columns(3)
    m[0].metric(f"{g['home']} win chance", ui.pct(wp["home"]))
    m[1].metric(f"{g['away']} win chance", ui.pct(wp["away"]))
    ml = g.get("moneyline")
    if ml and ml.get("home") and ml.get("away"):
        m[2].metric("DraftKings moneyline", f"{ui.american(ml['away']['american'])} / {ui.american(ml['home']['american'])}",
                    f"{g['away']} / {g['home']}", delta_color="off")
        st.caption(f"Quote captured {ui.et_time(ml['home']['quote_captured_at_utc'], True)} ({ui.age_text(ml['home']['quote_captured_at_utc'])}). US feed; not verified for Ontario.")
    else:
        m[2].caption("No DraftKings moneyline quote on file.")
    st.caption(wp["model"] + ". A display estimate, not a recommendation: moneyline tickets need a fresh quote and are not driven by this model.")
if g.get("tickets"):
    st.caption("Tickets with a leg in this game: " + ", ".join(g["tickets"]))

if d is None:
    st.info("Lineup and projection detail is built for games from 3 days ago through 7 days ahead; this game is outside that window, so only the information above is available.")
    st.stop()

players, goalies = ui.load(ps.players, "Players"), ui.load(ps.goalies, "Goalies")
try:
    opts = {o["option_id"]: o for o in ps.options().get("options", [])}
except ps.Unavailable:
    opts = {}

for side in ("away", "home"):
    s = d["sides"][side]
    st.markdown(f"### {s['team']} ({'away' if side == 'away' else 'home'})")
    rec = s.get("record")
    if rec:
        st.caption(f"Record {rec['w']}-{rec['l']}-{rec['otl']} in {rec['gp']} game(s) · goals {rec['gf']}–{rec['ga']} · team strength rating {s['strength_rating']:+.2f} goals/game"
                   + ("  ·  last 5: " + " ".join(x["result"] for x in rec.get("last5", [])) if rec.get("last5") else ""))
    else:
        st.caption("No regular-season games played yet this season.")

    st.markdown("**Goalies**")
    grows = []
    for pid in s["goalie_ids"]:
        gl = goalies.get(pid)
        if not gl:
            continue
        sea = gl.get("season") or {}
        st_ = gl.get("start") or {}
        pr = gl.get("projection") if gl.get("next_game") and gl["next_game"]["game_id"] == gid else None
        grows.append({"Goalie": gl["name"], "Start chance (estimate)": ui.pct(st_.get("probability")) if st_ else "—",
                      "Status": gl["confirmation"]["status"].replace("_", " ").title(),
                      "Season W-L-OTL": f"{sea.get('wins')}-{sea.get('losses')}-{sea.get('ot_losses')}" if sea.get("games") else "no games yet",
                      "SV%": f"{sea['save_pct']:.3f}" if sea.get("save_pct") is not None else "—",
                      "GAA": f"{sea['gaa']:.2f}" if sea.get("gaa") is not None else "—",
                      "Exp. saves": f"{pr['expected_saves']:.1f} ({pr['saves_range_80'][0]}–{pr['saves_range_80'][1]})" if pr else "—",
                      "Exp. GA": f"{pr['expected_goals_against']:.2f}" if pr else "—"})
    if grows:
        st.dataframe(grows, hide_index=True, width="stretch")
        st.caption("Start chance is an estimate from recent usage and rest; no confirmation source is connected, so every goalie is Unconfirmed. Expected saves show the 80% range.")
    else:
        st.caption("No goalie data on file for this team.")

    st.markdown("**Skaters (by recent ice time)**")
    rows = []
    for pid in s["skater_ids"]:
        p = players.get(pid)
        if not p:
            continue
        pr = p.get("projection") if p.get("next_game") and p["next_game"]["game_id"] == gid else None
        rows.append({"Player": p["name"], "Pos": p["position"], "Est. usage tier": ui.est_tier(p), "Est. PP usage": ui.est_pp(p), "Reported line": ui.reported_line(p), "Reported PP": ui.reported_pp(p),
                     "TOI": f"{p['recent_avg']['toi']:.1f}", "PP min": f"{p['recent_avg']['toi_pp']:.1f}",
                     "Exp. shots": f"{pr['expected']['shots']:.2f}" if pr else "—",
                     "Shots 2+": ui.pct(pr["probabilities"]["shots>=2"]) if pr else "—",
                     "Point 1+": ui.pct(pr["probabilities"]["points>=1"]) if pr else "—",
                     "Goal": ui.pct(pr["probabilities"]["goals>=1"]) if pr else "—",
                     "Sample": f"{pr['games_observed']} g" + (" (limited)" if pr["limited_history"] else "") if pr else "—",
                     "Best option": "yes" if p.get("option_id") in opts else "—"})
    if rows:
        st.dataframe(rows, hide_index=True, width="stretch")
        st.caption("Est. usage tier and Est. PP usage are inferred from recent ice time (they are not assigned lines or power-play units). Reported line / PP appear only where a lineup source lists the player; each player's page shows the source and time. Probabilities are calibrated; players with fewer than 20 prior games are flagged limited and are not priced.")
    else:
        st.caption("No skater logs on file for this team yet this season.")
