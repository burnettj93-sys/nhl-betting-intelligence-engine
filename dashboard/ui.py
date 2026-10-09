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

TONES = {"good": ("#16352a", "#2f7a57", "#9be8bf"), "warn": ("#3a3115", "#8a6d1f", "#f3d479"),
         "bad": ("#3f2226", "#92404a", "#f5a9b0"), "info": ("#1b2d4d", "#3a64a8", "#a9c9ff"),
         "muted": ("#252d3b", "#3b465a", "#b5bfd1")}
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


def _utcnow() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def price_staleness(quoted_at: str | None, game_start_utc: str | None, now: dt.datetime | None = None) -> dict:
    """Is a displayed price past its freshness limit RIGHT NOW (judged when the page is opened, from the provider's quote time, not when the engine last ran)?
    The limit is the engine's own: 180 minutes, 90 minutes within 4 hours of puck drop (operational/source_status.moneyline_limit_min). A price with no
    quote time is treated as stale. Stale prices are never used for tickets; this only makes that visible."""
    from operational import source_status as ss
    now = now or _utcnow()
    t, start = parse_utc(quoted_at), parse_utc(game_start_utc)
    if t is None:
        return {"stale": True, "age_min": None, "limit_min": None}
    limit = ss.moneyline_limit_min(now, [start] if start else [])
    age = (now - t).total_seconds() / 60.0
    return {"stale": age > limit, "age_min": age, "limit_min": limit}


def age_short(minutes: float | None) -> str:
    if minutes is None:
        return "age unknown"
    return f"{minutes:.0f} min" if minutes < 120 else f"{minutes / 60:.1f} h"


def moneyline_text(g: dict, now: dt.datetime | None = None) -> tuple[str, bool]:
    """(text, stale) for a game's DraftKings moneyline: the last quote, plainly marked STALE with its age when it is past its limit."""
    ml = g.get("moneyline") or {}
    a, h = ml.get("away") or {}, ml.get("home") or {}
    if not a or not h:
        return "no quote on file", False
    sa = price_staleness(a.get("quote_captured_at_utc"), g.get("start_utc"), now)
    sh = price_staleness(h.get("quote_captured_at_utc"), g.get("start_utc"), now)
    stale = sa["stale"] or sh["stale"]
    text = f"{american(a.get('american'))} / {american(h.get('american'))}"
    if stale:
        text += f"  ⚠ STALE — quoted {age_short(max(x for x in (sa['age_min'], sh['age_min']) if x is not None) if any(x is not None for x in (sa['age_min'], sh['age_min'])) else None)} ago"
    return text, stale


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
    return (f"<span style='display:inline-block;background:{bg};border:1px solid {border};color:{fg};border-radius:999px;"
            f"padding:2px 10px;font-size:0.76rem;font-weight:650;letter-spacing:.01em;margin-right:4px'>{text}</span>")


def status_chip(status: str) -> str:
    return chip(STATUS_TEXT.get(status, status.replace("_", " ").title()), STATUS_TONE.get(status, "muted"))


def banner(text: str, tone: str = "info") -> None:
    bg, border, fg = TONES[tone]
    st.markdown(f"<div style='border:1px solid {border};border-radius:12px;padding:10px 16px;background:{bg};color:{fg};"
                f"font-size:0.88rem;line-height:1.45;margin:4px 0 12px 0'>{text}</div>", unsafe_allow_html=True)


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

def _h(text) -> str:
    """Text for inside a raw-HTML block: HTML-escaped, and `$` as an entity so Streamlit's markdown never reads a pair of prices as maths."""
    import html
    return html.escape(str(text)).replace("$", "&#36;")


def _card_head(title: str, chips: str) -> None:
    st.markdown(f"<div class='card-head'><div class='card-title'>{_h(title)}</div><div class='card-chips'>{chips}</div></div>", unsafe_allow_html=True)


