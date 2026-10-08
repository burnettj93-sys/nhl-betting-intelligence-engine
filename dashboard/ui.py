"""
Shared presentation for the product pages: headers with data freshness, truthful unavailable states, small formatters, the
ticket card and the best-option card with its explicit "Add to paper book -- $10" control.

Rules the helpers keep: every number shown has its source and time next to it somewhere on the page; an estimated combined
price is labelled estimated and a quoted single price is labelled as a quote; nothing here writes anything except the add
control, and that only inside a button handler.
"""
from __future__ import annotations

import datetime as dt

import streamlit as st

from dashboard import order_client, product_source
from operational import eastern_time as et
from operational import runtime_mode

TONES = {"good": ("#12301e", "#2f6a48", "#8fe0b0"), "warn": ("#3a2f12", "#6b5417", "#f0cf6a"),
         "bad": ("#3d1d1d", "#7a2f2f", "#f0a0a0"), "info": ("#14243d", "#2f4f80", "#9cc2f5"),
         "muted": ("#1c212b", "#3a3f4b", "#aab2c5")}
STATUS_TONE = {"RECORDED": "info", "PENDING": "info", "WON": "good", "LOST": "bad", "VOID": "muted", "UNRESOLVED": "warn",
               "RECOMMENDED": "warn", "FINAL": "muted", "SCHEDULED": "info", "STARTED": "warn", "UNCONFIRMED": "warn",
               "CONFIRMED": "good", "AUTOMATIC": "muted", "MANUALLY_ADDED": "info"}
STATUS_TEXT = {"STARTED": "Started — awaiting final", "MANUALLY_ADDED": "Manually added", "AUTOMATIC": "Automatic"}


# ------------------------------------------------------------------ formatting ----

def american(price) -> str:
    if price is None:
        return "—"
    p = int(round(price))
    return f"+{p}" if p > 0 else str(p)


def pct(p, digits: int = 0) -> str:
    return "—" if p is None else f"{p * 100:.{digits}f}%"


def money(v) -> str:
    if v is None:
        return "—"
    return f"{'-' if v < 0 else ''}${abs(v):,.2f}"


def signed_money(v) -> str:
    if v is None:
        return "—"
    return f"{'+' if v > 0 else '-' if v < 0 else ''}${abs(v):,.2f}"


def parse_utc(s: str | None) -> dt.datetime | None:
    if not s:
        return None
    t = dt.datetime.fromisoformat(s.replace("Z", "+00:00"))
    return t if t.tzinfo else t.replace(tzinfo=dt.timezone.utc)


def et_time(s: str | None, with_date: bool = False) -> str:
    t = parse_utc(s)
    if t is None:
        return "—"
    return t.astimezone(et.EASTERN).strftime("%b %-d, %-I:%M %p ET" if with_date else "%-I:%M %p ET")


def age_text(s: str | None, now: dt.datetime | None = None) -> str:
    t = parse_utc(s)
    if t is None:
        return "unknown age"
    mins = ((now or dt.datetime.now(dt.timezone.utc)) - t).total_seconds() / 60.0
    if mins < 1:
        return "just now"
    if mins < 120:
        return f"{mins:.0f} min ago"
    if mins < 48 * 60:
        return f"{mins / 60:.1f} h ago"
    return f"{mins / 1440:.0f} d ago"


def esc(text) -> str:
    """Streamlit renders $...$ as math: escape dollar signs in free text."""
    return str(text).replace("$", "\\$")


def chip(text: str, tone: str = "muted") -> str:
    bg, border, fg = TONES.get(tone, TONES["muted"])
    return (f"<span style='display:inline-block;background:{bg};border:1px solid {border};color:{fg};border-radius:10px;"
            f"padding:1px 9px;font-size:0.78rem;font-weight:600;margin-right:4px'>{text}</span>")


def status_chip(status: str) -> str:
    return chip(STATUS_TEXT.get(status, status.replace("_", " ").title()), STATUS_TONE.get(status, "muted"))


def banner(text: str, tone: str = "info") -> None:
    bg, border, fg = TONES[tone]
    st.markdown(f"<div style='border:1px solid {border};border-radius:8px;padding:8px 14px;background:{bg};color:{fg};"
                f"font-size:0.86rem;margin:4px 0 10px 0'>{text}</div>", unsafe_allow_html=True)


# ------------------------------------------------------------------ page chrome ----

def unavailable(reason: str, what: str = "This page") -> None:
    banner(f"<b>{what} is unavailable.</b> {esc(reason)}", "bad")


