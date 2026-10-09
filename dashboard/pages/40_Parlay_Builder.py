"""Paper Parlay Builder — pick players and lines from DraftKings' current prices, build a slip, and submit it to YOUR personal paper account. Your own choices: no edge or +100 requirement.
The slip's number is the product of the leg prices, an ESTIMATE (for legs from one game, not even that). Nothing is recorded until you press Submit."""
from __future__ import annotations

import datetime as dt
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import streamlit as st

from dashboard import order_client
from dashboard import product_source as ps
from dashboard import theme, ui
from operational import builder_pool as bp
from operational import log_signing
from operational import personal_logs as pl

ui.header("Parlay Builder", "Build your own paper bet from DraftKings' current prices, and submit it to your own account.")
pool = ui.load(ps.builder_pool, "The price list")
games = (pool or {}).get("games") or {}
if not games:
    theme.empty_state("<b>No player prices are on file yet for today's games.</b>",
                      "The engine looks at DraftKings from 8:00 AM ET and again before each game; prices appear here as soon as they are on file. Tomorrow's player prices are not posted a day ahead (see the Tomorrow page).")
    st.stop()

now = ui._utcnow()
SLIP, STAKE, ACK = "_slip", "_slip_stake", "_slip_ack"
slip: list[dict] = st.session_state.setdefault(SLIP, [])
st.caption(f"Price list built {ui.et_time(pool.get('generated_at_utc'), True)} ({ui.age_text(pool.get('generated_at_utc'))}) · {len(games)} game(s) · prices are DraftKings US-feed quotes, not verified for Ontario. "
           "Each market shows its own quote age; a price older than its limit can be seen but not added.")
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