def _slip(legs: list[dict], stats: list[tuple], *, outcomes: bool) -> bool:
    """Renders the slip; returns True when any displayed price is past its freshness limit at this moment (never true for a recorded ticket's frozen prices)."""
    any_stale = False
    """The bet slip: one row per selection (what, which game, the price as a pill, the model's chance and the edge it implies) and a stat strip."""
    rows = []
    for l in legs:
        d = 1.0 + (l["american_price"] / 100.0 if l["american_price"] > 0 else 100.0 / abs(l["american_price"]))
        implied = 1.0 / d
        edge = (l.get("probability") - implied) * 100 if l.get("probability") is not None else None
        game = " vs ".join(x for x in (l.get("team"), l.get("opponent")) if x)
        when = et_time(l.get("game_start_utc"))
        quote = l.get("quote_updated_utc") or l.get("retrieved_at_utc") or l.get("price_captured_at_utc")
        stale = price_staleness(quote, l.get("game_start_utc")) if not outcomes else {"stale": False}
        any_stale = any_stale or stale["stale"]
        meta = " · ".join(x for x in (game, when if when != "—" else None,
                                      (f"⚠ STALE — quoted {age_short(stale['age_min'])} ago, past its {age_short(stale['limit_min'])} limit" if stale["stale"] else
                                       f"quote {age_text(quote).replace(' ago', '')} old" if (not outcomes and quote and l.get("quote_age_min") is not None) else None)) if x)
        res = {"WIN": ("won", "Won"), "LOSS": ("lost", "Lost"), "VOID": ("void", "Void")}.get(l.get("outcome") or "") if outcomes else None
        chance = f"Model {pct(l.get('probability'))}" + (f" <span class='edge {'pos' if edge >= 0.5 else 'neg' if edge <= -0.5 else 'flat'}'>{edge:+.0f} pts vs price</span>" if edge is not None else "")
        if edge is not None and edge < 1.0:
            chance += " <span class='edge flat'>· no edge of its own</span>"
        rows.append(f"<div class='leg {res[0] if res else ''}{' stale' if stale['stale'] else ''}'><div class='leg-main'><div class='leg-sel'>{_h(l['label'])}</div><div class='leg-meta'>{_h(meta)}</div></div>"
                    f"<div class='leg-right'><span class='odds'>{american(l['american_price'])}</span><span class='chance'>{chance}</span>"
                    + (f"<span class='res {res[0]}'>{res[1]}</span>" if res else "") + "</div></div>")
    cells = "".join(f"<div class='stat {tone}'><span class='k'>{_h(k)}</span><span class='v'>{_h(v)}</span>" + (f"<span class='sub'>{_h(sub)}</span>" if sub else "") + "</div>"
                    for k, v, sub, tone in stats)
    st.markdown(f"<div class='slip'>{''.join(rows)}<div class='stats'>{cells}</div></div>", unsafe_allow_html=True)
    return any_stale


def ticket_card(t: dict, *, show_account_note: bool = False) -> None:
    status = t["status"]
    with st.container(border=True):
        legs = t["legs"]
        title = " + ".join(l["label"] for l in legs) if len(legs) <= 2 else f"{len(legs)}-leg ticket"
        _card_head(title, status_chip(status) + status_chip(t.get("origin") or "AUTOMATIC"))
        res = t.get("result")
        pnl = res.get("profit_loss") if res else None
        decimal = t.get("combined_decimal") or 1.0
        _slip(legs, [("Estimated price" if len(legs) > 1 else "DraftKings quote", american(t["combined_american"]), "product of leg prices" if len(legs) > 1 else "US feed", "accent"),
                     ("Stake", money(t["stake"]), None, ""),
                     ("To return", money(t["potential_return"]), f"profit {money(t['potential_profit'])}" if t.get("potential_profit") is not None else None, ""),
                     ("Model chance", pct(t.get("hit_probability")), f"price implies {pct(1.0 / decimal)}" if decimal else None, ""),
                     ("Result", signed_money(pnl) if pnl is not None else "Open", None, ("good" if (pnl or 0) > 0 else "bad" if (pnl or 0) < 0 else "") if pnl is not None else "open")], outcomes=True)
        st.caption(esc(t["rationale"]))
        leg_why(legs)
        if t.get("recorded_at_utc"):
            st.caption(f"Recorded {et_time(t['recorded_at_utc'], True)} · ticket {t['ticket_id']} · prices and probabilities are frozen at that moment.")
        prov = t.get("provenance")
        if prov:
            st.caption(f"Manually added from order {prov.get('order_id')} (received {et_time(prov.get('received_at_utc'), True)}, "
                       f"revalidated {et_time(prov.get('revalidated_at_utc'), True)}). {prov.get('jurisdiction_note', '')}")
        for a in t.get("alerts") or []:
            if a.get("retracted"):
                st.caption(f"Alert retracted — {esc(a['retracted'])} (original: {esc(a['detail'])})")
            else:
                st.warning(esc(a["detail"]))