def load(fn, what: str = "This page"):
    """Calls a product_source accessor; on failure shows the truthful unavailable state and stops the page."""
    try:
        return fn()
    except product_source.Unavailable as exc:
        unavailable(str(exc), what)
        st.stop()


def header(title: str, subtitle: str | None = None, *, meta: dict | None = None) -> None:
    st.title(title)
    if subtitle:
        st.caption(subtitle)
    try:
        meta = meta or product_source.meta()
    except product_source.Unavailable as exc:
        unavailable(str(exc), "The data feed")
        return
    through = meta.get("data_through") or {}
    gen = meta.get("generated_at_utc")
    gen_t = parse_utc(gen)
    stale = gen_t is not None and (dt.datetime.now(dt.timezone.utc) - gen_t).total_seconds() > 45 * 60
    bits = [f"Engine update {et_time(gen)} ({age_text(gen)})", f"Eastern date {meta.get('et_today')}",
            f"Player logs through {through.get('skaters') or 'n/a'}", f"Results through {et_time(through.get('schedule_results'), True)}"]
    if runtime_mode.is_community_cloud():
        try:
            from dashboard import cloud_snapshot
            fr = cloud_snapshot.freshness()
            if fr.get("state") in ("STALE", "VERY_STALE", "UNAVAILABLE"):
                banner(f"<b>Data freshness: {fr['state'].replace('_', ' ')}.</b> The newest data in the published feed is from {et_time(fr.get('data_as_of'), True)}"
                       f" ({age_text(fr.get('data_as_of'))}). Do not treat it as live.", "bad" if fr["state"] != "STALE" else "warn")
            if fr.get("source") == "REMOTE_LAST_KNOWN_GOOD":
                banner(f"<b>The latest data update failed</b> ({esc(fr.get('last_error') or 'unknown cause')}). Showing the last good "
                       f"data, published {et_time(fr.get('last_updated'), True)}.", "warn")
        except Exception:  # noqa: BLE001 - the banner is informational
            pass
    st.caption(" · ".join(bits))
    if stale:
        banner(f"The data on this page is {age_text(gen).replace(' ago', '')} old. The engine refreshes it about every 15 minutes while the "
               "Mac that runs it is awake; prices may have moved.", "warn")


def team_label(code: str) -> str:
    return code


# ------------------------------------------------------------------ tickets ----

def ticket_card(t: dict, *, show_account_note: bool = False) -> None:
    status = t["status"]
    with st.container(border=True):
        top = st.columns([5, 2])
        legs = t["legs"]
        title = " + ".join(l["label"] for l in legs) if len(legs) <= 2 else f"{len(legs)}-leg ticket"
        top[0].markdown(f"**{esc(title)}**")
        top[1].markdown(f"<div style='text-align:right'>{status_chip(status)}{status_chip(t.get('origin') or 'AUTOMATIC')}</div>",
                        unsafe_allow_html=True)
        rows = []
        for l in legs:
            matchup = " vs ".join(x for x in (l.get("team"), l.get("opponent")) if x)
            rows.append({"Selection": l["label"], "Game": matchup, "Starts": et_time(l.get("game_start_utc")),
                         "Price": american(l["american_price"]),
                         "Quote updated": et_time(l.get("quote_updated_utc") or l.get("price_captured_at_utc"), True),
                         "Model chance": pct(l.get("probability")),
                         "Result": {"WIN": "Won", "LOSS": "Lost", "VOID": "Void"}.get(l.get("outcome") or "", "—")})
        st.dataframe(rows, hide_index=True, width="stretch")
        c = st.columns(5)
        basis = "Estimated combined price" if len(legs) > 1 else "DraftKings quote"
        c[0].metric(basis, american(t["combined_american"]),
                    help="Product of each leg's own price, not a quoted parlay price." if len(legs) > 1 else
                    "The sportsbook's own price for this single (US feed).")
        c[1].metric("Stake", money(t["stake"]))
        c[2].metric("Return if it hits", money(t["potential_return"]))
        c[3].metric("Model hit chance", pct(t.get("hit_probability")))
        res = t.get("result")
        c[4].metric("Result", signed_money(res["profit_loss"]) if res and res.get("profit_loss") is not None else "Open")
        st.caption(esc(t["rationale"]))
        if t.get("recorded_at_utc"):
            st.caption(f"Recorded {et_time(t['recorded_at_utc'], True)} · ticket {t['ticket_id']} · prices and probabilities are frozen at that moment.")
        prov = t.get("provenance")
        if prov:
            st.caption(f"Manually added from order {prov.get('order_id')} (received {et_time(prov.get('received_at_utc'), True)}, "
                       f"revalidated {et_time(prov.get('revalidated_at_utc'), True)}). {prov.get('jurisdiction_note', '')}")
        for a in t.get("alerts") or []:
            st.warning(esc(a["detail"]))


