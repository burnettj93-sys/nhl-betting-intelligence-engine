"""My Bets — a personal paper-bet log that belongs to whoever knows its code, shown beside (never mixed into) the model's $500 book.
A code is a name for a log, not a password; the data is published to a public repository, so keep nothing personal in a log."""
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import streamlit as st

from dashboard import order_client
from dashboard import product_source as ps
from dashboard import ui
from operational import personal_logs as pl

ui.header("My Bets", "Your own paper-bet log — kept apart from the model's \\$500 book. Nothing you add here can change the model's cash, tickets or results.")

doc = ui.personal_logs_doc()
if not doc:
    ui.banner("Personal logs are not published yet by the engine version that wrote the current data. They appear after the next engine publish.", "warn")
    st.stop()
logs = doc.get("logs") or {}
rules = doc.get("rules") or {}


def money(v):
    return ui.money(v)


def show_log_summary(summary: dict) -> None:
    c = st.columns(6)
    c[0].metric("Bets", summary["bets"])
    c[1].metric("Open", summary["open"], f"{money(summary['open_stake'])} at stake", delta_color="off")
    c[2].metric("Settled", f"{summary['wins']}W–{summary['losses']}L–{summary['voids']}V")
    c[3].metric("Settled P&L", ui.signed_money(summary["settled_pnl"]))
    c[4].metric("Win rate", ui.pct(summary["win_rate"], 0) if summary["win_rate"] is not None else "—")
    c[5].metric("ROI on settled", ui.pct(summary["roi"], 1) if summary["roi"] is not None else "—", help="Settled profit divided by settled stakes (voids excluded).")


def my_log_section(log: dict) -> None:
    d = log["doc"]
    if d is None:
        ui.banner("Waiting for the engine to create this log (usually a few minutes). You can add bets in the meantime — they are held in order.", "info")
        pending = (st.session_state.get("_personal_pending_creation") or {})
        if pending.get("url"):
            st.link_button("Open GitHub to finish filing the log request", pending["url"])
            st.caption("Repository owner only: the request exists once you press “Submit new issue” on GitHub.")
        return
    show_log_summary(d["summary"])
    st.caption(f"Log created {ui.et_time(d['created_at_utc'], True)} · every bet shows “Manually added” · stakes are your own paper amounts, there is no bankroll limit.")
    def is_settled(b):
        return (b.get("result") or {}).get("status") in ("WIN", "LOSS", "VOID")
    open_bets = [b for b in d["bets"] if not is_settled(b)]
    settled = [b for b in d["bets"] if is_settled(b)]
    st.subheader("Open bets")
    if open_bets:
        for b in open_bets:
            ui.ticket_card(b)
    else:
        st.caption("No open bets. Add one from Best Options, Players or Goalies.")
    st.subheader("Settled bets")
    if settled:
        for b in settled:
            ui.ticket_card(b)
    else:
        st.caption("Nothing settled yet.")
    orders = d.get("orders") or []
    if orders:
        with st.expander("Recent order answers (including ones that were not added)"):
            st.dataframe([{"Answered": ui.et_time(o["processed_at_utc"], True), "What": "New log" if o["kind"] == "PERSONAL_LOG_CREATE" else "Add bet",
                           "Answer": o["status"].replace("_", " ").title(), "Why": o.get("reason") or "", "Bet": o.get("bet_id") or "—"} for o in orders],
                         hide_index=True, width="stretch")


def model_section() -> None:
    tk = ui.load(ps.tickets, "The model book")
    if "account" not in tk:
        ui.unavailable("The model book has not been published yet.", "The model book")
        return
    acct, allo = tk["account"], (tk.get("origins") or {}).get("ALL") or {}
    c = st.columns(6)
    c[0].metric("Available cash", money(acct["available_cash"]))
    c[1].metric("Open stakes", money(acct["open_stakes"]), f"{acct['open_tickets']} open", delta_color="off")
    c[2].metric("Equity", money(acct["equity"]))
    c[3].metric("Settled P&L", ui.signed_money(acct["settled_pnl"]))
    c[4].metric("Record", f"{allo.get('wins', 0)}W–{allo.get('losses', 0)}L–{allo.get('voids', 0)}V")
    c[5].metric("Tickets", acct["tickets"])
    st.caption("The model's \\$500 book: only the engine's automatic \\$10 tickets. Details on Today, Paper Performance and Ticket History.")
    for t in (tk.get("tickets") or []):
        if t.get("recorded"):
            ui.ticket_card(t)
    for t in (tk.get("earlier_open_tickets") or []):
        ui.ticket_card(t)


