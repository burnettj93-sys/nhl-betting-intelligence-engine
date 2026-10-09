"""My Bets — your own paper account, opened by your last name, with its own \\$500 paper bankroll, shown beside (never mixed into) the model's \\$500 book.
Reading is public (the data is published to a public repository); adding is protected by your passcode."""
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import streamlit as st

from dashboard import order_client
from operational import log_signing
from dashboard import product_source as ps
from dashboard import theme, ui
from operational import personal_logs as pl

ui.header("My Bets", "Your own paper account — kept apart from the model's \\$500 book and from everyone else's. Nothing you do here can change the model's cash, tickets or results.")

doc = ui.personal_logs_doc()
if not doc:
    ui.banner("Personal accounts are not published yet by the engine version that wrote the current data. They appear after the next engine publish.", "warn")
    st.stop()
logs = doc.get("logs") or {}
rules = doc.get("rules") or {}


def money(v):
    return ui.money(v)


def metric_grid(items: list[tuple], compact: bool) -> None:
    """Six figures in one row, or three per row when the section sits in a half-width column (so values never truncate)."""
    per_row = 3 if compact else len(items)
    for i in range(0, len(items), per_row):
        row = st.columns(per_row)
        for col, item in zip(row, items[i:i + per_row]):
            label, value, *rest = item
            col.metric(label, value, *(rest[:1]), **(rest[1] if len(rest) > 1 else {}))


def show_bankroll(bank: dict, summary: dict, compact: bool = False) -> None:
    metric_grid([("Available cash", money(bank["available_cash"]), f"started with {money(bank['starting_balance'])}", {"delta_color": "off"}),
                 ("Open stakes", money(bank["open_stakes"]), f"{summary['open']} open", {"delta_color": "off"}),
                 ("Payouts received", money(bank["payouts_received"]), "stake + profit on wins; stake back on voids", {"delta_color": "off"}),
                 ("Settled P&L", ui.signed_money(bank["settled_pnl"])),
                 ("Record", f"{summary['wins']}W–{summary['losses']}L–{summary['voids']}V"),
                 ("ROI on settled", ui.pct(summary["roi"], 1) if summary["roi"] is not None else "—", None, {"help": "Settled profit divided by settled stakes (voids excluded)."})], compact)
    if bank.get("over_drawn"):
        st.warning("This account is below zero because of earlier bets recorded before bankrolls existed. New bets are blocked until settled bets bring the cash back above the minimum stake.")


def my_log_section(log: dict, compact: bool = False) -> None:
    d = log["doc"]
    if d is None:
        ui.banner("Waiting for the engine to create this account (usually a few minutes).", "info")
        return
    show_bankroll(d["bankroll"], d["summary"], compact)
    st.caption(f"Account created {ui.et_time(d['created_at_utc'], True)} · starts with {money(d['bankroll']['starting_balance'])} of paper cash · every bet shows “Manually added” · "
               "stakes come out of your cash when they are added, and wins pay back stake plus profit.")

    def is_settled(b):
        return (b.get("result") or {}).get("status") in ("WIN", "LOSS", "VOID")
    open_bets = [b for b in d["bets"] if not is_settled(b)]
    settled = [b for b in d["bets"] if is_settled(b)]
    st.subheader("Open bets")
    if open_bets:
        for b in open_bets:
            ui.ticket_card(b)
    else:
        theme.empty_state("No open bets in this account yet.", "Build a slip on the Parlay Builder, or add an option from Best Options. It shows up here after the engine rechecks the price.")
    st.subheader("Settled bets")
    if settled:
        for b in settled:
            ui.ticket_card(b)
    else:
        st.caption("Nothing settled yet.")
    orders = d.get("orders") or []
    if orders:
        with st.expander("Recent order answers (including ones that were not added)"):
            st.dataframe([{"Answered": ui.et_time(o["processed_at_utc"], True), "What": "New account" if o["kind"] == "PERSONAL_LOG_CREATE" else "Add bet",
                           "Answer": o["status"].replace("_", " ").title(), "Why": o.get("reason") or "", "Bet": o.get("bet_id") or "—"} for o in orders],
                         hide_index=True, width="stretch")


def model_section(compact: bool = False) -> None:
    tk = ui.load(ps.tickets, "The model book")
    if "account" not in tk:
        ui.unavailable("The model book has not been published yet.", "The model book")
        return
    acct, allo = tk["account"], (tk.get("origins") or {}).get("ALL") or {}
    metric_grid([("Available cash", money(acct["available_cash"])), ("Open stakes", money(acct["open_stakes"]), f"{acct['open_tickets']} open", {"delta_color": "off"}),
                 ("Equity", money(acct["equity"])), ("Settled P&L", ui.signed_money(acct["settled_pnl"])),
                 ("Record", f"{allo.get('wins', 0)}W–{allo.get('losses', 0)}L–{allo.get('voids', 0)}V"), ("Tickets", acct["tickets"])], compact)
    st.caption("The model's \\$500 book: only the engine's automatic \\$10 tickets. Details on Today, Paper Performance and Ticket History.")
    for t in (tk.get("tickets") or []):
        if t.get("recorded"):
            ui.ticket_card(t)
    for t in (tk.get("earlier_open_tickets") or []):
        ui.ticket_card(t)


