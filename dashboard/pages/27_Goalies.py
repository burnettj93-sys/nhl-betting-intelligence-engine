"""Goalies — current-season record, recent form and sample size, the next opponent, how likely each goalie is to start (an estimate)
and whether a start is confirmed (with its source and time), plus expected saves and goals against with supported ranges."""
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import streamlit as st

from dashboard import product_source as ps
from dashboard import ui

ui.header("Goalies", "Season records come from NHL.com; form, start chances and projections come from the engine's goalie model.")
goalies = ui.load(ps.goalies, "Goalie data")
games = ui.load(ps.games, "The schedule")

teams = sorted({g["team"] for g in goalies.values() if g["team"]})
f = st.columns([3, 2, 3])
q = f[0].text_input("Search by name", key="gl_q")
team = f[1].selectbox("Team", ["All teams"] + teams, key="gl_team")
scope = f[2].radio("Show", ["Goalies with a game coming up", "All goalies"], horizontal=True, key="gl_scope")

rows = list(goalies.values())
if q:
    rows = [g for g in rows if q.lower() in g["name"].lower()]
if team != "All teams":
    rows = [g for g in rows if g["team"] == team]
if scope.startswith("Goalies with"):
    rows = [g for g in rows if g.get("next_game")]
rows.sort(key=lambda g: ((g.get("next_game") or {}).get("start_utc") or "9", -((g.get("start") or {}).get("probability") or 0), g["name"]))

def rec(g):
    s = g.get("season") or {}
    return f"{s.get('wins')}-{s.get('losses')}-{s.get('ot_losses')}" if s.get("games") else "—"

table = [{"Goalie": g["name"], "Team": g["team"], "Next game": (f"{'vs' if g['next_game']['home'] else '@'} {g['next_game']['opp']} · {g['next_game']['date_et']} {g['next_game']['start_et']}"
                                                              if g.get("next_game") else "none scheduled"),
          "Start chance (est.)": ui.pct((g.get("start") or {}).get("probability")) if g.get("start") else "—", "Status": g["confirmation"]["status"].replace("_", " ").title(),
          "W-L-OTL": rec(g), "SV%": f"{g['season']['save_pct']:.3f}" if (g.get("season") or {}).get("save_pct") is not None else "—",
          "GAA": f"{g['season']['gaa']:.2f}" if (g.get("season") or {}).get("gaa") is not None else "—",
          "SO": str((g.get("season") or {}).get("shutouts")) if (g.get("season") or {}).get("games") else "—"} for g in rows]
st.caption(f"{len(rows)} goalie(s). Status is Confirmed only while a fresh confirmation exists — from the team, a recognized beat reporter (read automatically when that feed is enabled), or a person who recorded one — and no later report disagrees. Otherwise it is Unconfirmed; the start chance is an estimate from recent usage and rest.")
ev = st.dataframe(table, hide_index=True, width="stretch", on_select="rerun", selection_mode="single-row", key="gl_table", height=360)
ids = [g["player_id"] for g in rows]
sel = ids[ev.selection.rows[0]] if ev and ev.selection.rows else (st.query_params.get("goalie") if st.query_params.get("goalie") in goalies else None)
if not sel:
    st.info("Select a goalie to open the full page.")
    st.stop()
st.query_params["goalie"] = sel
g = goalies[sel]
st.divider()
st.subheader(f"{g['name']} — {g['team']}")
s = g.get("season")
src = g["season_source"]
if s and s.get("games"):
    m = st.columns(6)
    m[0].metric("Games / starts", f"{s['games']} / {s['starts']}")
    m[1].metric("W-L-OTL", rec(g))
    m[2].metric("Save %", f"{s['save_pct']:.3f}")
    m[3].metric("GAA", f"{s['gaa']:.2f}")
    m[4].metric("Shutouts", s["shutouts"])
    m[5].metric("Shots against", s["shots_against"])
    st.caption(f"Current-season regular-season record from NHL.com, fetched {ui.et_time(src['fetched_at_utc'], True)}"
               + (f". The latest refresh failed ({ui.esc(src['error'])}); this is the last good record from {ui.et_time(src['last_ok_utc'], True)}." if src.get("error") else "."))
