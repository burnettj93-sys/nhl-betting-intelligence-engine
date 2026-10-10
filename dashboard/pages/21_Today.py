"""Today — the automatic engine's view of the Eastern-time hockey day: today's games, the (up to five) distinct +100 cross-game
tickets it recorded or recommends, and the model's $500 paper book. Personal logs are on My Bets and never appear here. Read-only."""
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import streamlit as st

from dashboard import product_source as ps
from dashboard import theme, ui

ui.header("Today", "Games, the automatic tickets, and the model's paper book — all from observed data and DraftKings quotes.")
tk = ui.load(ps.tickets, "The ticket board")
try:
    games = ps.games()
except ps.Unavailable as _exc:
    games = None
    _games_error = str(_exc)
if "account" not in tk:
    ui.unavailable("The ticket board has not been built yet; it is written by the 15-minute paper-trader job.", "The ticket board")
    st.stop()

acct, origins = tk["account"], tk.get("origins") or {}
settled_stake = (origins.get("ALL") or {}).get("settled_stake") or 0
roi = (origins.get("ALL") or {}).get("roi")
cols = st.columns(6)
cols[0].metric("Available cash", ui.money(acct["available_cash"]), help="Starting \\$500 plus settled results, minus open stakes. No top-ups.")
cols[1].metric("Open stakes", ui.money(acct["open_stakes"]), f"{acct['open_tickets']} open ticket(s)", delta_color="off")
cols[2].metric("Equity", ui.money(acct["equity"]), help="Cash plus open stakes at cost.")
cols[3].metric("Settled P&L", ui.signed_money(acct["settled_pnl"]))
cols[4].metric("ROI on settled", ui.pct(roi, 1) if roi is not None else "—", help="Settled profit divided by settled stakes.")
cols[5].metric("Tickets", acct["tickets"], help="Every automatic bet ever recorded in this account: parlay tickets plus any single bets (the moneyline pre-game job records those). Personal bets are never here.")
st.caption(f"Model book (automatic tickets only) · \\$500 start, \\$10 per ticket · ticket board updated {ui.et_time(tk['generated_at_utc'], True)} ({ui.age_text(tk['generated_at_utc'])}).")
_gen = ui.parse_utc(tk["generated_at_utc"])
if _gen is not None and (__import__("datetime").datetime.now(__import__("datetime").timezone.utc) - _gen).total_seconds() > 45 * 60:
    ui.banner(f"The ticket board is {ui.age_text(tk['generated_at_utc']).replace(' ago', '')} old. The scheduled job refreshes it every 15 minutes while the Mac that runs it is awake; prices may have moved.", "warn")
ui.book_split(origins)
if tk.get("notice"):
    ui.banner(ui.esc(tk["notice"]), "warn")
if tk.get("label"):
    st.caption(ui.esc(tk["label"]))

ui.morning_strip(tk)
today = games["et_today"] if games else tk["date_et"]
todays = [g for g in games["games"] if g["date_et"] == today and g["type"] == "REGULAR" and g["season"] == "20262027"] if games else []
st.subheader(f"Today's games — {today}")
if games is None:
    ui.unavailable(_games_error, "The schedule")
elif not todays:
    nxt = games["default_date"]
    st.info(f"No regular-season games are scheduled for {today}. Next game day: {nxt}. Open Games to browse.")
else:
    rows = []
    n_stale = 0
    for g in todays:
        ml_text, ml_stale = ui.moneyline_text(g)
        n_stale += int(ml_stale)
        score = f"{g['away_score']}–{g['home_score']}" if g["state"] == "FINAL" else "—"
        rows.append({"Time": g["start_et"], "Game": f"{g['away']} @ {g['home']}", "Status": ui.STATUS_TEXT.get(g["state"], g["state"].title()),
                     "Score": score, "Model home win": ui.pct((g.get("win_probability") or {}).get("home")),
                     "DK moneyline (away / home)": ml_text,
                     "Tickets": len(g.get("tickets") or [])})
    if n_stale:
        ui.banner(f"<b>{n_stale} of {len(todays)} moneyline prices below are stale</b> (older than their freshness limit when you opened this page). They are shown as the last quote only, "
                  "marked STALE, and are never used for a ticket; the moneyline refresh is limited by the odds-credit allowance (see Data Status).", "warn")
    st.dataframe(rows, hide_index=True, width="stretch")
    st.caption("Model home win is a display estimate (validated strength model); moneyline quotes are DraftKings US-feed prices and are not used for tickets unless fresh.")