# ---------------------------------------------------------------- choosing an account ----
log = ui.selected_log()
SHORT = ("<b>Open</b> your account with your last name. You can look at it without a passcode. <b>Adding a bet</b> needs your 8-character passcode, shown once when you create the account. "
         "Each account starts with its own \\$500 of paper money.")
DETAILS = """**What is public and what is protected**

* **Reading is not private.** The engine publishes its data to a public GitHub repository (that is what lets this free app read it), so anyone can read any account's bets, under its last name.
  Paper bets only; no real money moves. Use only your last name: nothing else personal belongs here.
* **Writing is protected.** Every order (create an account, add a bet) is signed with a key derived from your passcode, using the standard Ed25519 signature scheme. The engine keeps only the
  *public* half and refuses any order whose signature does not verify. Knowing someone's last name lets you read their account, not add to it.
* **The passcode is short on purpose** (8 characters, you type it only when adding a bet) and **the key made from it is deliberately slow to compute**, so guessing passcodes offline takes about a thousand
  CPU-years. It never leaves your browser session: only signatures are sent. Close the page and you will need it again.
* **Your cash is its own.** It is worked out from your own bets every time (\\$500 + settled profit/loss − open stakes), so opening the account again never resets it, and no other account or the model book shares it.
* **Two people with the same last name** are two accounts: the second becomes “Smith 2”, the third “Smith 3”, each with its own passcode and cash. Open yours by typing the number.
* **Lost passcode:** it cannot be recovered (the engine never has it). The account stays readable; create a new one.
"""
with st.expander("Open or create your account", expanded=log is None):
    ui.banner(SHORT, "info")
    with st.expander("How it works — and what is public"):
        st.markdown(DETAILS)
    tab_open, tab_new = st.tabs(["Open my account", "Create an account"])
    with tab_open:
        who = st.text_input("Your last name (add the number if there is more than one, like Smith 2)", key="mb_open_name", placeholder="for example Burnett")
        wkey = st.text_input("Passcode (leave empty to look without adding)", key="mb_open_key", type="password", placeholder="ABCD-EFGH")
        if st.button("Open account", key="mb_open", disabled=not who.strip()):
            surname, number, perr = pl.parse_account_name(who)
            if perr:
                st.error(perr)
            else:
                h = pl.account_key(pl.surname_slug(surname, number))
                found = logs.get(h)
                if not found:
                    st.error(f"No account is filed under “{pl.display_for(surname, number)}” yet. Check the spelling (and the number, if two people share the name), or create it on the next tab. "
                             "An account created in the last few minutes may not be published yet.")
                elif wkey.strip() and not (log_signing.valid_key_shape(wkey) and ui._write_pub(log_signing.normalize_key(wkey), h) == found.get("write_pub")):
                    st.error("That passcode does not belong to this account. Check it, or look at the account without adding by leaving the passcode empty.")
                else:
                    ui.select_account(surname, number, write_key=log_signing.normalize_key(wkey) if wkey.strip() else None)
                    st.rerun()
    with tab_new:
        mine = st.text_input("Your last name", key="mb_new_name", max_chars=pl.MAX_NAME_LEN, placeholder="for example Burnett")
        surname, serr = pl.clean_surname(mine) if mine.strip() else (None, None)
        if serr:
            st.caption(serr)
        taken = [lg.get("slug") for lg in logs.values() if lg.get("slug")]
        number = pl.next_free_number(taken, surname) if surname else 1
        will_be = pl.display_for(surname, number) if surname else ""
        if surname and number > 1:
            st.warning(f"There is already an account for {surname}. **If that is you, open it on the other tab instead.** If you are a different person, this creates a separate account called "
                       f"**{will_be}** with its own passcode and its own \\$500; remember the number when you open it.")
        elif surname:
            st.caption(f"This will create the account **{will_be}**, starting with \\$500 of paper cash.")
        if not order_client.personal_write_token(getattr(st, "secrets", {})):
            st.caption("Creating accounts is not switched on yet (the app has no write credential). It will work as soon as the owner adds one.")
        elif st.button(f"Create account “{will_be}”" if will_be else "Create account", key="mb_create", type="primary", disabled=not surname):
            slug = pl.surname_slug(surname, number)
            h = pl.account_key(slug)
            if h in logs:
                st.error("That account was just created by someone else. Reload and try again.")
            else:
                passcode = log_signing.new_passcode()
                creation = {"creation_id": order_client.new_order_id().replace("ord_", "crt_"), "display_name": surname, "slug": slug,
                            "write_pub": ui._write_pub(log_signing.normalize_key(passcode), h)}
                order = order_client.build_personal_order(None, order_id=order_client.new_order_id(), log_hash=h, page_generated_at=doc.get("generated_at_utc"),
                                                          stake=pl.DEFAULT_STAKE, create=creation, kind=pl.TYPE_CREATE)
                order["via"] = "direct"
                res = ui.file_order(log_signing.sign(order, log_signing.normalize_key(passcode), h))
                if not res["ok"]:
                    st.error(res["error"])
                else:
                    st.session_state["_personal_pending_creation"] = {"order_id": order["order_id"], "passcode": passcode, "name": will_be, "pub": creation["write_pub"]}
                    ui.select_account(surname, number, creation=creation, write_key=log_signing.normalize_key(passcode))
                    st.rerun()