def to_my_bets() -> None:
    try:
        st.page_link("pages/38_My_Bets.py", label="Go to My Bets")
    except Exception:  # noqa: BLE001 - a missing navigation entry must not take the builder down; the sidebar still has the page
        st.caption("Open **My Bets** from the sidebar.")


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
            row[2].caption(f"Model {ui.pct(prob)}" if prob is not None else "No model estimate")
            row[3].caption(("✅ " if stt["usable"] else "⚠️ ") + stt["word"])
            already = any(l["participant_id"] == pid and l["market_family"] == family and l["game_id"] == gid for l in slip)
            if row[4].button("Replace" if already else "Add", key=f"pb_add_{gid}_{pid}_{code}_{k}", disabled=not stt["usable"]):
                if len(slip) >= pl.MAX_BUILDER_LEGS and not already:
                    st.warning(f"A slip holds at most {pl.MAX_BUILDER_LEGS} legs.")
                else:
                    slip[:] = [l for l in slip if not (l["participant_id"] == pid and l["market_family"] == family and l["game_id"] == gid)]
                    slip.append({"game_id": gid, "participant_id": pid, "participant_name": p["n"], "market_family": family, "threshold": int(k), "side": "OVER",
                                 "american_price": float(price), "team": p["t"], "matchup": matchup(g)})
                    st.session_state.pop("_builder_order", None)
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
                st.session_state.pop("_builder_order", None)
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
        st.session_state.pop("_builder_order", None)
        st.rerun()

    same_game = len({l["game_id"] for l in slip}) < len(slip)
    comb = bp.combined([l["american_price"] for l in slip])
    log = ui.selected_log()
    bank = ui.account_bankroll(log)
    cash = bank["available_cash"] if bank else None
    lo = float(pl.MIN_STAKE)
    hi = max(min(float(pl.MAX_STAKE), float(cash)), lo) if cash is not None else float(pl.MAX_STAKE)
    stake = st.number_input("Stake (paper \\$)", min_value=lo, max_value=hi, value=min(10.0, hi), step=5.0, key=STAKE)
    kind = ("DraftKings quote" if len(slip) == 1 else
            ("Multiplied from the leg prices — NOT a DraftKings quote (DraftKings prices legs from one game together, with its own adjustment)" if same_game else
             "Estimated: the product of each leg's own price, not a quoted parlay price"))
    st.markdown(f"<div class='slip'><div class='stats'><div class='stat accent'><span class='k'>{'Quoted price' if len(slip) == 1 else ('Multiplied price' if same_game else 'Estimated price')}</span>"
                f"<span class='v'>{ui.american(comb['american'])}</span><span class='sub'>{ui.esc(kind)}</span></div>"
                f"<div class='stat'><span class='k'>Stake</span><span class='v'>{ui.money(stake)}</span></div>"
                f"<div class='stat'><span class='k'>To return</span><span class='v'>{ui.money(stake * comb['return_decimal'])}</span><span class='sub'>profit {ui.money(stake * (comb['return_decimal'] - 1))}</span></div></div></div>",
                unsafe_allow_html=True)
    ack = True
    if same_game:
        ack = st.checkbox("I understand this slip has legs from the same game: its number is multiplied from the leg prices and is not a DraftKings quote.", key=ACK)

    st.markdown("**Destination**")
    if log is None:
        st.warning("Open or create your account on **My Bets** first. Your slip stays here while you do.")
        to_my_bets()
        can_submit = False
    elif not log["can_write"]:
        st.warning(f"**{log['name']}** is open view-only. Open it with your passcode on **My Bets** to add bets.")
        to_my_bets()
        can_submit = False
    elif not order_client.personal_write_token(getattr(st, "secrets", {})):
        st.caption(f"Destination: **{ui.esc(log['name'])}** — available cash {ui.money(cash)}.")
        st.info("Submitting is not switched on yet (the app has no write credential). It will work as soon as the owner adds one; your slip is kept.")
        can_submit = False
    else:
        st.markdown(f"Destination: **{ui.esc(log['name'])}** (your own account — separate from the model book and everyone else's) · available cash {ui.money(cash)}")
        can_submit = True
    if any_blocked:
        st.warning("A leg on your slip has no usable price right now (stale, withdrawn, or the game started). Remove it, or wait for a refresh.")
    if any_moved:
        st.info("A price moved. Press “Use the current prices” before submitting.")
    sess = st.session_state.get("_builder_order")
    if sess and log and sess.get("log") == log["hash"]:
        answer = ui._log_order(sess["order_id"], log["hash"])
        if answer is None:
            ui.banner("Order sent — waiting for the engine to recheck the prices and your cash and record it (usually a few minutes).", "info")
            if st.button("Check status", key="pb_check"):
                from dashboard import snapshot_source
                snapshot_source.current(force_refresh=True)
                st.rerun()
        elif answer["status"] == "RECORDED":
            ui.banner(f"Added to <b>{ui.esc(log['name'])}</b> as <b>{answer['bet_id']}</b>. See it on My Bets.", "good")
            if st.button("Start a new slip", key="pb_new"):
                slip.clear()
                st.session_state.pop("_builder_order", None)
                st.rerun()
        else:
            ui.banner(f"Nothing was added: {ui.esc(answer['reason'] or answer['status'])}", "bad" if answer["status"] == "REJECTED" else "warn")
            if st.button("Dismiss", key="pb_dismiss"):
                st.session_state.pop("_builder_order", None)
                st.rerun()
    elif st.button(f"Submit to {log['name']} — {ui.money(stake)}" if log else "Submit", key="pb_submit", type="primary", disabled=not (can_submit and ack and not any_blocked and not any_moved),
                   help="Files one order. The engine rechecks every price and your cash first. Nothing is added by browsing."):
        order = order_client.build_builder_order(slip, order_id=order_client.new_order_id(), log_hash=log["hash"], page_generated_at=pool.get("generated_at_utc"),
                                                 stake=float(stake), same_game_ack=bool(same_game and ack), combined_american=comb["american"])
        order["via"] = "direct"
        res = ui.file_order(log_signing.sign(order, log["write_key"], log["hash"]))
        if not res["ok"]:
            st.error(res["error"])
        else:
            st.session_state["_builder_order"] = {"order_id": order["order_id"], "log": log["hash"]}
            st.rerun()
    st.caption("A slip is your own choice: it does not need the model's edge or +100. It does need a real, fresh DraftKings price on every leg, a game that has not started, and enough cash. "
               "A leg whose player does not play is left open (UNRESOLVED) rather than guessed, until the sportsbook's rule is verified.")