# ------------------------------------------------------------------ best options ----

def option_card(opt: dict, *, key: str, cash: float | None, page_generated_at: str | None, show_people: bool = True) -> None:
    with st.container(border=True):
        top = st.columns([5, 2])
        kind = "Single" if opt["kind"] == "SINGLE" else "2-leg cross-game parlay"
        top[0].markdown("**" + esc(" + ".join(l["label"] for l in opt["legs"])) + "**")
        top[1].markdown(f"<div style='text-align:right'>{chip(kind, 'info')}</div>", unsafe_allow_html=True)
        rows = [{"Selection": l["label"], "Game": " vs ".join(x for x in (l.get("team"), l.get("opponent")) if x),
                 "Starts": et_time(l.get("game_start_utc")), "Price": american(l["american_price"]),
                 "Quote updated": et_time(l.get("quote_updated_utc"), True), "Quote age": f"{l['quote_age_min']:.0f} min" if l.get("quote_age_min") is not None else "—",
                 "Model chance": pct(l["probability"])} for l in opt["legs"]]
        st.dataframe(rows, hide_index=True, width="stretch")
        c = st.columns(5)
        quoted = opt["price_basis"] == "SPORTSBOOK_QUOTE"
        c[0].metric("Quoted price" if quoted else "Estimated price", american(opt["combined_american"]),
                    help=opt["price_label"])
        c[1].metric("Stake", money(opt["stake"]))
        c[2].metric("Return if it hits", money(opt["potential_return"]))
        c[3].metric("Model hit chance", pct(opt["hit_probability"]))
        c[4].metric("Value after haircut", f"{opt['ev_after_haircut'] * 100:+.0f}%",
                    help="Expected return per dollar after lowering every leg's probability by 3 points — a policy margin, not a calibration.")
        st.caption(esc(opt["rationale"]))
        if show_people and opt.get("best_for"):
            names = ", ".join(p["name"] for p in opt["best_for"])
            st.caption(f"Best option for: {names}")
        st.caption(f"{opt['price_label']}. Prices are DraftKings US-feed quotes; they have not been verified against DraftKings Ontario.")
        ontario_check(opt, key=key)
        add_control(opt, key=key, cash=cash, page_generated_at=page_generated_at)


def ontario_check(opt: dict, *, key: str) -> None:
    """Shows any recorded manual Ontario price check for each leg and offers a short form to record a new one."""
    try:
        checks = product_source.ontario_verifications()
    except Exception:  # noqa: BLE001
        checks = []
    by_leg = {}
    for c in checks:
        by_leg.setdefault((c["game_id"], c["participant_id"], c["market_family"], c["threshold"], c["side"]), c)
    lines = []
    for l in opt["legs"]:
        c = by_leg.get((str(l["game_id"]), str(l["participant_id"]), l["market_family"], l["threshold"], l["side"]))
        if c:
            lines.append(f"{l['label']}: Ontario {american(c['ontario_price'])} seen {et_time(c['observed_at_utc'], True)} ({c['where_seen']}) vs US feed {american(l['american_price'])}")
    if lines:
        st.caption("Ontario spot check — " + " · ".join(esc(x) for x in lines))
    with st.expander("Check this on DraftKings Ontario"):
        st.caption("Prices here come from the US feed. Open the same selection in DraftKings Ontario and record the price you see; it is stored as evidence "
                   "with the time and where you saw it, and does not change any ticket.")
        sess = st.session_state.setdefault("_verifications", {})
        for i, l in enumerate(opt["legs"]):
            with st.form(f"{key}_ont_{i}", clear_on_submit=False):
                st.write(l["label"])
                price = st.number_input("Ontario price (American, e.g. -135 or 120)", value=int(l["american_price"]), step=5, key=f"{key}_ont_p_{i}")
                where = st.text_input("Where you saw it", value="DraftKings Ontario app", key=f"{key}_ont_w_{i}")
                if st.form_submit_button("Record Ontario price"):
                    if abs(price) < 100:
                        st.error("American odds are at least 100 in size.")
                    else:
                        v = order_client.build_verification(l, verification_id=order_client.new_order_id().replace("ord_", "ver_"), ontario_price=float(price),
                                                            observed_at_utc=dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                                                            where_seen=where, us_price_shown=l["american_price"])
                        email = getattr(getattr(st, "user", None), "email", None)
                        direct, token = order_client.configured_write_access(getattr(st, "secrets", {}), email)
                        if direct:
                            res = order_client.submit_direct(v, token)
                            st.success("Recorded — it appears after the engine's next pass.") if res["ok"] else st.error(res["error"])
                        else:
                            sess[f"{key}_{i}"] = order_client.prefilled_issue_url(v)
            if sess.get(f"{key}_{i}"):
                st.link_button("Open GitHub to file this check", sess[f"{key}_{i}"])