slots = tk["slots"]
st.subheader("Automatic tickets")
st.caption(f"{slots['used']} of {slots['total']} daily slots used. Each ticket is a distinct cross-game bet at +100 or better; recorded tickets are paper bets and never change, "
           "recommended ones can still move with the next price refresh.")
for t in tk["tickets"]:
    ui.ticket_card(t)
if slots["empty"]:
    theme.empty_state("<b>%d slot(s) empty.</b> %s" % (slots["empty"], ui.esc(tk.get("empty_slot_reason") or "No further ticket qualifies right now.")),
                      "The engine leaves a slot empty rather than fill it with a ticket that fails the rules.")
    tpol = tk.get("ticket_policy")
    if tpol:
        st.caption(f"Ticket policy ({tpol['policy_id']}): {tpol['summary']}. "
                   + ("Approved by the owner." if tpol.get("approved") else "A proposal: the numbers are judgements, not validated results, and the owner has not approved them, so automatic recording stays paused.")
                   + " A slot is left empty rather than filled with a ticket that fails these rules; an empty slot is a correct answer.")
    else:
        pol = tk.get("policy") or {}
        st.caption(f"A ticket must reach +100 combined, show an estimated edge of at least {pol.get('min_estimated_ev', 0.05):.0%} and stay positive after lowering "
                   f"each leg's probability by {pol.get('leg_probability_margin', 0.03) * 100:.0f} points; one leg may sit on at most {pol.get('max_tickets_per_leg')} tickets and one game on at most "
                   f"{pol.get('max_tickets_per_game')}. Slots are left empty rather than filled with tickets that fail these rules.")

ui.provisional_section(tk)
ui.selection_report(tk, quiet=True)

ex = tk.get("exposure") or {}
if ex.get("tickets_counted"):
    with st.expander(f"Exposure across {ex['tickets_counted']} model-book ticket(s)"):
        by = ex.get("tickets_by_origin") or {}
        st.caption(f"Open stake at risk: {ui.money(ex.get('recorded_stake_at_risk'))} · "
                   + " · ".join(f"{ui.STATUS_TEXT.get(k, k)}: {v}" for k, v in by.items()) + ". " + ui.esc(ex.get("note", "")))
        shared = [p for p in ex.get("players", []) if p["count"] > 1]
        shared_g = [g for g in ex.get("games", []) if g["count"] > 1]
        if shared:
            st.markdown("**Players on more than one ticket**")
            st.dataframe([{"Player": p["player"], "Tickets": p["count"], "Ids": ", ".join(p["tickets"])} for p in shared], hide_index=True, width="stretch")
        if shared_g:
            st.markdown("**Games on more than one ticket**")
            st.dataframe([{"Game": g["matchup"], "Tickets": g["count"], "Ids": ", ".join(g["tickets"])} for g in shared_g], hide_index=True, width="stretch")
        if not shared and not shared_g:
            st.caption("No player or game appears on more than one ticket.")

st.caption("Your own bets live on **My Bets** (your personal log) and never change the model book on this page.")

if tk.get("earlier_open_tickets"):
    st.subheader("Earlier tickets still open")
    for t in tk["earlier_open_tickets"]:
        ui.ticket_card(t)
if tk.get("recent_settled"):
    st.subheader("Recently settled")
    for t in tk["recent_settled"][:5]:
        ui.ticket_card(t)
    st.caption("Full history is on Ticket History.")