fresh = st.session_state.get("_personal_pending_creation")
if fresh and fresh.get("passcode"):
    ui.banner(f"<b>Save your passcode now — this is the only time it is shown.</b> Your account is <b>{ui.esc(fresh['name'])}</b>; the passcode is the only thing that lets you add bets to it. "
              "If the page is closed before you save it, it cannot be recovered.", "warn")
    st.code(f"Account: {fresh['name']}\nPasscode: {fresh['passcode']}", language=None)
    if st.button("I have saved it", key="mb_saved"):
        fresh.pop("passcode", None)
        st.rerun()
if fresh and log and log["doc"] is not None and fresh.get("pub") and (log["doc"].get("write_pub") != fresh["pub"]):
    ui.banner(f"Someone else created <b>{ui.esc(log['name'])}</b> a moment before you did, so your request was not applied. Create it again to get the next number.", "warn")

legacy = doc.get("unclaimed_legacy") or {}
if legacy.get("tickets"):
    with st.expander(f"The earlier manual ticket ({legacy['tickets']}) — what it is and how to claim it"):
        st.markdown(
            f"Before personal accounts existed, {legacy['tickets']} ticket(s) were added by hand and sat inside the model's book. They were **moved out of the model's accounting** "
            f"(the original ledger row is untouched and still on file) and now wait here, owned by nobody: result(s) {', '.join(r.title() for r in legacy.get('results', []))}, "
            f"settled P&L {ui.signed_money(legacy.get('settled_pnl'))}. Claiming puts them in your account once: the settled result counts toward your cash exactly once. To claim, open your account "
            "with its passcode, then enter the single-use claim phrase below. The owner gets the phrase by running `python3 -m operational.personal_logs issue-claim-phrase` "
            "on the engine Mac (it is saved to a file, never shown).".replace("$", "\\$"))
        if log and log["can_write"]:
            phrase = st.text_input("Claim phrase", key="mb_claim_phrase", type="password")
            if st.button("Claim into this account", key="mb_claim", disabled=not phrase.strip() or not order_client.personal_write_token(getattr(st, "secrets", {}))):
                order = order_client.build_claim_order(order_id=order_client.new_order_id(), log_hash=log["hash"], claim_proof=pl.claim_proof(phrase, log["hash"]),
                                                       page_generated_at=doc.get("generated_at_utc"))
                order["via"] = "direct"
                res = ui.file_order(log_signing.sign(order, log["write_key"], log["hash"]))
                (st.success("Claim sent — it is applied on the engine's next pass (a few minutes).") if res["ok"] else st.error(res["error"]))
        else:
            st.caption("Open your account with its passcode first (above), then this box appears.")

if log is None:
    st.subheader("Model book at a glance")
    model_section()
    st.stop()

if not log["can_write"]:
    ui.banner("This account is open <b>view-only</b>: " + ("the passcode does not match. " if log["key_given"] else "no passcode was entered. ")
              + "You can read it, but adding bets needs the passcode (open it again with the passcode).", "info")
top = st.columns([4, 1])
top[0].markdown(f"### {ui.esc(log['name'])}")
if top[1].button("Switch account", key="mb_switch"):
    ui.forget_log()
    st.rerun()
view = st.radio("Show", ["My bets", "Model bets", "Both"], horizontal=True, key="mb_view",
                help="My bets: your own account. Model bets: the engine's \\$500 book. Both: side by side with separate totals — never added together.")
if view == "My bets":
    my_log_section(log)
elif view == "Model bets":
    model_section()
else:
    left, right = st.columns(2)
    with left:
        st.markdown("#### My account")
        my_log_section(log, compact=True)
    with right:
        st.markdown("#### Model book")
        model_section(compact=True)
    st.caption("The two sets of numbers are kept separate on purpose: your bets are not part of the model's record, and the model's tickets are not in your account.")
