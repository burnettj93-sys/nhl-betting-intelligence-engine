"""Parlay Builder — a read-only tool: pick players, markets and lines from DraftKings' current prices, assemble a hypothetical slip, edit it, enter a hypothetical stake and review the prices and
the potential return. Nothing is submitted, recorded or debited anywhere (there are no accounts); the model's $500 paper book is untouched by anything here. The slip's number is the product of
the leg prices, an ESTIMATE (for legs from one game, not even that: those stay an unpriced draft)."""
from __future__ import annotations

import datetime as dt
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import streamlit as st

from dashboard import product_source as ps
from dashboard import theme, ui
from operational import builder_pool as bp

ui.header("Parlay Builder", "Try combinations from DraftKings' current prices and see the price and return for a hypothetical stake. Nothing is placed or recorded.")
pool = ui.load(ps.builder_pool, "The price list")
games = (pool or {}).get("games") or {}
if not games:
    theme.empty_state("<b>No player prices are on file yet for today's games.</b>",
                      "The engine looks at DraftKings from 8:00 AM ET and again before each game; prices appear here as soon as they are on file. Tomorrow's player prices are not posted a day ahead (see the Tomorrow page).")
    st.stop()

now = ui._utcnow()
SLIP, STAKE = "_slip", "_slip_stake"
MAX_BUILDER_LEGS = 6                              # a hypothetical slip holds at most this many legs
slip: list[dict] = st.session_state.setdefault(SLIP, [])
st.caption(f"Price list built {ui.et_time(pool.get('generated_at_utc'), True)} ({ui.age_text(pool.get('generated_at_utc'))}) · {len(games)} game(s) · prices are DraftKings US-feed quotes, not verified for Ontario. "
           "Each market shows its own quote age; a price older than its limit can be seen but not added to the slip.")
FAMILY_NAME = pool.get("families") or {k: v[2] for k, v in bp.FAMILIES.items()}


def line_state(line: dict | None) -> dict:
    if line is None:
        return {"usable": False, "word": "no longer priced", "age": None}
    fr = bp.freshness(line, now)
    if fr["started"]:
        return {"usable": False, "word": "game started", "age": fr["quote_age_min"]}
    if not fr["usable"]:
        return {"usable": False, "word": f"STALE — {ui.age_short(fr['quote_age_min'])} old", "age": fr["quote_age_min"]}
    return {"usable": True, "word": f"fresh — {ui.age_short(fr['quote_age_min'])} old", "age": fr["quote_age_min"]}


def matchup(g: dict) -> str:
    return f"{g['away']} @ {g['home']}"


left, right = st.columns([3, 2])