elif s is not None:
    st.caption(f"No regular-season games played yet this season (NHL.com, fetched {ui.et_time(src['fetched_at_utc'], True)}).")
else:
    st.caption("The season record could not be loaded from NHL.com" + (f" ({ui.esc(src['error'])})" if src.get("error") else "") + f". Last successful update: {ui.et_time(src.get('last_ok_utc'), True)}.")

st.markdown("#### Recent appearances (history)")
rs = g["recent_starts"]
if rs:
    st.dataframe([{"Date": r["date"], "Season": r["season"], "Opp": r["opp"], "Started": "yes" if r["started"] else "relief", "Shots against": int(r["shots_against"]),
                   "Saves": int(r["saves"]), "Goals against": int(r["goals_against"]), "SV%": f"{r['save_pct']:.3f}" if r["save_pct"] is not None else "—", "Minutes": r["toi"]} for r in rs],
                 hide_index=True, width="stretch")
    cur = [r for r in rs if r["season"] == "current" and r["shots_against"]]
    tot_sa, tot_sv = sum(r["shots_against"] for r in cur), sum(r["saves"] for r in cur)
    st.caption(f"Sample: {len(rs)} most recent appearances shown ({len(cur)} this season"
               + (f", combined SV% {tot_sv / tot_sa:.3f} on {int(tot_sa)} shots" if tot_sa else "") + f"); {g['games_in_log']} appearances in the engine's history. Small samples swing widely.")
else:
    st.caption("No appearances on file.")

st.markdown("#### Next game")
ng, pr, st_ = g.get("next_game"), g.get("projection"), g.get("start")
cf = g["confirmation"]
if not ng:
    st.caption("No upcoming regular-season game is scheduled for this team in the schedule on file.")