STATUS_WORDS = {"SELECTED": "Taken", "BLOCKED_LEG_LIMIT": "Skipped — leg limit", "BLOCKED_PLAYER_LIMIT": "Skipped — player limit", "BLOCKED_GAME_LIMIT": "Skipped — game limit",
                "BLOCKED_RECORDING_WINDOW": "Held — recording window", "BLOCKED_OPPOSITE_SIDE": "Skipped — opposite side held", "ALREADY_RECORDED": "Already on the book",
                "NOT_REACHED": "Not needed — slots full"}


def selection_report(tk: dict, *, quiet: bool = False) -> None:
    """Why these tickets and not the others: every qualifying ticket in hit-chance order with what happened to it. Estimates, not findings."""
    audit = ((tk.get("diagnostics") or {}).get("selection_audit") or {})
    cycles = audit.get("recording_cycles_today") or []
    cyc = cycles[-1] if cycles else audit.get("latest_cycle")
    if not cyc:
        return
    if not cyc.get("considered"):
        if quiet:
            return
        st.caption(f"No ticket qualified in the latest selection pass ({et_time(cyc['at_utc'], True)}): {cyc.get('pool_legs', 0)} eligible leg(s), {cyc.get('qualifying', 0)} qualifying ticket(s). "
                   "The report lists every qualifying ticket, and why each was taken or skipped, as soon as any qualify.")
        return
    with st.expander("Why these tickets — and the higher-hit alternatives that were not taken"):
        st.caption(f"{cyc['qualifying']} tickets qualified from {cyc['pool_legs']} eligible legs ({et_time(cyc['at_utc'], True)}). They are ranked by **estimated hit chance** (then value), "
                   "so a longer-priced ticket is taken only when every higher-hit one is already taken or would put too much on one leg, player or game. "
                   "Nothing is lowered to fill a slot, and a slot with no qualifying ticket stays empty.")
        rows = ["| Hit chance | Price | Value | After haircut | Ticket | What happened |", "|---|---|---|---|---|---|"]
        for c in cyc["considered"][:18]:
            what = STATUS_WORDS.get(c["status"], c["status"]) + (f": {c['reason']}" if c.get("reason") and c["status"] != "SELECTED" else "")
            legs = " + ".join(f"{l} ({american(p)})" for l, p in zip(c["legs"], c["leg_prices"]))
            rows.append(f"| **{pct(c['hit_probability'], 1)}** | {american(c['estimated_price'])} | {c['ev_estimated'] * 100:+.0f}% | {c['ev_after_haircut'] * 100:+.1f}% | {_h(legs)} | {_h(what)} |")
        st.markdown("\n".join(rows))
        st.caption("Value = estimated return per dollar; After haircut = the same with every leg's chance lowered 3 points. Individual-player model error in testing was about 8 points, "
                   "so small differences in value between tickets are not reliable; hit chance is the ranking.")


def leg_why(legs: list[dict]) -> None:
    """Short per-selection rationale (role, recent production and sample, expected output, main uncertainty) from the published product state."""
    from operational import leg_context
    try:
        players, goalies = product_source.players(), product_source.goalies()
    except Exception:  # noqa: BLE001 - the card stays valid without the extra context
        return
    shown = [(l, leg_context.for_leg(l, players, goalies)) for l in legs if l.get("market_family") != "MONEYLINE"]
    shown = [(l, c) for l, c in shown if c]
    if not shown:
        return
    with st.expander("Why this selection"):
        for l, c in shown:
            st.markdown(f"**{esc(l['label'])}**")
            st.caption(esc(" · ".join(c["lines"])))
            (st.warning if c["low_sample"] else st.caption)(esc(c["uncertainty"]))


# ------------------------------------------------------------------ best options ----