# ------------------------------------------------------------------ 1-3: player, market, line ----
with left:
    st.subheader("1 · Pick a player")
    options = []
    for gid, g in games.items():
        for pid, p in g["players"].items():
            options.append((p["n"], pid, gid))
    options.sort(key=lambda o: o[0].lower())
    q = st.text_input("Search by name", key="pb_q", placeholder="for example Draisaitl")
    shown = [o for o in options if q.strip().lower() in o[0].lower()] if q.strip() else options
    if not shown:
        st.info("No priced player matches that name.")
    else:
        labels = {(o[1], o[2]): f"{o[0]} ({games[o[2]]['players'][o[1]]['t']}) · {matchup(games[o[2]])} · {ui.et_time(games[o[2]]['start_utc'])}" for o in shown}
        choice = st.selectbox("Player", list(labels), format_func=lambda k: labels[k], key="pb_player", label_visibility="collapsed")
        pid, gid = choice
        g, p = games[gid], games[gid]["players"][choice[0]]
        codes = [c for c in bp.FAMILIES if c in p["m"]]
        st.subheader("2 · Choose a market")
        code = st.radio("Market", codes, format_func=lambda c: FAMILY_NAME.get(c, c), horizontal=True, key=f"pb_market_{pid}_{gid}", label_visibility="collapsed")
        # A market this game has no prices for is named with the reason (posted / not posted by DraftKings / not fetched / budget blocked), never silently absent.
        try:
            _day = (ui.availability_block(ps.tickets()).get("today") or {}).get("games") or {}
        except Exception:  # noqa: BLE001 - the explanation is a convenience; the builder works without it
            _day = {}
        _mk_keys = {"SOG": "player_shots_on_goal_alternate", "PTS": "player_points", "GOAL": "player_goal_scorer_anytime"}
        for _c, _key in _mk_keys.items():
            if _c in p["m"]:
                continue
            _m = ((_day.get(gid) or {}).get("markets") or {}).get(_key)
            _word = ui._AVAIL_WORDS.get((_m or {}).get("status"), "not fetched for this game today")
            st.caption(f"{FAMILY_NAME.get(_c, _c)}: no prices on file for this game — {_word}" + (f" (checked {ui.et_time(_m.get('checked_utc'), True)})" if _m and _m.get("checked_utc") else "")
                       + (". The odds-credit allowance buys shots first, then anytime goals, then points." if (_m or {}).get("status") in ("BUDGET_BLOCKED", None) else "."))
        mk = p["m"][code]
        family = bp.FAMILIES[code][0]
        st.subheader("3 · Choose a line")
        st.caption(f"{p['n']} — {FAMILY_NAME.get(code, code)}. Quote updated {ui.et_time(mk['q'], True)}.")
        for k, price, prob in mk["l"]:
            line = bp.find_line(pool, gid, pid, family, k)
            stt = line_state(line)
            row = st.columns([4, 2, 2, 3, 2])
            row[0].markdown(f"**{bp.leg_label(p['n'], family, k)}**")
            row[1].markdown(f"<span class='odds'>{ui.american(price)}</span>", unsafe_allow_html=True)
            rec = (mk.get("rec") or {}).get(str(k))
            if rec:                      # four numbers kept apart: recent record, model, the estimate a ticket would use, and what the price implies
                price_p = 1.0 / (1.0 + (price / 100.0 if price > 0 else 100.0 / abs(price)))
                row[2].caption(f"Recent {rec[0]}/{rec[1]} ({ui.pct(rec[0] / max(rec[1], 1))}{f', {rec[3]} this season' if len(rec) > 3 and rec[3] is not None else ''}) · Model {ui.pct(prob)}  \n" + (f"Estimate {ui.pct(rec[2])} · " if rec[2] is not None else "") + f"Price implies {ui.pct(price_p)}")
            else:
                row[2].caption(f"Model {ui.pct(prob)}" if prob is not None else "No model estimate")
            row[3].caption(("✅ " if stt["usable"] else "⚠️ ") + stt["word"])
            already = any(l["participant_id"] == pid and l["market_family"] == family and l["game_id"] == gid for l in slip)
            if row[4].button("Replace" if already else "Add", key=f"pb_add_{gid}_{pid}_{code}_{k}", disabled=not stt["usable"]):
                if len(slip) >= MAX_BUILDER_LEGS and not already:
                    st.warning(f"A slip holds at most {MAX_BUILDER_LEGS} legs.")
                else:
                    slip[:] = [l for l in slip if not (l["participant_id"] == pid and l["market_family"] == family and l["game_id"] == gid)]
                    slip.append({"game_id": gid, "participant_id": pid, "participant_name": p["n"], "market_family": family, "threshold": int(k), "side": "OVER",
                                 "american_price": float(price), "team": p["t"], "matchup": matchup(g)})
                    st.rerun()
        st.caption("Lines come from DraftKings' alternate shots ladder, player points, anytime goal scorer, and (only for a goalie whose start is confirmed) saves. A player can be on a slip once per market. "
                   "The model's chance, where shown, is an estimate, not a prediction you can rely on.")