def _engine_order(order_id: str) -> dict | None:
    try:
        for o in product_source.manual_orders():
            if o["order_id"] == order_id:
                return o
    except Exception:  # noqa: BLE001
        return None
    return None


def add_control(opt: dict, *, key: str, cash: float | None, page_generated_at: str | None) -> None:
    """The only writer in the product UI. Called on every render, but it files an order only inside the button handlers."""
    on_book = opt.get("on_book")
    if on_book:
        label = "an automatic ticket" if on_book["origin"] == "AUTOMATIC" else "a manually added ticket"
        banner(f"On the book as {label}: <b>{on_book['ticket_id']}</b> ({on_book['status'].title()}). A second stake on the same bet is not allowed.", "info")
        return
    sess = st.session_state.setdefault("_paper_orders", {})
    mine = sess.get(opt["option_id"])
    order = _engine_order(mine["order_id"]) if mine else None
    if order is not None:
        status = order["status"]
        if status == "RECORDED":
            banner(f"Added to the paper book as <b>{order['ticket_id']}</b> (\\$10, manually added).", "good")
            return
        if status == "ALREADY_RECORDED":
            banner(f"Not added again: {esc(order['reason'])} ({order['ticket_id']}).", "info")
            return
        if status == "REJECTED":
            banner(f"The engine did not add this: {esc(order['reason'])}", "bad")
            if st.button("Dismiss", key=f"{key}_dismiss"):
                sess.pop(opt["option_id"], None)
                st.rerun()
            return
        if status == "NEEDS_ACCEPTANCE":
            detail = order.get("detail") or {}
            ch = "; ".join(f"{c['leg']}: {c['was']} → {c['now']}" for c in detail.get("changes", [])) or "details changed"
            banner(f"<b>Nothing was added.</b> Since you looked, {esc(ch)}. New price {american(detail.get('combined_american'))}, "
                   f"hit chance {pct(detail.get('hit_probability'))}, return {money(detail.get('potential_return'))}. "
                   "Accept the new details to add it.", "warn")
            if st.button("Accept new details and add — $10", key=f"{key}_accept", type="primary"):
                _submit(opt | {k: detail[k] for k in ("legs", "combined_american", "hit_probability") if k in detail}, key, sess,
                        page_generated_at, supersedes=order["order_id"])
            return
    if mine:
        msg = "Order sent — waiting for the engine to revalidate and record it."
        banner(msg, "info")
        if mine.get("url"):
            st.link_button("Open GitHub to finish filing this order", mine["url"])
            st.caption("The order exists only after you press “Submit new issue” on GitHub.")
        if st.button("Check status", key=f"{key}_check"):
            from dashboard import snapshot_source
            snapshot_source.current(force_refresh=True)
            st.rerun()
        return
    disabled = cash is not None and cash + 1e-9 < opt["stake"]
    if disabled:
        st.caption(f"Add is unavailable: available cash {money(cash)} is below the {money(opt['stake'])} stake (no top-up).")
    if st.button("Add to paper book — $10", key=f"{key}_add", disabled=disabled, type="primary",
                 help="Files one $10 order. The engine rechecks the price first and asks you to accept any change."):
        _submit(opt, key, sess, page_generated_at)


def _submit(opt: dict, key: str, sess: dict, page_generated_at: str | None, supersedes: str | None = None) -> None:
    if opt["option_id"] in sess and not supersedes:
        return                                                  # a repeated click while an order is outstanding
    order = order_client.build_order(opt, order_id=order_client.new_order_id(), page_generated_at=page_generated_at, supersedes=supersedes)
    email = getattr(getattr(st, "user", None), "email", None)
    direct, token = order_client.configured_write_access(getattr(st, "secrets", {}), email)
    record = {"order_id": order["order_id"], "via": "direct" if direct else "link"}
    if direct:
        res = order_client.submit_direct(order, token)
        if not res["ok"]:
            banner(esc(res["error"]), "bad")
            return
        record["issue"] = res["issue"]
    else:
        record["url"] = order_client.prefilled_issue_url(order)
    sess[opt["option_id"]] = record
    st.rerun()