def option_card(opt: dict, *, key: str, cash: float | None, page_generated_at: str | None, show_people: bool = True) -> None:
    with st.container(border=True):
        kind = "Single" if opt["kind"] == "SINGLE" else "2-leg cross-game parlay"
        _card_head(" + ".join(l["label"] for l in opt["legs"]), chip(kind, "info"))
        quoted = opt["price_basis"] == "SPORTSBOOK_QUOTE"
        decimal = opt.get("combined_decimal") or 1.0
        ev = opt["ev_after_haircut"] * 100
        stale_any = _slip(opt["legs"], [("Quoted price" if quoted else "Estimated price", american(opt["combined_american"]), "DraftKings quote" if quoted else "product of leg prices", "accent"),
                            ("Stake", money(opt["stake"]), None, ""),
                            ("To return", money(opt["potential_return"]), f"profit {money(opt['potential_profit'])}", ""),
                            ("Model chance", pct(opt["hit_probability"]), f"price implies {pct(1.0 / decimal)}", ""),
                            ("Value after haircut", f"{ev:+.0f}%", "3-pt policy margin", "good" if ev > 0 else "bad")], outcomes=False)
        if stale_any:
            banner("<b>Stale price.</b> At least one price on this card is past its freshness limit now. It is shown for reference only: it is no longer a recommendation, and it cannot be added.", "bad")
        st.caption(esc(opt["rationale"]))
        leg_why(opt["legs"])
        if show_people and opt.get("best_for"):
            names = ", ".join(p["name"] for p in opt["best_for"])
            st.caption(f"Best option for: {names}")
        st.caption(f"{opt['price_label']}. Prices are DraftKings US-feed quotes; they have not been verified against DraftKings Ontario.")
        ontario_check(opt, key=key)
        if not stale_any:
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
                        direct, token = order_client.configured_write_access(getattr(st, "secrets", {}), signed_in_email())
                        if direct:
                            res = order_client.submit_direct(v, token)
                            st.success("Recorded — it appears after the engine's next pass.") if res["ok"] else st.error(res["error"])
                        else:
                            sess[f"{key}_{i}"] = order_client.prefilled_issue_url(v)
            if sess.get(f"{key}_{i}"):
                st.link_button("Open GitHub to file this check", sess[f"{key}_{i}"])


# ---- personal logs: the only writer in the product UI ----
# A personal log belongs to whoever knows its code. It is kept apart from the $500 model book (different database file), so nothing here can change the
# model's cash, exposure, slots or results. A code is a name for a log, not a password: see operational/personal_logs.py.

def personal_logs_doc() -> dict:
    try:
        return product_source.personal_logs() or {}
    except Exception:  # noqa: BLE001 - a missing section just means no log can be opened right now
        return {}


@st.cache_data(show_spinner=False, ttl=3600, max_entries=200)
def _code_hash(code: str) -> str:
    from operational import personal_logs
    return personal_logs.code_hash(code)


def selected_log() -> dict | None:
    """The log this browser session is working in, resolved against what is published: {"hash", "name", "exists", "creation"} or None."""
    sel = st.session_state.get("_personal_log")
    if not sel:
        return None
    doc = (personal_logs_doc().get("logs") or {}).get(sel["hash"])
    if doc:
        sel["creation"] = None                                  # the log exists now; later bets do not need the creation details
        sel["name"] = doc["display_name"]
    key = sel.get("write_key")
    # Writing is allowed only with the log's write key; it must derive the public key the log was created with (published, so a wrong key is caught here).
    pub = (doc or {}).get("write_pub") or (sel.get("creation") or {}).get("write_pub")
    can_write = bool(key and pub and _write_pub(key, sel["hash"]) == pub)
    return {"hash": sel["hash"], "name": sel["name"], "exists": bool(doc), "creation": sel.get("creation"), "doc": doc, "write_key": key if can_write else None,
            "can_write": can_write, "key_given": bool(key)}


@st.cache_data(show_spinner=False, ttl=3600, max_entries=200)
def _write_pub(write_key: str, log_hash: str) -> str:
    from operational import log_signing
    return log_signing.public_key_hex(write_key, log_hash)


def select_log(code: str, name: str | None = None, *, creation: dict | None = None, write_key: str | None = None) -> None:
    st.session_state["_personal_log"] = {"hash": _code_hash(code), "name": name or "Your log", "creation": creation, "write_key": write_key or None}


def forget_log() -> None:
    st.session_state.pop("_personal_log", None)


def _log_order(order_id: str, log_hash: str) -> dict | None:
    for o in ((personal_logs_doc().get("logs") or {}).get(log_hash) or {}).get("orders") or []:
        if o["order_id"] == order_id:
            return o
    return None