# ------------------------------------------------------------------ the slip ----
with right:
    st.subheader("Your slip")
    if not slip:
        st.info("Nothing on your slip yet. Pick a player, a market and a line, then press Add. Your selections stay here while you look at other players.")
        st.stop()
    any_blocked, any_moved = False, False
    for i, leg in enumerate(list(slip)):
        line = bp.find_line(pool, leg["game_id"], leg["participant_id"], leg["market_family"], leg["threshold"])
        stt = line_state(line)
        moved = line is not None and abs(line["price"] - leg["american_price"]) > 1e-9
        any_blocked = any_blocked or not stt["usable"]
        any_moved = any_moved or moved
        with st.container(border=True):
            c = st.columns([6, 1])
            c[0].markdown(f"**{bp.leg_label(leg['participant_name'], leg['market_family'], leg['threshold'])}**  \n{leg['matchup']} · {ui.et_time(line['start_utc']) if line else '—'}")
            if c[1].button("✕", key=f"pb_rm_{i}_{leg['participant_id']}_{leg['market_family']}", help="Remove this leg"):
                slip.pop(i)
                st.rerun()
            st.markdown(f"<span class='odds'>{ui.american(leg['american_price'])}</span> &nbsp; " + ("✅ " if stt["usable"] else "⚠️ ") + ui.esc(stt["word"]), unsafe_allow_html=True)
            if moved:
                st.caption(f"Price moved: you added it at {ui.american(leg['american_price'])}; it is now {ui.american(line['price'])}.")
    if any_moved and st.button("Use the current prices", key="pb_update"):
        for leg in slip:
            line = bp.find_line(pool, leg["game_id"], leg["participant_id"], leg["market_family"], leg["threshold"])
            if line:
                leg["american_price"] = line["price"]
        st.rerun()
    if st.button("Clear slip", key="pb_clear"):
        slip.clear()
        st.rerun()

    same_game = len({l["game_id"] for l in slip}) < len(slip)
    comb = bp.combined([l["american_price"] for l in slip])
    stake = st.number_input("Hypothetical stake (\\$)", min_value=1.0, max_value=1000.0, value=10.0, step=5.0, key=STAKE,
                            help="Only to show the return on this slip. Nothing is bet, recorded or debited.")
    if same_game:
        st.markdown("<div class='slip'><div class='stats'><div class='stat accent'><span class='k'>Unpriced draft</span><span class='v'>—</span>"
                    "<span class='sub'>DraftKings prices legs from one game together and no combined price is available</span></div></div></div>", unsafe_allow_html=True)
        st.warning("**This draft has no price.** It has legs from the same game, and there is no genuine combined price for them; multiplying the leg prices would not be a price, so no payout is shown. "
                   "Keep one leg per game to see an estimated combined price. The draft stays here.")
    else:
        kind = ("DraftKings quote" if len(slip) == 1 else "Estimated: the product of each leg's own price, not a quoted parlay price")
        st.markdown(f"<div class='slip'><div class='stats'><div class='stat accent'><span class='k'>{'Quoted price' if len(slip) == 1 else 'Estimated price'}</span>"
                    f"<span class='v'>{ui.american(comb['american'])}</span><span class='sub'>{ui.esc(kind)}</span></div>"
                    f"<div class='stat'><span class='k'>Hypothetical stake</span><span class='v'>{ui.money(stake)}</span></div>"
                    f"<div class='stat'><span class='k'>Potential return</span><span class='v'>{ui.money(stake * comb['return_decimal'])}</span><span class='sub'>profit {ui.money(stake * (comb['return_decimal'] - 1))}</span></div></div></div>",
                    unsafe_allow_html=True)
    if any_blocked:
        st.warning("A leg on this slip has no usable price right now (stale, withdrawn, or the game started). Remove it, or wait for a refresh; the return above is not current.")
    if any_moved:
        st.info("A price moved since you added a leg. Press “Use the current prices” to review the slip at today's quotes.")
    st.caption("This is a review tool, not a bet: nothing here is placed, submitted or recorded, and it never touches the model's paper book. The model's automatic ticket uses only shots and moneylines; "
               "this builder also shows points, anytime goals and (for a goalie whose start is confirmed) saves. A slip does not need the model's edge or +100. Prices are DraftKings US-feed quotes, "
               "not verified for Ontario; a market with no price shows why on its player's page.")