else:
    st.write(f"**{'vs' if ng['home'] else '@'} {ng['opp']}** · {ui.et_time(ng['start_utc'], True)} · game {ng['game_id']}")
    k = st.columns(3)
    k[0].metric("Start chance (estimate)", ui.pct(st_["probability"]) if st_ else "—", help="Share of the team's recent starts and rest, from a model validated on 2025-26.")
    k[1].metric("Start status", cf["status"].replace("_", " ").title())
    k[2].metric("Status source / checked", cf["source"] or "none", cf.get("checked_at_utc") and ui.et_time(cf["checked_at_utc"], True) or "never", delta_color="off")
    st.caption(ui.esc(cf["note"]))
    if cf.get("source_url"):
        st.markdown(f"Source link: [{ui.esc(cf['source'])}]({cf['source_url']}) · basis: {ui.esc(str(cf.get('basis', '')).replace('_', ' ').title())}")
    with st.expander("Record a confirmed start for this game"):
        st.caption("Only record what you have actually seen from a published source (team or league announcement, broadcast, morning-skate report). "
                   "The record keeps where you saw it and when; it is what unlocks saves props for this goalie. The model's estimate never confirms anything.")
        with st.form(f"gl_confirm_{sel}"):
            where = st.text_input("Where you saw it", placeholder="e.g. team announcement on X, 10:45 AM", key=f"gl_where_{sel}")
            if st.form_submit_button("Record confirmed start"):
                if not where.strip():
                    st.error("Say where you saw it.")
                else:
                    from dashboard import order_client
                    doc = order_client.build_confirmation(confirmation_id=order_client.new_order_id().replace("ord_", "cnf_"), game_id=ng["game_id"], team=g["team"],
                                                          goalie_id=g["player_id"], where_seen=where.strip(),
                                                          seen_at_utc=__import__("datetime").datetime.now(__import__("datetime").timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))
                    email = getattr(getattr(st, "user", None), "email", None)
                    direct, token = order_client.configured_write_access(getattr(st, "secrets", {}), email)
                    if direct:
                        res = order_client.submit_direct(doc, token)
                        st.success("Filed — the status updates after the engine's next pass.") if res["ok"] else st.error(res["error"])
                    else:
                        st.session_state[f"gl_link_{sel}"] = order_client.prefilled_issue_url(doc)
        if st.session_state.get(f"gl_link_{sel}"):
            st.link_button("Open GitHub to file this confirmation", st.session_state[f"gl_link_{sel}"])
    if pr:
        p2 = st.columns(5)
        p2[0].metric("Expected shots against", f"{pr['expected_shots_against']:.1f}")
        p2[1].metric("Save % used", f"{pr['save_pct_used']:.3f}", help="This goalie's save percentage shrunk toward the league rate.")
        p2[2].metric("Expected saves", f"{pr['expected_saves']:.1f}", f"80% range {pr['saves_range_80'][0]}–{pr['saves_range_80'][1]}", delta_color="off")
        p2[3].metric("Expected goals against", f"{pr['expected_goals_against']:.2f}", f"80% range {pr['goals_against_range_80'][0]}–{pr['goals_against_range_80'][1]}", delta_color="off")
        w = pr["win_probability"]
        p2[4].metric(f"{g['team']} win chance", ui.pct(w["team_win_probability"]), f"{ui.pct(w['if_this_goalie_starts'])} if he starts", delta_color="off")
        st.caption("Saves range: the model's central 80% interval; in testing it contained the actual result 83% of the time. The goals-against range is conservative: it contained the result 90% of the time because goals come in whole numbers. "
                   + ui.esc(w["note"]))
        st.markdown("**Chance of at least N saves (raw model probability)**")
        st.dataframe([{"Saves": k.replace("saves>=", "") + "+", "Chance": ui.pct(v, 1)} for k, v in pr["saves_probabilities"].items()], hide_index=True, width="stretch")
        st.caption(f"Sample behind the numbers: {pr['sample']['games']} appearances, about {pr['sample']['shots_faced_weighted']} recency-weighted shots faced. Model {pr['model_version']}; "
                   "saves props stay blocked from tickets until this goalie's start is confirmed.")
    else:
        st.caption("No projection: this goalie has no recorded appearances and is not among his team's recent starters.")

st.markdown("#### Best qualifying +100 option")
try:
    _tk = ps.tickets()
    _doc = ps.options()
    _opts = {o["option_id"]: o for o in _doc.get("options", [])}
    _person = (_doc.get("persons") or {}).get(sel)
    _page_ts = _tk["generated_at_utc"]
except ps.Unavailable as _exc:
    _opts, _person, _page_ts = {}, None, None
    st.caption(f"The option board is unavailable: {ui.esc(str(_exc))}")
if _person and _person.get("option_id") in _opts:
    ui.option_card(_opts[_person["option_id"]], key=f"gl_{_person['option_id']}", cash=None, page_generated_at=_page_ts)
elif _page_ts:
    _why = {"NO_FRESH_PRICED_LEG": "no fresh DraftKings saves price is on file for this goalie",
            "NO_LEG_BEATS_ITS_PRICE": "none of his priced saves lines beats its price under the ticket policy",
            "NO_COMPANION_REACHES_PLUS_100_WITH_POSITIVE_VALUE": "his saves line alone does not reach +100 and no cross-game partner makes a +100 parlay with value"}
    _confirmed = str(cf.get("status", "")).upper() == "CONFIRMED" if ng else False
    st.caption("No qualifying option for this goalie right now: "
               + (_why.get((_person or {}).get("reason_no_option"), "saves lines are only priced into options once this goalie's start is confirmed, and none is on file")
                  if _confirmed else "saves props stay blocked until this goalie's start is confirmed by a recognised source, and the goalie's team result is not a substitute for his own line")
               + ". Singles are quoted DraftKings prices; two-leg parlays are labelled estimates, never a quoted combined price.")