def file_order(order: dict) -> dict:
    """Creates the order in the queue. {"ok", "via", "issue"|"url"|"error"}; the credential never leaves this function."""
    token = order_client.personal_write_token(getattr(st, "secrets", {}))
    if token:
        res = order_client.submit_direct(order, token)
        return {"ok": res["ok"], "via": "direct", "issue": res.get("issue"), "error": res.get("error")}
    return {"ok": True, "via": "link", "url": order_client.prefilled_issue_url(order)}


def add_control(opt: dict, *, key: str, cash: float | None = None, page_generated_at: str | None = None) -> None:
    """Adds this option to the personal log chosen on My Bets. Called on every render, but it files an order only inside the button handlers."""
    from operational import personal_logs as pl
    log = selected_log()
    if not log:
        st.caption("To track this bet, open or create your own log on **My Bets** (sidebar) first. Personal logs never touch the model's $500 book.")
        return
    on_book = opt.get("on_book")
    if on_book:
        st.caption(f"The model book holds this exact bet as {on_book['ticket_id']} ({on_book['status'].title()}). Adding it to your own log does not change that.")
    if not log["can_write"]:
        banner(f"<b>{esc(log['name'])}</b> is open <b>view-only</b> in this session"
               + (": the write key you entered does not match this log." if log["key_given"] else ": adding a bet needs the log's write key.")
               + " Enter it on <b>My Bets</b> (sidebar). Knowing a log's code lets you read it, not write to it.", "warn")
        return
    banner(f"Adding to your personal log <b>{esc(log['name'])}</b> — separate from the model book.", "info")
    sess = st.session_state.setdefault("_personal_orders", {})
    mine = sess.get(opt["option_id"])
    if mine and mine.get("log") != log["hash"]:
        mine = None
    answer = _log_order(mine["order_id"], log["hash"]) if mine else None
    if answer is not None:
        status = answer["status"]
        if status == "RECORDED":
            banner(f"Added to <b>{esc(log['name'])}</b> as <b>{answer['bet_id']}</b> (manually added).", "good")
            return
        if status == "ALREADY_RECORDED":
            banner(f"Not added again: {esc(answer['reason'])}", "info")
            return
        if status == "NEEDS_ACCEPTANCE":
            banner("<b>Nothing was added.</b> The price or hit chance moved since you looked. Reload to see the current numbers, then add again.", "warn")
        else:
            banner(f"The engine did not add this: {esc(answer['reason'])}", "bad")
        if st.button("Dismiss", key=f"{key}_dismiss"):
            sess.pop(opt["option_id"], None)
            st.rerun()
        return
    if mine:
        banner("Order sent — waiting for the engine to revalidate and record it (usually a few minutes).", "info")
        if mine.get("url"):
            st.link_button("Open GitHub to finish filing this order", mine["url"])
            st.caption("Repository owner only: the order exists once you press “Submit new issue” on GitHub.")
        if st.button("Check status", key=f"{key}_check"):
            from dashboard import snapshot_source
            snapshot_source.current(force_refresh=True)
            st.rerun()
        return
    rules = personal_logs_doc().get("rules") or {}
    lo, hi, default = rules.get("stake_min", pl.MIN_STAKE), rules.get("stake_max", pl.MAX_STAKE), rules.get("stake_default", pl.DEFAULT_STAKE)
    stake = st.number_input("Stake (paper $)", min_value=float(lo), max_value=float(hi), value=float(default), step=5.0, key=f"{key}_stake")
    if not order_client.personal_write_token(getattr(st, "secrets", {})):
        st.caption("Adding bets from this page is not switched on yet (the app has no write credential). The button below makes a pre-filled GitHub issue that only the repository owner can submit.")
    if st.button(f"Add to {log['name']} — {money(stake)}", key=f"{key}_add", type="primary",
                 help="Files one order. The engine rechecks the price first, and tells you if it moved. Nothing is added by browsing or refreshing."):
        _submit_personal(opt, key, sess, log, float(stake), page_generated_at)