# ---------------------------------------------------------------- choosing a log ----
log = ui.selected_log()
with st.expander("Open or create a log", expanded=log is None):
    ui.banner("<b>A code is a name, not a password.</b> Anyone who knows it can open and add to your log. The engine's data is published to a public repository, so the bets in a log "
              "can be read by anyone who looks (filed under a one-way hash of your code and the display name you choose, never the code itself). Paper bets only; keep nothing personal in a log.", "warn")
    tab_open, tab_new = st.tabs(["Open my log", "Create a log"])
    with tab_open:
        code = st.text_input("Your log code", key="mb_open_code", placeholder="for example otter-maple-puck-4821")
        if st.button("Open log", key="mb_open", disabled=not code.strip()):
            h = ui._code_hash(code)
            found = logs.get(h)
            if found:
                ui.select_log(code, found["display_name"])
                st.rerun()
            else:
                st.error("No log uses that code yet. Check the spelling, or create it on the next tab. A log created in the last few minutes may not be published yet.")
    with tab_new:
        if "mb_new_code" not in st.session_state:
            st.session_state["mb_new_code"] = pl.suggest_code()
        name = st.text_input("Display name (letters, numbers, spaces only)", key="mb_new_name", max_chars=pl.MAX_NAME_LEN, placeholder="for example Casey's picks")
        st.text_input("Choose a code (or keep the suggestion) — write it down; it cannot be recovered", key="mb_new_code")
        if st.button("Suggest another code", key="mb_suggest"):
            st.session_state["mb_new_code"] = pl.suggest_code()
            st.rerun()
        new_code = st.session_state.get("mb_new_code", "")
        problem = pl.validate_code(new_code) or pl.validate_name(name)[1] if (new_code or name) else None
        if problem:
            st.caption(problem)
        if st.button("Create log", key="mb_create", type="primary", disabled=bool(problem) or not name.strip() or not new_code.strip()):
            h = ui._code_hash(new_code)
            clean, _ = pl.validate_name(name)
            if h in logs:
                st.error("That code is already in use. Choose a different code.")
            else:
                creation = {"creation_id": order_client.new_order_id().replace("ord_", "crt_"), "display_name": clean}
                order = order_client.build_personal_order(None, order_id=order_client.new_order_id(), log_hash=h, page_generated_at=doc.get("generated_at_utc"),
                                                          stake=pl.DEFAULT_STAKE, create=creation, kind=pl.TYPE_CREATE)
                res = ui.file_order(order)
                if not res["ok"]:
                    st.error(res["error"])
                else:
                    st.session_state["_personal_pending_creation"] = {"url": res.get("url"), "order_id": order["order_id"]}
                    ui.select_log(new_code, clean, creation=creation)
                    st.rerun()
    if not order_client.personal_write_token(getattr(st, "secrets", {})):
        st.caption("This app has no write credential configured yet, so creating a log or adding a bet makes a GitHub issue link that only the repository owner can submit. "
                   "The owner enables one-click adding by adding a Streamlit secret (see docs/PERSONAL_LOGS.md).")

if log is None:
    st.subheader("Model book at a glance")
    model_section()
    st.stop()

top = st.columns([4, 1])
top[0].markdown(f"### {ui.esc(log['name'])}")
if top[1].button("Switch log", key="mb_switch"):
    ui.forget_log()
    st.rerun()
view = st.radio("Show", ["My bets", "Model bets", "Both"], horizontal=True, key="mb_view",
                help="My bets: your personal log. Model bets: the engine's \\$500 book. Both: side by side with separate totals — never added together.")
if view == "My bets":
    my_log_section(log)
elif view == "Model bets":
    model_section()
else:
    left, right = st.columns(2)
    with left:
        st.markdown("#### My log")
        my_log_section(log)
    with right:
        st.markdown("#### Model book")
        model_section()
    st.caption("The two sets of numbers are kept separate on purpose: your bets are not part of the model's record, and the model's tickets are not in your log.")