def _submit_personal(opt: dict, key: str, sess: dict, log: dict, stake: float, page_generated_at: str | None) -> None:
    if opt["option_id"] in sess:
        return                                                  # a repeated click while an order is outstanding
    order = order_client.build_personal_order(opt, order_id=order_client.new_order_id(), log_hash=log["hash"], page_generated_at=page_generated_at,
                                              stake=stake, create=None if log["exists"] else log["creation"])
    from operational import log_signing
    order["via"] = "direct" if order_client.personal_write_token(getattr(st, "secrets", {})) else "link"      # signed with the order; lets the engine tell a one-click order from a hand-filed one
    order = log_signing.sign(order, log["write_key"], log["hash"])
    res = file_order(order)
    if not res["ok"]:
        banner(esc(res["error"]), "bad")
        return
    sess[opt["option_id"]] = {"order_id": order["order_id"], "log": log["hash"], "via": res["via"], "issue": res.get("issue"), "url": res.get("url")}
    st.rerun()


# ---- player roles: estimated usage versus reported assignments (never shown under one "Line 1 / PP1" heading) ----
_PP_USAGE = {1: "High", 2: "Some"}


def est_tier(p: dict) -> str:
    return f"Tier {p['usage_tier']}" if p.get("usage_tier") else "—"


def est_pp(p: dict) -> str:
    return _PP_USAGE.get(p.get("pp_usage"), "—")


def _shown(p: dict):
    r = p.get("reported")
    return r if r and r.get("status", "REPORTED") == "REPORTED" else None


def reported_line(p: dict) -> str:
    r = _shown(p)
    return (r.get("line") or "—") if r else "—"


def reported_pp(p: dict) -> str:
    r = _shown(p)
    return (r.get("pp") or "—") if r else "—"


def app_version() -> dict:
    """The commit this running app was deployed from, read from its checkout's .git (no subprocess), or 'unknown'. Shown on Diagnostics so a stale deploy is visible."""
    from pathlib import Path
    root = Path(__file__).resolve().parent.parent
    try:
        git = root / ".git"
        if git.is_file():                                      # a worktree: ".git" is a pointer file
            git = Path(git.read_text().split("gitdir:", 1)[1].strip())
        ref = (git / "HEAD").read_text().strip()
        if len(ref) == 40:
            return {"commit": ref, "source": ".git/HEAD"}
        target = git / ref.split(" ", 1)[1]
        if target.exists():
            return {"commit": target.read_text().strip(), "source": ".git ref"}
        packed = git / "packed-refs"
        if packed.exists():
            for line in packed.read_text().splitlines():
                if line.endswith(" " + ref.split(" ", 1)[1]):
                    return {"commit": line.split()[0], "source": "packed-refs"}
    except Exception:  # noqa: BLE001
        pass
    return {"commit": "unknown", "source": None}


# ---- order-path check: proves the click-to-queue path end to end without staking anything ----

def signed_in_email() -> str | None:
    """The viewer's email from the SUPPORTED sign-in only: `st.login()` (OIDC) sets `st.user`; None when nobody has signed in. The platform's
    `X-Streamlit-User` header is deliberately not consulted (undocumented, no stability or trust guarantee)."""
    try:
        user = st.user
        if not getattr(user, "is_logged_in", False):
            return None
        if getattr(user, "email_verified", True) is False:
            return None
        email = getattr(user, "email", None)
        return str(email).strip().lower() if email else None
    except Exception:  # noqa: BLE001
        return None


def identity_probe() -> dict:
    """Names only (never values): what identity the running app can actually see, so a missing email is explained rather than guessed."""
    out = {"streamlit": getattr(st, "__version__", "?"), "is_logged_in": None, "user_fields": [], "header_names": [], "user_header": {}}
    try:
        user = st.user
        out["is_logged_in"] = getattr(user, "is_logged_in", None)
        out["user_fields"] = sorted(k for k, v in user.to_dict().items() if v not in (None, "", False))
    except Exception:  # noqa: BLE001 - diagnostic only
        pass
    try:
        v = st.context.headers.get("X-Streamlit-User")
        out["user_header"] = {"present": bool(v), "length": len(v or ""), "has_at": "@" in (v or "")}
    except Exception:  # noqa: BLE001
        pass
    try:
        out["header_names"] = sorted(h for h in st.context.headers.keys() if h.lower().startswith(("x-", "cf-")) and "cookie" not in h.lower() and "auth" not in h.lower())
    except Exception:  # noqa: BLE001
        pass
    return out


def order_path_panel() -> None:
    email = signed_in_email()
    stat = order_client.path_status(getattr(st, "secrets", {}), email)
    st.dataframe([{"Check": "Order token secret configured", "Result": "yes" if stat["write_path_configured"] else "no"},
                  {"Check": "Supported sign-in (OIDC) configured", "Result": "yes" if stat["login_configured"] else "no"},
                  {"Check": "You are signed in", "Result": f"yes ({stat['signed_in_masked']})" if stat["signed_in"] else "no"},
                  {"Check": "Allowed emails configured", "Result": str(stat["allowed_email_count"])},
                  {"Check": "You are on the allow-list", "Result": "yes" if stat["viewer_allowed"] else "no"},
                  {"Check": "One-click (direct) path ready", "Result": "yes" if stat["direct_ready"] else "no — the click would open a pre-filled GitHub issue instead"}],
                 hide_index=True, width="stretch")
    if stat["login_configured"] and not stat["signed_in"]:
        if st.button("Sign in to enable one-click adding", key="order_signin"):
            st.login()
    elif stat["signed_in"]:
        if st.button("Sign out", key="order_signout"):
            st.logout()
    elif not stat["login_configured"]:
        st.info("This panel is about the owner-only evidence records (goalie confirmations, Ontario price checks): they change what everyone sees, so they need a supported sign-in "
                "(`st.login()`, OIDC; steps in docs/MANUAL_ORDERS.md) because Streamlit says the platform's own viewer header is not a supported identity. "
                "**Personal-log adding is different:** it needs no sign-in, only the app's write credential (`LOG_WRITE_TOKEN`, docs/PERSONAL_LOGS.md) plus the log's own write key.")
    st.caption("Booleans only: the token and the allow-list are never displayed. This check creates no order, ticket or stake.")
    probe = identity_probe()
    st.caption(f"What the platform exposes (names only, no values; none of it is used for authorisation): Streamlit {probe['streamlit']}; `st.user.is_logged_in` = {probe['is_logged_in']}; "
               f"`X-Streamlit-User` header = {probe['user_header'] or 'absent'} (an opaque value that Streamlit says is not a supported identity).")
    sess = st.session_state.setdefault("_path_checks", {})
    if st.button("Run non-staking order-path check", key="path_check_run"):
        cid = order_client.new_order_id().replace("ord_", "chk_")
        doc = order_client.build_path_check(check_id=cid, sent_at_utc=dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                                            via="direct" if stat["direct_ready"] else "link", signed_in=stat["signed_in"],
                                            viewer_allowed=stat["viewer_allowed"], write_path_configured=stat["write_path_configured"])
        if stat["direct_ready"]:
            _, token = order_client.configured_write_access(getattr(st, "secrets", {}), email)
            res = order_client.submit_direct(doc, token)
            if res["ok"]:
                sess[cid] = {"via": "direct", "issue": res["issue"]}
            else:
                st.error(res["error"])
        else:
            sess[cid] = {"via": "link", "url": order_client.prefilled_issue_url(doc)}
        st.rerun()
    results = {r.get("check_id"): r for r in product_source.path_checks() if r.get("check_id")}
    for cid, rec in sess.items():
        got = results.get(cid)
        if got:
            banner(f"Check <b>{cid}</b>: <b>{got['status']}</b> at {got['processed_at_utc']} (sent {rec['via']}). {got['note']}", "good")
        elif rec["via"] == "link":
            banner(f"Check <b>{cid}</b> is not filed yet: the direct path is not ready, so it needs the GitHub page.", "warn")
            st.link_button("Open GitHub to file this check", rec["url"])
        else:
            banner(f"Check <b>{cid}</b> sent (issue {rec['issue']}). Waiting for the engine's next queue pass (about 2 minutes, then a snapshot refresh).", "info")
    if sess and st.button("Refresh result", key="path_check_refresh"):
        from dashboard import snapshot_source
        snapshot_source.current(force_refresh=True)
        st.rerun()
    recent = [r for r in product_source.path_checks() if r.get("check_id")]
    if recent:
        st.markdown("**Checks the engine has answered**")
        st.dataframe([{"Check": r["check_id"], "Result": r["status"], "Answered (UTC)": r.get("processed_at_utc"), "Sent via": r.get("via"),
                       "Filed by": r.get("author"), "Viewer on allow-list when sent": "yes" if r.get("viewer_allowed") else "no"} for r in reversed(recent[-8:])],
                     hide_index=True, width="stretch")
    ign = [r for r in product_source.path_checks() if r["status"] == "IGNORED_AUTHOR"]
    for r in ign[-3:]:
        banner(esc(r["note"]), "bad")
