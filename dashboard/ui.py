"""
Shared presentation for the product pages: headers with data freshness, truthful unavailable states, small formatters, the
ticket card and the best-option card with its explicit "Add to paper book -- $10" control.

Rules the helpers keep: every number shown has its source and time next to it somewhere on the page; an estimated combined
price is labelled estimated and a quoted single price is labelled as a quote; nothing here writes anything except the add
control, and that only inside a button handler.
"""
from __future__ import annotations

import contextlib
import datetime as dt

import streamlit as st

from dashboard import order_client, product_source
from operational import eastern_time as et
from operational import runtime_mode
from operational.bet_labels import leg_dict_label

TONES = {"good": ("#16352a", "#2f7a57", "#9be8bf"), "warn": ("#3a3115", "#8a6d1f", "#f3d479"),
         "bad": ("#3f2226", "#92404a", "#f5a9b0"), "info": ("#1b2d4d", "#3a64a8", "#a9c9ff"),
         "muted": ("#252d3b", "#3b465a", "#b5bfd1")}
STATUS_TONE = {"PROVISIONAL": "warn", "RECORDED": "info", "PENDING": "info", "WON": "good", "LOST": "bad", "VOID": "muted", "UNRESOLVED": "warn",
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


def provisional_staleness(quoted_at: str | None, now: dt.datetime | None = None) -> dict:
    """A provisional (morning/midday) price is judged against the wider provisional limit, from the provider's quote time, when the page is opened."""
    from operational.pricing_policy import PROVISIONAL_MAX_AGE_MIN
    now = now or _utcnow()
    t = parse_utc(quoted_at)
    if t is None:
        return {"stale": True, "age_min": None, "limit_min": PROVISIONAL_MAX_AGE_MIN}
    age = (now - t).total_seconds() / 60.0
    return {"stale": age > PROVISIONAL_MAX_AGE_MIN, "age_min": age, "limit_min": PROVISIONAL_MAX_AGE_MIN}


EARLY_MAX_AGE_MIN = 12 * 60.0     # a quote for a game that is not today is current for this long; it is also stale if it was retrieved on an earlier Eastern day


def early_staleness(quoted_at: str | None, now: dt.datetime | None = None) -> dict:
    """Judged when the page is opened. An early (next-day) price is never treated as fresh because it was fresh yesterday: it must be recent AND from today's Eastern day."""
    from operational import eastern_time as et
    now = now or _utcnow()
    t = parse_utc(quoted_at)
    if t is None:
        return {"stale": True, "age_min": None, "limit_min": EARLY_MAX_AGE_MIN, "reason": "no quote time"}
    age = (now - t).total_seconds() / 60.0
    previous_day = et.eastern_today(t) != et.eastern_today(now)
    return {"stale": age > EARLY_MAX_AGE_MIN or previous_day, "age_min": age, "limit_min": EARLY_MAX_AGE_MIN, "previous_day": previous_day,
            "reason": "from an earlier Eastern day" if previous_day else ("older than the early-price limit" if age > EARLY_MAX_AGE_MIN else "")}


def early_moneyline_text(g: dict, now: dt.datetime | None = None) -> tuple[str, bool]:
    """(text, stale) for a next-day game's DraftKings moneyline."""
    ml = g.get("moneyline") or {}
    a, h = ml.get("away") or {}, ml.get("home") or {}
    if not a or not h:
        return "not posted or not on file", False
    sa, sh = early_staleness(a.get("quote_captured_at_utc"), now), early_staleness(h.get("quote_captured_at_utc"), now)
    stale = sa["stale"] or sh["stale"]
    ages = [x["age_min"] for x in (sa, sh) if x["age_min"] is not None]
    text = f"{american(a.get('american'))} / {american(h.get('american'))}"
    text += (f"  ⚠ STALE — quoted {age_short(max(ages) if ages else None)} ago") if stale \
        else f"  · early price, quoted {age_short(max(ages) if ages else None)} ago"
    return text, stale


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
    text = str(text).replace("\\$", "$").replace("$", "&#36;")      # inside this HTML block a markdown "\\$" shows its backslash (seen on a phone: "\\$500"), and a bare "$" can start math; the entity is both safe and plain
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


def standalone_page_fallback() -> None:
    """Streamlit decides between 'pages/ directory' mode and the entry script's own navigation only when a session first runs the entry script. After every restart, a visitor who opens a deep link
    (for example /Parlay_Builder) BEFORE anyone has opened the app's root address gets the page script on its own: no theme, no Eggy, and the raw list of every page file in the sidebar.
    The entry script marks its sessions (`_frame_ran`); a page that finds no mark applies the theme itself and hides the raw page list, so the first deep link looks right too. Opening the root address
    restores the full navigation."""
    if st.session_state.get("_frame_ran"):
        return
    try:
        from dashboard import theme
        theme.inject()
        st.markdown("<style>[data-testid='stSidebarNav'], [data-testid='stSidebarNavItems'] { display: none !important; }</style>", unsafe_allow_html=True)
    except Exception:  # noqa: BLE001 - a styling fallback must never break a page
        pass


def header(title: str, subtitle: str | None = None, *, meta: dict | None = None) -> None:
    standalone_page_fallback()
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
            f"Player logs through {through.get('skaters') or 'n/a'}" + (f" (box scores through {through['skaters_box_scores']})" if through.get("skaters_box_scores") else ""),
            f"Results through {et_time(through.get('schedule_results'), True)}"]
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
        banner(f"The data on this page is {age_text(gen).replace(' ago', '')} old. The engine refreshes it about every 10 minutes while the "
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
        prov = bool(l.get("provisional")) and not outcomes
        if prov:
            stale = provisional_staleness(quote)
        any_stale = any_stale or stale["stale"]
        if prov and not stale["stale"]:
            note = f"PROVISIONAL — quoted {age_short(stale['age_min'])} ago; re-checked before puck drop"
        elif stale["stale"]:
            note = f"⚠ STALE — quoted {age_short(stale['age_min'])} ago, past its {age_short(stale['limit_min'])} limit"
        else:
            note = f"quote {age_text(quote).replace(' ago', '')} old" if (not outcomes and quote and l.get("quote_age_min") is not None) else None
        ev = l.get("evidence") or {}
        record = (f"recent {ev['hits']} of last {ev['games']} games ({pct(ev.get('recent_hit_rate') if ev.get('recent_hit_rate') is not None else ev['hits'] / max(ev['games'], 1))}"
                  + (f", {ev['games_current']} this season" if ev.get("games_current") is not None else "") + f") · model {pct(ev.get('model'))}"
                  if ev.get("kind") == "player_record" and ev.get("hits") is not None
                  else "estimate = market no-vig chance" if ev.get("kind") == "market" else None)
        meta = " · ".join(x for x in (game, when if when != "—" else None, record, note) if x)
        res = {"WIN": ("won", "Won"), "LOSS": ("lost", "Lost"), "VOID": ("void", "Void")}.get(l.get("outcome") or "") if outcomes else None
        chance = f"{'Final estimate' if ev else 'Model'} {pct(l.get('probability'))}" + (f" <span class='edge {'pos' if edge >= 0.5 else 'neg' if edge <= -0.5 else 'flat'}'>{edge:+.0f} pts vs price</span>" if edge is not None else "")
        if edge is not None and edge < 1.0:
            chance += " <span class='edge flat'>· no edge of its own</span>"
        rows.append(f"<div class='leg {res[0] if res else ''}{' stale' if stale['stale'] else ''}'><div class='leg-main'><div class='leg-sel'>{_h(leg_dict_label(l))}</div><div class='leg-meta'>{_h(meta)}</div></div>"
                    f"<div class='leg-right'><span class='odds'>{american(l['american_price'])}</span><span class='chance'>{chance}</span>"
                    + (f"<span class='res {res[0]}'>{res[1]}</span>" if res else "") + "</div></div>")
    cells = "".join(f"<div class='stat {tone}'><span class='k'>{_h(k)}</span><span class='v'>{_h(v)}</span>" + (f"<span class='sub'>{_h(sub)}</span>" if sub else "") + "</div>"
                    for k, v, sub, tone in stats)
    st.markdown(f"<div class='slip'>{''.join(rows)}<div class='stats'>{cells}</div></div>", unsafe_allow_html=True)
    return any_stale


PLAYER_MODEL_ERROR = 0.08   # 1 SD of between-player error for veteran shots/points legs in the 2025-26 walk-forward (docs/SELECTOR_AUDIT.md)


def chance_range(legs: list[dict], error: float = PLAYER_MODEL_ERROR) -> tuple[float, float] | None:
    """Plausible range for the ticket's hit chance if every leg's probability is one measured player-level error (default 8 points) lower or
    higher. Legs on the same game are treated as independent here, as the estimate itself does. A range of the estimate, not a confidence interval."""
    if any(l.get("market_family") in ("MONEYLINE", "PUCK_LINE") for l in legs):
        return None   # the 8-point figure was measured for individual-player legs only
    probs = [l.get("probability") for l in legs]
    if not probs or any(p is None for p in probs):
        return None
    lo = hi = 1.0
    for p in probs:
        lo *= max(p - error, 0.0)
        hi *= min(p + error, 1.0)
    return lo, hi


def chance_range_text(legs: list[dict]) -> str | None:
    r = chance_range(legs)
    return f"range {r[0] * 100:.0f}–{r[1] * 100:.0f}%" if r else None


MARKET_WORDS = {"player_shots_on_goal_alternate": "Shots", "player_points": "Points", "player_goal_scorer_anytime": "Goals", "player_total_saves": "Saves"}
_AVAIL_WORDS = {"POSTED": "Posted", "NOT_POSTED": "Not posted by DraftKings yet", "NOT_FETCHED": "Not fetched yet", "BUDGET_BLOCKED": "Not fetched — credit budget",
                "FETCH_ERROR": "Fetch failed"}


def availability_block(tk: dict) -> dict:
    return ((tk.get("diagnostics") or {}).get("price_availability") or {})


WAVE_STATE_WORDS = {"PENDING": "Not yet due", "DUE": "Due now", "CAPTURED_ON_TIME": "Captured on time", "CAPTURED_LATE": "Captured late (after the 60-minute publish deadline)",
                    "PARTIAL": "Partly captured", "MISSED": "Missed: nothing is bought after the window", "NONE_SELECTED": "No game selected (budget)", "CLOSED": "Window closed", "CAPTURED_EARLIER": "Priced earlier today (under the earlier plan)"}


def morning_strip(tk: dict, now: dt.datetime | None = None) -> None:
    """One banner on today's timeline: the 07:45 refresh, the 08:00 analysis and league pull, and the next capture, from the published day status."""
    ds = day_status_or_none()
    if not ds or not ds.get("plan_present"):
        st.caption("Today's capture plan has not been made yet: the first 10-minute engine cycle of the day makes it.")
        return
    p, lp = ds.get("prep") or {}, ds.get("league_pulls") or {}
    comps = ", ".join(f"{k} {str(v.get('status')).lower()}" + (f" {et_time(v['finished_utc'], True)}" if v.get("finished_utc") else "") for k, v in (p.get("refresh_steps") or {}).items())
    parts = [f"07:45 refresh: {str(p.get('refresh_status') or '—').replace('_', ' ').lower()}"
             + (f" ({et_time(p['refresh_finished_utc'], True)})" if p.get("refresh_finished_utc") else "") + (f" [{comps}]" if comps else "") + (" · waiting for the network" if p.get("refresh_network_down") and p.get("refresh_status") != "DONE" else ""),
             f"whole-slate analysis: {p.get('analysis_games') or 0} of {p.get('slate_games') or 0} games"
             + (f", published {et_time(p['analysis_published_utc'], True)}" if p.get("analysis_published_utc") else ", not published yet"),
             f"league moneyline + spread pulls: {len(lp.get('made') or [])} made of {lp.get('planned') if lp.get('planned') is not None else '—'} planned"]
    nxt = next((w for w in ds.get("waves") or [] if w["state"] in ("PENDING", "DUE")), None)
    if nxt:
        parts.append(f"next player-odds capture: wave {nxt['index'] + 1}, target {et_time(nxt['target_utc'], True)} ({nxt['selected']} game(s) selected)")
    banner("<b>Today's timeline.</b> " + esc(" · ".join(parts)), "info")


def day_status_or_none() -> dict | None:
    try:
        return product_source.day_status()
    except Exception:  # noqa: BLE001 - the timeline is informational
        return None


def day_timeline(ds: dict | None = None) -> None:
    """The day's capture timeline in full: waves with their targets and states, the games each priced and with which markets, the games the credit budget excluded, and the budget with its assumptions."""
    ds = ds if ds is not None else day_status_or_none()
    if not ds or not ds.get("plan_present"):
        st.caption(esc((ds or {}).get("note") or "No capture plan for today yet."))
        return
    p, lp = ds.get("prep") or {}, ds.get("league_pulls") or {}
    st.markdown(f"**07:45 refresh** {esc(str(p.get('refresh_status') or '—').replace('_', ' ').lower())} · due {et_time(p.get('refresh_due_utc'), True)}"
                + f" &nbsp;|&nbsp; **08:00 analysis** published {et_time(p['analysis_published_utc'], True) if p.get('analysis_published_utc') else 'not yet'} for {p.get('analysis_games') or 0} of {p.get('slate_games') or 0} games"
                + f" &nbsp;|&nbsp; **League pulls** (moneyline + standard spreads, {lp.get('cost_each', '?')} credit(s) each, {'observed' if lp.get('cost_observed') else 'ASSUMED until the first charge is recorded'}): "
                + (", ".join(et_time(m['at_utc'], True) + f" ({m['credits']:g} cr)" for m in lp.get("made") or []) or "none yet") + f" of {lp.get('planned')} planned", unsafe_allow_html=True)
    rows = []
    for w in ds.get("waves") or []:
        games = ", ".join(f"{g.get('matchup') or g['game_id']}" + (" ✓" if g["selected"] else " (not selected)") for g in w["games"])
        rows.append({"Wave": w["index"] + 1, "First game": et_time(w["first_game_utc"], True), "Capture target": et_time(w["target_utc"], True), "Publish by": et_time(w["publish_deadline_utc"], True),
                     "State": WAVE_STATE_WORDS.get(w["state"], w["state"]), "Selected": w["selected"], "Credits": w["credits"], "Games": games})
    if rows:
        st.dataframe(rows, hide_index=True, width="stretch")
    detail = []
    for w in ds.get("waves") or []:
        for g in w["games"]:
            if not g["selected"]:
                continue
            for mw in g.get("missed") or []:
                detail.append(f"{g.get('matchup') or g['game_id']}: window missed ({', '.join(t for t in mw.get('tags', []) if t != 'MISSED_WINDOW') or 'no sleep or outage evidence'}). {mw.get('detail')}")
    for line in detail:
        st.warning(esc(line))
    ex = ds.get("excluded_by_budget") or []
    if ex:
        st.caption("Excluded by the credit budget (analysis only, no player prices): " + esc("; ".join(f"{e.get('matchup') or e['game_id']} (wave {e['wave'] + 1})" for e in ex)))
    b = ds.get("budget") or {}
    bal = ds.get("balance") or {}
    if bal.get("remaining") is not None:
        seen = f"{bal['remaining']} credits remaining (provider header {bal['baseline_value']} at {et_time(bal['baseline_at'], True)}" + (f", minus {bal['deducted']:g} charged since" if bal.get("deducted") else "") + ")"
    else:
        seen = f"{b.get('remaining_header_now') if b.get('remaining_header_now') is not None else b.get('remaining_header')} remaining on the provider's last header"
    line = (f"Credits: {seen} · "
            f"today's player-odds share {b.get('player_today')} · league pulls reserved {b.get('league_total')} · recovery pool {b.get('recovery_pool')} · reserve {b.get('reserve')} · "
            f"{b.get('days_left')} day(s) to the assumed reset.")
    st.caption(esc(line))
    for n in b.get("notes") or []:
        st.caption("⚠ " + esc(n))
    rec = ds.get("reconciliation") or {}
    if rec:
        st.caption("Planned vs charged today: " + esc("; ".join(f"{k} {v['planned']:g} planned / {v['actual']:g} charged in {v['calls']} call(s)" for k, v in rec.items())))


def availability_table(day_block: dict, *, only_unposted: bool = False) -> None:
    """Per game and market: why a price is or is not on file (posted / not posted by DraftKings / not fetched yet / not fetched because of the credit budget)."""
    games = (day_block or {}).get("games") or {}
    if not games:
        st.caption("No look at these games has been recorded yet.")
        return
    rows = []
    for gid, g in sorted(games.items(), key=lambda kv: (kv[1].get("start_utc") or "", kv[0])):
        row = {"Game": g.get("matchup") or gid, "Starts": et_time(g.get("start_utc")) if g.get("start_utc") else "—"}
        for key, label in MARKET_WORDS.items():
            m = (g.get("markets") or {}).get(key)
            if not m:
                row[label] = "—"
                continue
            word = _AVAIL_WORDS.get(m["status"], m["status"])
            if m["status"] == "POSTED":
                word += f" ({m.get('outcomes')} prices)"
            row[label] = f"{word} · {et_time(m.get('checked_utc'), True)}" if m.get("checked_utc") else word
        rows.append(row)
    st.dataframe(rows, hide_index=True, width="stretch")


def provisional_section(tk: dict) -> None:
    prov = tk.get("provisional") or {}
    tickets = prov.get("tickets") or []
    if not tickets:
        return
    st.subheader("Provisional recommendations")
    st.caption(esc(prov.get("note") or "") + f" As of {et_time(prov.get('as_of_utc'), True)}.")
    for t in tickets:
        ticket_card(t)


def ticket_card(t: dict, *, show_account_note: bool = False) -> None:
    status = t["status"]
    with st.container(border=True):
        legs = t["legs"]
        title = " + ".join(leg_dict_label(l) for l in legs) if len(legs) <= 2 else f"{len(legs)}-leg ticket"
        _card_head(title, status_chip(status) + status_chip(t.get("origin") or "AUTOMATIC"))
        res = t.get("result")
        pnl = res.get("profit_loss") if res else None
        decimal = t.get("combined_decimal") or 1.0
        _slip(legs, [("Estimated price" if len(legs) > 1 else "DraftKings quote", american(t["combined_american"]), "product of leg prices" if len(legs) > 1 else "US feed", "accent"),
                     ("Stake", money(t["stake"]), None, ""),
                     ("To return", money(t["potential_return"]), f"profit {money(t['potential_profit'])}" if t.get("potential_profit") is not None else None, ""),
                     ("Estimated chance" if t.get("anchor") else "Model chance", pct(t.get("hit_probability")),
                      (f"range {t['anchor']['hit_low'] * 100:.0f}–{t['anchor']['hit_high'] * 100:.0f}% · price implies {pct(1.0 / decimal)}" if t.get("anchor") else
                       (f"{chance_range_text(legs) or ''} · price implies {pct(1.0 / decimal)}").strip(" ·") if decimal else chance_range_text(legs)), ""),
                     ("Result", signed_money(pnl) if pnl is not None else ("Not recorded" if t.get("provisional") else "Open"), None,
                      ("good" if (pnl or 0) > 0 else "bad" if (pnl or 0) < 0 else "") if pnl is not None else "open")], outcomes=not t.get("provisional"))
        if t.get("proposed"):
            banner("<b>Proposed — not recorded.</b> Automatic recording is paused until you approve the ticket policy, so nothing is staked. This is what the selector would record right now, at current prices.", "info")
        if t.get("provisional"):
            banner("<b>Provisional — not recorded.</b> Built from an earlier look at DraftKings' prices that is now older than the freshness limit. It uses no slot and no money. "
                   "It is recorded only if, once its prices are refreshed, it still qualifies.", "warn")
        st.caption(esc(t["rationale"]))
        if t.get("estimate_note"):
            banner(f"<b>Earlier estimate rule.</b> {esc(t['estimate_note'])}", "warn")
        leg_why(legs)
        leg_reviews(legs)
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


EGGY_PICK_TITLE = "Eggy Pick of the Day"
EGGY_NO_PICK = "No Eggy pick today"


def eggy_pick_card(primary: dict | None, *, recording_paused: bool = False) -> None:
    """Eggy's Parlay Pick of the Day: the engine's ONE primary ticket shown as a branded card. It is the same ticket the selector would record (or has recorded), not an additional bet,
    and it carries the prices, the estimated hit chance, the $10 paper stake and the return. Nothing here is guaranteed: the chance is an estimate with a range.
    With no qualifying ticket the card says so and gives the exact reason; it never invents a pick."""
    from dashboard import theme
    primary = primary or {}
    ticket = primary.get("ticket")
    img = f"<img alt='Eggy' src='{theme.eggy_pick_uri()}'/>"
    with st.container(border=True):
        if not ticket:
            reason = primary.get("reason") or "No combination of supported selections reaches +100 to +500 with value at the prices available."
            title = "Eggy's pick is unavailable right now" if primary.get("status") == "ERROR" else EGGY_NO_PICK
            st.markdown(f"<div class='eggy-pick-head eggy-pick-none'>{img}<div><div class='eyebrow'>Strongest learning ticket (not Eggy's pick)</div><div class='ttl'>{_h(title)}</div>"
                        f"<div class='sub'>{_h(reason)}</div></div></div>", unsafe_allow_html=True)
            if primary.get("data_limit"):
                st.warning(primary["data_limit"]["note"])
            st.caption("A day without a pick is a correct answer: Eggy does not loosen the rules to produce one, and no weaker slip is offered in its place.")
            return
        if ticket.get("recorded"):
            state_line = "Recorded as today's single primary ticket ($10 paper stake). Prices and probabilities are frozen."
        elif recording_paused or ticket.get("proposed"):
            state_line = "Proposed, not recorded: automatic recording is paused until the owner approves it, so nothing is staked."
        elif ticket.get("provisional"):
            state_line = "Provisional: built on an earlier look that has aged out; it is recorded only if it still qualifies on refreshed prices."
        else:
            state_line = "Selected for today; it is recorded only if every price is still fresh at that moment."
        st.markdown(f"<div class='eggy-pick-head'>{img}<div><div class='eyebrow'>Today's single primary ticket</div><div class='ttl'>{_h(EGGY_PICK_TITLE)}</div>"
                    f"<div class='sub'>{_h(state_line)}</div></div></div>", unsafe_allow_html=True)
        legs = ticket["legs"]
        decimal = ticket.get("combined_decimal") or 1.0
        anchor = ticket.get("anchor") or {}
        rng = (f"range {anchor['hit_low'] * 100:.0f}–{anchor['hit_high'] * 100:.0f}% · price implies {pct(1.0 / decimal)}"
               if anchor.get("hit_low") is not None and anchor.get("hit_high") is not None else f"price implies {pct(1.0 / decimal)}")
        _slip(legs, [("Combined price", american(ticket["combined_american"]), "calculated: product of the legs' DraftKings prices" if len(legs) > 1 else "DraftKings quote", "accent"),
                     ("Estimated hit chance", pct(ticket.get("hit_probability")), rng, ""),
                     ("Paper stake", money(ticket["stake"]), "model book, fixed", ""),
                     ("To return", money(ticket["potential_return"]), f"profit {money(ticket['potential_profit'])}" if ticket.get("potential_profit") is not None else None, "")],
              outcomes=bool(ticket.get("recorded")))
        why = primary.get("explanation") or primary.get("reason") or ticket.get("rationale") or ""
        if why:
            st.markdown(f"**Why this one.** {esc(why)}")
        leg_reviews(legs)
        st.caption("This is the same ticket as the primary parlay on the board, not an extra bet. Hit chances are estimates with a range, not promises; any parlay can lose.")


def recording_status_banner(rec: dict | None) -> None:
    """One concise status line while automatic paper recording is ACTIVE (a paused state keeps its own notice). Nothing here stakes anything: paper only, $10 a ticket, only when every production gate passes."""
    if not rec:
        return
    if rec.get("blocked_reason"):
        banner(f"<b>{esc(rec.get('label') or 'AUTOMATIC PAPER RECORDING: BLOCKED')}</b> · {esc(rec['blocked_reason'])}. Existing tickets still settle and prices still refresh.", "warn")
        return
    if not rec.get("active"):
        return
    bank = rec.get("bankroll") or {}
    cash, eq = bank.get("available_cash"), bank.get("equity")
    nxt_s, nxt_p = (rec.get("next_settlement") or {}).get("label"), (rec.get("next_postmortem") or {}).get("label")
    parts = [f"<b>{esc(rec.get('label') or 'AUTOMATIC PAPER RECORDING: ACTIVE')}</b>",
             f"paper bankroll {money(cash)}" + (f" (equity {money(eq)})" if eq is not None and eq != cash else ""),
             f"today: {rec.get('tickets_recorded_today', 0)} recorded · {rec.get('pending_today', 0)} pending · {rec.get('settled_today', 0)} settled",
             f"${rec.get('stake_usd', 10):.0f} paper stake, up to {rec.get('daily_slots', 1)} ticket(s) a day, only when every production gate passes"]
    if nxt_s and nxt_p:
        parts.append(f"next daily settlement {esc(nxt_s)}, post-mortem {esc(nxt_p)} ({esc(rec.get('settlement_note') or '')})")
    banner(" · ".join(parts), "good")


_EDGE_LABEL = {"POSITIVE_EDGE": "Positive edge", "NEUTRAL_UNCERTAIN_EDGE": "Neutral / uncertain", "LEARNING_SAMPLE_NEGATIVE_EDGE": "Learning sample: negative edge"}


def _edge_class_table(rows_: list | None) -> None:
    """The three edge classes side by side, never merged: N, W-L-P, hit rate, average odds, average model and implied probability, average estimated edge, P/L, ROI and calibration."""
    if not rows_:
        return
    st.dataframe([{"Edge class": _EDGE_LABEL.get(r["edge_class"], r["edge_class"]), "N": r["n"], "W-L-P": f"{r['wins']}-{r['losses']}-{r['pushes_voids']}", "Hit rate": _lx_pct(r.get("hit_rate")),
                   "Avg odds": (f"{r['average_american_odds']:+.0f}" if r.get("average_american_odds") is not None else "—"), "Avg model prob": _lx_pct(r.get("average_model_probability"), 1),
                   "Avg implied prob": _lx_pct(r.get("average_implied_probability"), 1), "Avg model edge (last observed price)": (f"{r['average_estimated_edge'] * 100:+.1f} pts" if r.get("average_estimated_edge") is not None else "—"),
                   "P/L": signed_money(r["profit_loss"]) if r["settled"] else "—", "ROI": _lx_pct(r.get("roi"), 1),
                   "Wins: expected vs actual": (f"{r['expected_wins']:.1f} vs {r['actual_wins']}" if r["settled"] else "—")} for r in rows_], hide_index=True, width="stretch")


def _age_table(rows_: list | None, key: str = "bucket", title: str = "Price age") -> None:
    """Results by the age of the last observed price when the bet was recorded. Selection quality (hit rate against the model's probability) and price value (ROI against the edge at that price) side by side."""
    if not rows_:
        return
    st.dataframe([{title: r[key].replace("_", " ").title() if key == "price_status" else r[key], "N": r["n"], "W-L-P": f"{r['wins']}-{r['losses']}-{r['pushes_voids']}", "Hit rate": _lx_pct(r.get("hit_rate")),
                   "Avg model prob": _lx_pct(r.get("average_model_probability"), 1), "Avg implied prob": _lx_pct(r.get("average_implied_probability"), 1),
                   "Wins: expected vs actual": (f"{r['expected_wins']:.1f} vs {r['actual_wins']}" if r["settled"] else "—"), "Avg model edge (last observed price)": (f"{r['average_estimated_edge'] * 100:+.1f} pts" if r.get("average_estimated_edge") is not None else "—"),
                   "P/L": signed_money(r["profit_loss"]) if r["settled"] else "—", "ROI": _lx_pct(r.get("roi"), 1)} for r in rows_], hide_index=True, width="stretch")


def _lx_pct(v, digits=0) -> str:
    return "—" if v is None else pct(v, digits)


# ------------------------------------------------------------------ the intelligence card ----
_ACTION_TONE = {"RECORD": "good", "LOWER_PRIORITY": "warn", "PASS": "bad"}
_LEVEL_TONE = {"LOW": "good", "MODERATE": "warn", "HIGH": "bad", "SEVERE": "bad"}
_CAL_TONE = {"HIGH": "good", "MEDIUM": "warn", "LOW": "bad", "INSUFFICIENT": "bad"}
_DECISION_TONE = {"GOOD_DECISION": "good", "QUESTIONABLE_DECISION": "warn", "BAD_DECISION": "bad", "INSUFFICIENT_EVIDENCE": "muted"}
_OUTCOME_TONE = {"WIN": "good", "LOSS": "bad", "VOID": "muted", "PENDING": "info", "UNRESOLVED": "warn"}
_VERDICT_TONE = {"HELD": "good", "PARTLY_HELD": "warn", "FAILED": "bad", "NOT_TESTABLE": "muted", "INFORMATION": "info"}
_ANSWER_LABELS = (("why_this_team_or_player", "WHY THIS TEAM / PLAYER"), ("why_tonight", "WHY TONIGHT"), ("why_this_market", "WHY THIS MARKET"), ("why_this_price", "WHY THIS PRICE"),
                  ("who_is_most_likely_to_win", "WHO IS MOST LIKELY TO WIN"), ("what_the_model_believes", "WHAT THE MODEL BELIEVES"), ("what_the_market_believes", "WHAT THE MARKET BELIEVES"),
                  ("why_they_disagree", "WHY THEY DISAGREE"), ("what_supports_it", "WHAT SUPPORTS THE BET"), ("what_argues_against_it", "WHAT ARGUES AGAINST IT"),
                  ("what_history_says", "WHAT HISTORICAL EVIDENCE SAYS"), ("what_data_is_missing", "WHAT DATA IS MISSING"), ("what_would_make_eggy_pass", "WHAT WOULD MAKE EGGY PASS"))


def _words(code) -> str:
    return str(code or "—").replace("_", " ")


def intelligence_card(card: dict | None, review: dict | None = None) -> None:
    """The review of one bet, readable in one glance: what Eggy believed before the game and why, what argued against it, and, once the game is over, what happened and whether the DECISION (not the
    result) was good. `card` is the review stored with the bet; `review` is the postmortem entry for it (facts of the game, the assessment). Every answer is read from the stored card, never recomputed."""
    if not card or card.get("kind") == "ERROR":
        st.caption("There is no review on file for this bet" + (f" ({esc((card or {}).get('reconstruction_error') or '')})" if (card or {}).get("reconstruction_error") else "") + ".")
        return
    from operational import eggy_intelligence as ei
    act = (card.get("action") or {}).get("decision")
    contra = (card.get("contradiction") or {}).get("level")
    cal = (card.get("calibration_confidence") or {}).get("level")
    chips = [chip(f"ACTION: {_words(act)}", _ACTION_TONE.get(act, "muted")), chip(f"CONTRADICTION: {_words(contra)}", _LEVEL_TONE.get(contra, "muted")), chip(f"CALIBRATION: {_words(cal)}", _CAL_TONE.get(cal, "muted")),
             chip(f"DECISION CONFIDENCE: {_words(card.get('decision_confidence'))}", {"HIGH": "good", "MEDIUM": "warn", "LOW": "bad"}.get(card.get("decision_confidence"), "muted")),
             chip(f"THESIS: {_words((card.get('thesis') or {}).get('type'))}", "info")]
    if card.get("reconstructed"):
        chips.append(chip("RECONSTRUCTED", "warn"))
    st.markdown(" ".join(chips), unsafe_allow_html=True)
    if card.get("reconstructed"):
        st.caption(esc(card.get("reconstruction_note") or ""))
    reasons = (card.get("action") or {}).get("reasons") or []
    if reasons:
        st.caption(("Why Eggy recorded it: " if act in ("RECORD", "LOWER_PRIORITY") else "Why the review passes: ") + esc("; ".join(reasons)))
    a = (review or {}).get("assessment") or {}
    if a and (a.get("outcome") in ("WIN", "LOSS", "VOID", "UNRESOLVED")):
        dq = (a.get("decision_quality") or {}).get("label")
        st.markdown(f"**OUTCOME** {chip(_words(a['outcome']), _OUTCOME_TONE.get(a['outcome'], 'muted'))} &nbsp; **DECISION RESULT** {chip(_words(dq), _DECISION_TONE.get(dq, 'muted'))}", unsafe_allow_html=True)
        st.caption("Two separate results: what the game did, and whether the choice was a good one given what was known before it. " + esc("; ".join((a.get("decision_quality") or {}).get("reasons") or [])))
        if not (review or {}).get("facts_complete"):
            st.caption("Provisional: the official box score is in, but power-play time and expected goals land with the MoneyPuck file (usually the next day) and may change this review.")
    ans = card.get("answers") or ei.standard_answers(card)
    for key, label in _ANSWER_LABELS:
        v = ans.get(key)
        if isinstance(v, list):
            if v:
                st.markdown(f"**{label}**")
                st.markdown("\n".join(f"- {esc(x)}" for x in v))
        elif v:
            st.markdown(f"**{label}** · {esc(v)}")
    with st.expander("The numbers, the doubts and the data behind this review"):
        pr, mk = card.get("probabilities") or {}, card.get("market") or {}
        st.dataframe([{"Measure": "Model probability", "Value": _lx_pct(pr.get("model"), 1)}, {"Measure": "Market probability (no margin)" if mk.get("no_vig") is not None else "Price-implied probability (margin included)",
                      "Value": _lx_pct(mk.get("no_vig") if mk.get("no_vig") is not None else mk.get("implied_with_margin"), 1)}, {"Measure": "Estimate used for expected value", "Value": _lx_pct(card.get("estimate_used"), 1)},
                      {"Measure": "MODEL EDGE AT LAST OBSERVED PRICE", "Value": f"{(mk.get('edge_vs_price') or 0) * 100:+.1f} pts (model alone {(mk.get('model_edge_vs_price') or 0) * 100:+.1f} pts)"},
                      {"Measure": "Price for a 3-point edge", "Value": (f"{mk['breakeven_price_for_3pt_edge']:+.0f}" if mk.get("breakeven_price_for_3pt_edge") is not None else "—")}], hide_index=True, width="stretch")
        flags = (card.get("contradiction") or {}).get("flags") or []
        if flags:
            st.markdown("**Contradiction flags** (one HIGH or two MODERATE make it HIGH; two HIGH or any SEVERE make it SEVERE)")
            st.dataframe([{"Flag": _words(f["name"]), "Level": f["level"], "What it says": f["text"]} for f in flags], hide_index=True, width="stretch")
        dc = card.get("data_completeness") or {}
        st.markdown(f"**Data completeness** {dc.get('k', 0)} of {dc.get('n', 0)} inputs present · missing: " + (", ".join(esc(x) for x in dc.get("missing") or []) or "none"))
        ev = (card.get("calibration_confidence") or {}).get("evidence")
        if ev:
            what = ev.get("label") or (f"the team model's probabilities in the {ev.get('band')} band ({ev.get('seasons')})" if ev.get("band") else "")
            pred, obs = ev.get("pred", ev.get("predicted")), ev.get("obs", ev.get("observed"))
            st.caption("Calibration evidence: " + esc(what) + f" ({int(ev.get('n') or 0):,} cases; the model averaged {_lx_pct(pred, 1)}, {_lx_pct(obs, 1)} happened)")
        ms = card.get("model_state") or {}
        if ms:
            st.caption("Model state: " + " · ".join(f"{_words(k)}: {esc(v)}" for k, v in ms.items() if v))
        st.caption(f"Review version {esc(card.get('version'))}. Eggy's reviews are rules on printed quantities, not a score; the team model has not been shown to beat the market.")
    if a and a.get("outcome") in ("WIN", "LOSS"):
        st.markdown("**WHAT HAPPENED**")
        facts = (review or {}).get("facts") or {}
        g = facts.get("game") or {}
        if g:
            line = f"{g.get('away')} {g.get('away_score')} at {g.get('home')} {g.get('home_score')}" + (f" ({g['period_type']})" if g.get("period_type") not in (None, "REG") else "")
            ts = g.get("team_stats")
            if ts:
                line += " · expected goals " + ", ".join(f"{t} {v['xg_for']:.2f}" for t, v in ts.items())
            st.markdown(esc(line))
        pf = facts.get("player")
        if pf:
            num = lambda v: "—" if v is None else f"{v:g}"
            st.markdown(esc(f"{num(pf.get('toi'))} min on ice" + (f", {pf['toi_pp']} on the power play" if pf.get("toi_pp") is not None else "") + f", {num(pf.get('shots'))} shots, {num(pf.get('goals'))} goals, {num(pf.get('points'))} points ({pf.get('source')})"))
        claims = a.get("claims") or []
        if claims:
            st.markdown("**WAS THE THESIS RIGHT?** (each factual claim checked against what happened)")
            st.dataframe([{"Claim": c["claim"], "Verdict": _words(c["verdict"]), "What happened": c.get("note") or ""} for c in claims], hide_index=True, width="stretch")
        ls = a.get("lesson") or {}
        st.markdown(f"**WHAT TO LEARN** · {esc(ls.get('text') or '')}")
        if a.get("classification"):
            st.markdown(f"**CLASSIFICATION** {chip(_words(a['classification']), 'muted')} · {esc(a.get('why') or '')} · action: **{_words(a.get('recommended_action'))}**", unsafe_allow_html=True)


def leg_reviews(legs: list[dict]) -> None:
    """One expander per leg that carries a review card (Eggy's own legs): clicking it shows why this leg, in the same words as every learning-book bet."""
    shown = [l for l in legs or [] if l.get("review")]
    if not shown:
        return
    st.markdown("**Open a leg to see why**")
    for l in shown:
        card = l["review"]
        with st.expander(f"{leg_dict_label(l)} · {_words((card.get('action') or {}).get('decision'))}"):
            st.caption("Eggy's pick is chosen by its own policy, which this does not change. This is the review the learning book gives the same leg, so any disagreement is visible here rather than hidden.")
            intelligence_card(card)


def parlay_panel(parlays: list[dict], cards: dict, reviews: dict | None = None, *, title: str = "Today's learning parlays") -> None:
    """The learning unit: unpriced learning PARLAYS built from the reviewed legs. Each shows its legs, the weakest leg, the dependence between them, the estimated combined probability only where it is supportable
    (else bounds, and why not), the reference product of the legs' prices (never a quote), the construction quality and, once settled, hit or miss and the leg(s) that killed it. Every leg opens into its review."""
    if not parlays:
        return
    st.markdown(f"**{title}**")
    st.dataframe([{"#": p["rank"], "Legs": " + ".join(l["label"] for l in p["legs"]), "Construction": p.get("verdict") or _words(p["construction_quality"]), "Weakest leg": f"{p['weakest_leg']['label']} ({p['weakest_leg']['estimate']:.0%})",
                   "Dependence": _words(p["dependence_level"]), "Combined probability": (_lx_pct(p["combined_probability"], 1) if p.get("combined_probability") is not None else f"not given (bounds {_lx_pct(p['dependence']['joint_bounds']['low'], 0)}–{_lx_pct(p['dependence']['joint_bounds']['high'], 0)})"),
                   "Price": "UNPRICED", "Result": _words(p["result_status"])} for p in parlays], hide_index=True, width="stretch")
    st.caption("Unpriced learning parlays: the system has no combined sportsbook price, so none is made up. The product of the legs' own prices is shown only as a reference, and no stake, return or ROI is claimed. "
               "Each ticket is settled by its legs and records whether the whole ticket would have hit.")
    for p in parlays:
        with st.expander(f"Parlay {p['rank']} · {len(p['legs'])} legs · {p.get('verdict') or _words(p['construction_quality'])} · {_words(p['result_status'])}"):
            st.markdown(f"**Weakest leg** · {esc(p['weakest_leg']['label'])} at {p['weakest_leg']['estimate']:.0%} (contradiction {_words(p['weakest_leg']['contradiction'])})")
            st.markdown(f"**Dependence** · {_words(p['dependence_level'])}: " + esc("; ".join(f"{x['a']} / {x['b']}: {_words(x['kind'])} ({x['strength']})" for x in (p['dependence'].get('pairs') or [])) or "none"))
            st.markdown("**Combined probability** · " + ("estimated " + _lx_pct(p["combined_probability"], 1) + f" ({esc(p['probability_basis'])})" if p.get("combined_probability") is not None else
                        f"NOT GIVEN: {esc(p['probability_basis'])}. Assumption-free bounds {_lx_pct(p['dependence']['joint_bounds']['low'], 1)} to {_lx_pct(p['dependence']['joint_bounds']['high'], 1)}; dependence is UNCERTAIN, not assumed away"))
            st.markdown("**Price** · " + esc(p.get("price_text") or "UNPRICED learning parlay: reference product " + f"{p.get('reference_decimal') or 0:.2f}x, a reference not a quote"))
            if p.get("quality_reasons"):
                st.caption("Why not GOOD: " + esc("; ".join(p["quality_reasons"])))
            if p.get("hit_text"):
                st.markdown(f"**Result** · {esc(p['hit_text'])}")
            rv = {x["key"]: x for x in p.get("leg_reviews") or []}
            for l in p["legs"]:
                x = rv.get(l["key"]) or {}
                with st.expander(f"{l['label']} · {_words((cards.get(l['key']) or {}).get('action', {}).get('decision'))}" + (f" · {_words(x.get('outcome'))}" if x.get("outcome") else "")):
                    intelligence_card(cards.get(l["key"]) or (x.get("card")), {"assessment": x.get("assessment"), "facts": x.get("facts"), "facts_complete": p.get("facts_complete")} if x else None)


def _bet_expanders(bets: list[dict], cards: dict, reviews: dict, *, label_key: str = "label") -> None:
    """One expander per bet, titled with its complete name ('Filip Chytil — SHOTS ON GOAL 2+ — -130'); clicking it opens the whole review."""
    for x in bets:
        bid = x.get("bet_id")
        rv = (reviews or {}).get(bid) or {}
        card = (cards or {}).get(bid) or rv.get("card")
        res = x.get("result_status") or x.get("result")
        head = f"{x.get(label_key) or x.get('bet')} · {_words((card or {}).get('action', {}).get('decision')) if card else 'no review'}" + (f" · {_words(res)}" if res else "")
        with st.expander(head):
            intelligence_card(card, rv)


def parlay_product_banner(doc: dict | None) -> None:
    """One line that says what the product is NOW, shown instead of the retired primary slip's recording banner: up to 10 unpriced learning parlays a day, Eggy's one flagship parlay or PASS, individual legs
    are candidates only. Read from the learning status (the same document the Paper Performance panel shows)."""
    if not doc or not doc.get("authorised"):
        return
    n, tgt, fl = doc.get("recorded_today", 0), doc.get("target_per_day", 10), doc.get("eggy_flagship")
    sf = doc.get("shortfall_today") or {}
    banner(f"<b>CURRENT PRODUCT: UNPRICED LEARNING PARLAYS</b> · {n} recorded today of up to {tgt} · {sf.get('parlay_legs_eligible', 0)} of {sf.get('legs_in_pool', 0)} candidate legs eligible · "
           f"Eggy's flagship today: {'1 parlay' if fl else 'PASS'} · individual legs are candidates, not bets, with no price floor · no combined price exists, so results are hit or miss only · "
           "the earlier single primary slip is retired (history only) · paper only", "good")


def intelligence_metrics_panel(m: dict | None) -> None:
    """Is the engine getting smarter? The measured record (operational/intelligence_metrics.py): hit rates against what the engine expected, with n and a z score; only a row with enough cases is a finding."""
    if not m or not m.get("tickets_recorded"):
        return

    def rows(cells: dict, label: str) -> list[dict]:
        return [{label: k, "Settled": c["n"], "Hit": c["hit"], "Hit rate": _lx_pct(c.get("rate"), 0), "Engine expected": _lx_pct(c.get("expected_rate"), 0), "z": ("—" if c.get("z") is None else f"{c['z']:+.2f}"),
                 "Enough cases?": "yes" if c.get("sufficient") else f"no (under {m.get('min_n_for_a_finding', 30)})"} for k, c in cells.items()]
    with st.expander("Is the engine getting smarter? The measured record (hit rates against what the engine expected)"):
        lg = m["legs"]
        a = lg["all"]
        st.markdown(f"**Legs, one count per leg per day:** {a['hit']} of {a['n']} hit ({_lx_pct(a.get('rate'), 0)}) against {a['expected_hit']} expected ({_lx_pct(a.get('expected_rate'), 0)}), z {a['z']}")
        st.markdown("**Calibration by probability band**")
        st.dataframe(rows(lg["calibration_by_probability_band"], "Engine said"), hide_index=True, width="stretch")
        st.markdown("**By market and threshold**")
        st.dataframe(rows(lg["by_market_and_threshold"], "Market"), hide_index=True, width="stretch")
        st.markdown("**Legs that carried a role / opportunity flag against legs that did not, and legs on one ticket against several**")
        st.dataframe(rows({**lg["flagged_vs_unflagged"], **lg["reused_vs_not"]}, "Group"), hide_index=True, width="stretch")
        tk = m["tickets"]
        st.markdown("**Tickets: GOOD against QUESTIONABLE, and by estimated ticket probability**")
        st.dataframe(rows({**tk["good_vs_questionable"], **tk["ticket_probability_bands"]}, "Group"), hide_index=True, width="stretch")
        st.caption(tk.get("probability_basis", "") + ". Why missed tickets missed: " + ", ".join(f"{k}: {v}" for k, v in (tk.get("why_missed_tickets_missed") or {}).items()))
        ml = (m.get("moneyline_model_vs_market") or {}).get("by_model_market_gap") or {}
        if ml:
            st.markdown("**Moneyline legs: did the side beat what the MARKET said, by model-market gap?**")
            st.dataframe(rows(ml, "Gap"), hide_index=True, width="stretch")
        fl = m.get("flagship") or {}
        st.markdown(f"**Eggy's flagship:** {fl.get('hit', 0)} hit of {fl.get('settled', 0)} settled" + ("" if fl.get("sufficient") else " (too few to judge)"))
        pm = m.get("postmortem") or {}
        st.markdown(f"**Postmortem:** {pm.get('lost_legs', 0)} lost legs; UNKNOWN {_lx_pct(pm.get('unknown_rate'), 0)}; unexplained or untested {_lx_pct(pm.get('unexplained_or_untested_rate'), 0)}. Causes: "
                    + ", ".join(f"{k} {v}" for k, v in (pm.get("cause_counts") or {}).items()))
        if pm.get("decision_quality_vs_outcome"):
            st.dataframe([{"Decision": _words(r["decision"]), "Outcome": _words(r["outcome"]), "Legs": r["n"]} for r in pm["decision_quality_vs_outcome"]], hide_index=True, width="stretch")
        eh = m.get("evidence_health") or {}
        if eh.get("state") in ("OK", "EXPIRING", "EXPIRED"):
            st.markdown(f"**Calibration evidence:** built {esc(str(eh.get('built_at_utc'))[:10])}, {eh.get('age_days')} days old, {eh.get('days_remaining')} days before the review layer stops (state {eh['state']}). "
                        f"Refresh: {esc(eh.get('controlled_refresh', ''))}")
        pts = m.get("points") or {}
        if pts:
            st.caption(f"Player points thresholds validated for live parlays: {pts.get('enabled_thresholds')} (recalibrated: {pts.get('recalibrated_thresholds')}); points legs settled so far: {pts.get('live_points_legs_settled', 0)}.")
        st.caption((m.get("priced_metrics") or {}).get("why", "") + ". Small samples say little: a row is a finding only when it says so.")


def learning_experiment_panel(doc: dict | None) -> None:
    """The temporary 14-day paper learning experiment (operational/learning_cohort.py): its own bankroll, its own record, visibly separate from Eggy's pick and the normal paper book. Paper only."""
    if not doc:
        return
    st.markdown("### 14-DAY POSTMORTEM LEARNING EXPERIMENT")
    phase, day = doc.get("phase"), doc.get("day") or 0
    if not doc.get("authorised"):
        banner("<b>14-DAY POSTMORTEM LEARNING EXPERIMENT: not recording yet.</b> It starts recording as soon as the owner's activation is applied. Paper only; Eggy's pick is separate.", "info")
        return
    if phase == "ENDED":
        banner("<b>14-DAY POSTMORTEM LEARNING EXPERIMENT: ended.</b> The window closed after November 2 (Eastern). Nothing new is recorded; open bets still settle and the final report is below once it is written. The normal paper-book policy continues.", "info")
    elif doc.get("window") == "PRODUCTION":
        banner(f"<b>LEARNING PARLAYS · production day {day}</b> · the 14-day experiment ({esc(doc.get('start_date'))} to {esc(doc.get('end_date'))}) is complete and kept in the record; "
               "the engine continues automatically: recording, settlement, the daily postmortem and the measured record · paper only", "good")
    else:
        banner(f"<b>14-DAY POSTMORTEM LEARNING EXPERIMENT · Day {day} of {doc.get('days', 14)}</b> · paper only, separate from Eggy and the normal paper book · "
               f"{esc(doc.get('start_date'))} to {esc(doc.get('end_date'))}, then the engine continues as production", "good")
    parlay_mode = doc.get("unit") == "PARLAY"
    if parlay_mode:
        sfp, fl0 = doc.get("shortfall_today") or {}, doc.get("eggy_flagship")
        pc = st.columns(4)
        pc[0].metric("Learning parlays today", f"{doc.get('recorded_today', 0)} of up to {doc.get('target_per_day', 10)}")
        pc[1].metric("Candidate legs eligible", f"{sfp.get('parlay_legs_eligible', 0)}", f"of {sfp.get('legs_in_pool', 0)} examined", delta_color="off")
        pc[2].metric("Eggy flagship today", "1 parlay" if fl0 else "PASS")
        pc[3].metric("Pricing", "Unpriced", "no combined price exists: hit or miss only", delta_color="off")
        st.caption("The product: up to 10 unpriced learning PARLAYS a day built from reviewed candidate legs (individual legs are candidates, not bets; no price floor on a leg), and Eggy's ONE official flagship parlay "
                   "(the strongest GOOD ticket) or PASS. A real combined price, if one ever exists, is judged at +100 or better at the parlay level; none is made up. Fewer than 10 is a correct answer. "
                   "The earlier single primary slip and the straight-bet experiment are retired and kept below as history.")
    bank, rec = doc.get("bankroll") or {}, doc.get("record") or {}
    def straight_tiles() -> None:
        """The earlier straight-bet experiment's bankroll tiles (retired 2026-10-22); in the parlay product they are history, shown only inside the history expander."""
        a = st.columns(4)
        a[0].metric("Today's target", doc.get("target_per_day", 10))
        a[1].metric("Recorded today", f"{doc.get('recorded_today', 0)}/{doc.get('target_per_day', 10)}")
        a[2].metric("Stake each", money(doc.get("stake_usd", 10)))
        a[3].metric("Odds requirement", str(doc.get("odds_requirement", "-150 or better")))
        b = st.columns(4)
        b[0].metric("Today's exposure", money(doc.get("exposure_today_usd", 0)))
        b[1].metric("Experiment P/L", signed_money(bank.get("betting_pl", 0)))
        b[2].metric("Experiment ROI", _lx_pct(bank.get("roi"), 1))
        b[3].metric("Hit rate", _lx_pct(rec.get("hit_rate")), f"{rec.get('wins', 0)}-{rec.get('losses', 0)}-{rec.get('voids', 0)} · {rec.get('open', 0)} open", delta_color="off")
        c = st.columns(4)
        c[0].metric("Current cash", money(bank.get("cash", 0)), f"equity {money(bank.get('equity', 0))}", delta_color="off")
        c[1].metric("Betting P/L", signed_money(bank.get("betting_pl", 0)))
        c[2].metric("Reset count", bank.get("reset_count", 0))
        c[3].metric("Total reset funding (not profit)", money(bank.get("reset_funding_added", 0)))
        st.caption("A separate experiment bankroll that started at \\$500.00. Whenever cash falls below the next \\$10 stake it is reset to \\$500.00; the reset is logged as funding, never as winnings or profit, and earlier losses are never erased; results come from settled bets only. "
                   "Each day aims for 10 real, eligible paper bets at -150 or better, priced at the last real DraftKings price we observed (its age is shown with every bet); if fewer qualify the day records fewer and says why. Eggy's pick of the day is separate and unchanged.")
    if not parlay_mode:
        straight_tiles()
    sf = doc.get("shortfall_today")
    if sf and doc.get("recorded_today", 0) < doc.get("target_per_day", 10):
        with st.expander((f"Today: {doc.get('recorded_today', 0)} parlays recorded of up to {doc.get('target_per_day', 10)}, why not 10" if parlay_mode else f"Today: {sf.get('recorded')}/{sf.get('target')} recorded, why not 10")):
            st.write(sf.get("why") or "")
            reasons = sf.get("reasons_candidates_were_not_recorded") or sf.get("not_eligible_reasons") or {}
            if reasons:
                st.dataframe([{"Reason a candidate was not recorded": k, "Candidates": v} for k, v in reasons.items()], hide_index=True, width="stretch")
            st.caption(f"{sf.get('recommended_not_yet_recorded', sf.get('eligible_not_yet_recorded', 0))} reviewed and kept, waiting for their slot or their own final window "
                       f"({sf.get('lower_priority_waiting_for_their_final_window', 0)} lower-priority) · {sf.get('legs_in_pool', 0)} candidate legs examined. Eggy passes on a candidate whose review says there is no thesis, "
                       "so a day can record fewer than 10 bets.")
            if sf.get("intelligence_unavailable"):
                st.warning("Eggy's review could not run this cycle, so nothing was recorded: " + esc(sf["intelligence_unavailable"]))
            if sf.get("review_errors"):
                st.warning(f"{sf['review_errors']} candidate review(s) FAILED (an engine error, not a judgment): " + esc("; ".join(f"{k} (x{v})" for k, v in (sf.get("review_error_examples") or {}).items())))
    parlay_panel(doc.get("parlays_today") or [], doc.get("cards") or {})
    pf = doc.get("portfolio_today") or {}
    if pf.get("tickets"):
        ml = pf.get("most_reused_leg") or {}
        st.caption(f"**Today's portfolio:** {pf['tickets']} tickets built from {pf['distinct_legs']} distinct legs ({pf['leg_slots']} leg slots) · expected tickets hit {pf['expected_tickets_hit']} "
                   f"(10th to 90th percentile {pf['tickets_hit_10th_to_90th_percentile'][0]} to {pf['tickets_hit_10th_to_90th_percentile'][1]}) · chance no ticket hits {_lx_pct(pf.get('chance_no_ticket_hits'), 0)}{(' (range ' + _lx_pct(pf['chance_no_ticket_hits_range'][0], 0) + ' to ' + _lx_pct(pf['chance_no_ticket_hits_range'][1], 0) + ' with the measured same-game dependence at its interval ends)') if pf.get('chance_no_ticket_hits_range') else ''} · "
                   f"shared legs widen the day's spread {pf.get('spread_inflation_from_shared_legs')}x (1.0 = fully diversified) · most reused: {esc(ml.get('label', ''))} on {ml.get('tickets')} tickets · "
                   f"{pf.get('tickets_with_a_role_flagged_leg', 0)} ticket(s) carry a role-uncertain leg and {pf.get('tickets_with_a_model_risk_leg', 0)} a model-uncertain leg · the same assessment chose between similarly strong tickets (shared legs, covariance, repeated games, teams and role/model risk).")
    fl = doc.get("eggy_flagship")
    if fl:
        st.markdown("**Eggy's flagship parlay, from the learning pool** (this is Eggy's official output; UNPRICED)")
        st.markdown(" + ".join(esc(l["label"]) for l in fl["legs"]) + f" · weakest leg {esc(fl['weakest_leg']['label'])} ({fl['weakest_leg']['estimate']:.0%}) · unpriced")
    if parlay_mode:
        pm_ = doc.get("postmortem") or {}
        psc = pm_.get("parlay_scorecard") or []
        if psc:
            st.markdown("**Parlay scorecard** (hit or miss only: unpriced parlays have no stake, return or ROI)")
            st.dataframe([{"Date": d["date"], "Recorded": f"{d['recorded']}/{d['target']}", "Hit": d["hit"], "Miss": d["miss"], "Open": d["open"],
                           "GOOD hit/settled": f"{d['good']['hit']}/{d['good']['n']}", "QUESTIONABLE hit/settled": f"{d['questionable']['hit']}/{d['questionable']['n']}",
                           "Eggy flagship": ((d["flagship"]["result"].title() + ": " + " + ".join(x.split(" \u2014 ")[0] for x in d["flagship"]["legs"])) if d.get("flagship") else "PASS / none")} for d in psc], hide_index=True, width="stretch")
            tot = pm_.get("parlay_totals") or {}
            st.caption(f"Cumulative: {tot.get('recorded', 0)} parlays recorded, {tot.get('hit', 0)} hit, {tot.get('miss', 0)} missed, {tot.get('open', 0)} open. Small samples say little; any parlay can lose.")
        pdays_ = [p for p in (pm_.get("parlays") or []) if p.get("et_date") == pm_.get("postmortem_for")]
        if pdays_:
            with st.expander(f"Parlay postmortem of {pm_.get('postmortem_for')}"):
                st.dataframe([{"#": p["rank"], "Legs": " + ".join(l["label"] for l in p["legs"]), "Priced": "no (unpriced)", "Hit / miss": p.get("hit_text"), "Weakest leg": f"{p['weakest_leg']['label']} ({p['weakest_leg']['estimate']:.0%})",
                               "Killed by": ", ".join(p.get("killed_by") or []) or "\u2014", "Construction": p.get("verdict")} for p in pdays_], hide_index=True, width="stretch")
                parlay_panel(pdays_, doc.get("cards") or {}, title="Parlays, with their leg reviews")
        intelligence_metrics_panel(pm_.get("intelligence_metrics"))
        les = pm_.get("lessons") or {}
        if les.get("by_date") or les.get("cautions"):
            with st.expander("What yesterday taught today's review (persisted postmortem lessons)"):
                if les.get("cautions"):
                    st.markdown("**Temporary cautions on named players** (a role, ice-time or power-play miss; they flag the player in today's review for 3 days, they never change a probability)")
                    st.dataframe([{"Player": c["player"], "Team": c.get("team") or "\u2014", "Finding": _words(c["classification"]), "Date": c["date"], "From": _words(c.get("source"))} for c in les["cautions"]], hide_index=True, width="stretch")
                for day_, v in sorted((les.get("by_date") or {}).items(), reverse=True):
                    st.markdown(f"**{day_}**: {v['tickets']} parlays, {v['hit']} hit / {v['miss']} missed" + (f" · repeated killer legs: {', '.join(v['repeated_killer_legs'])}" if v.get("repeated_killer_legs") else "")
                                + (f" · weakest legs that failed: {', '.join(v['weakest_legs_that_failed'])}" if v.get("weakest_legs_that_failed") else ""))
                if les.get("lesson_state"):
                    sm = les.get("lesson_state_summary") or {}
                    st.markdown(f"**Lesson states** · {sm.get('ACTIONABLE', 0)} actionable now · {sm.get('ACCUMULATING', 0)} accumulating evidence · {sm.get('REPORT_ONLY', 0)} report only. "
                                "A lesson changes tomorrow's review only when it is actionable (enough evidence); the rest are measured with their threshold written down, and a rule or policy change is only ever a proposal to the owner.")
                    st.dataframe([{"Lesson": x["name"], "State": _words(x["state"]), "Evidence": ", ".join(f"{k} {v}" for k, v in (x.get("evidence") or {}).items() if v is not None and not isinstance(v, (list, dict)))[:110],
                                   "Needed": (f"n >= {x['threshold']['n_required']}" + (f", |z| >= {x['threshold']['abs_z_required']}" if x['threshold'].get('abs_z_required') else "")) if x.get("threshold") else "\u2014",
                                   "Read by": (x.get("consumed_by") or "").split(" -> ")[-1][:60] or "\u2014"} for x in les["lesson_state"]], hide_index=True, width="stretch")
                st.caption(les.get("note") or "")
    hist = st.expander("Earlier straight-bet experiment (retired 2026-10-22; history only: 10 bets a day, $10 stake, -150 or better)") if parlay_mode else contextlib.nullcontext()
    pm = doc.get("postmortem") or {}
    with hist:
        if parlay_mode:
            straight_tiles()
        bets = doc.get("bets_today") or []
        if bets:
            st.markdown("**Today's experiment bets**")
            st.dataframe([{"Rank": x["selection_rank"], "Bet": x.get("label") or f"{x['participant_name']} — {x['market_family']}", "Action": _words(x.get("action")), "Thesis": _words(x.get("thesis_type")),
                           "Team": x.get("team"), "Hit chance": _lx_pct(x.get("conservative_probability")), "Model edge at last observed price": f"{(x.get('edge') or 0) * 100:+.1f} pts",
                           "Contradiction": _words(x.get("contradiction_level")), "Calibration": _words(x.get("calibration_level")), "Decision confidence": _words(x.get("decision_confidence")),
                           "Price age": (f"{str(x.get('price_status') or '—').replace('_', ' ').title()} ({x['price_age_at_recording_min']:.0f} min)" if x.get("price_age_at_recording_min") is not None else "—"),
                           "Edge class": _EDGE_LABEL.get(x.get("edge_class"), x.get("edge_class") or "—"), "Result": x["result_status"].title(), "P/L": signed_money(x["profit_loss"]) if x.get("profit_loss") is not None else "—",
                           "Eggy": "Eggy leg" if str(x.get("eggy_relation", "")).startswith("SAME_PLAYER") else ""} for x in bets], hide_index=True, width="stretch")
            st.caption("Open any bet below to see why Eggy took it: the team or player, tonight, the market, the price, who is likely to win, what the model and the market each believe, why they disagree, what supports it, "
                       "what argues against it, what history says, what data is missing and what would make Eggy pass.")
            _bet_expanders(bets, doc.get("cards") or {}, ((doc.get("postmortem") or {}).get("bet_reviews")) or {})
        pm = doc.get("postmortem") or {}
        sc = pm.get("scorecard") or []
        if sc:
            st.markdown("**14-day scorecard**")
            st.dataframe([{"Day": d["day"], "Date": d["date"], "Recorded": f"{d['recorded']}/{d['target']}", "W-L-P": f"{d['wins']}-{d['losses']}-{d['pushes_voids']}", "Hit rate": _lx_pct(d.get("hit_rate")),
                           "P/L": signed_money(d["profit_loss"]) if d["recorded"] else "—", "ROI": _lx_pct(d.get("roi"), 1), "Avg odds (dec)": f"{d['average_decimal_odds']:.2f}" if d.get("average_decimal_odds") and d["recorded"] else "—",
                           "Expected wins": f"{d['expected_wins']:.2f}" if d["recorded"] else "—", "Actual wins": str(d["actual_wins"]) if d["recorded"] else "—",
                           "Calibration": f"{d['calibration_delta']:+.2f}" if d.get("calibration_delta") is not None else "—"} for d in sc if not d.get("is_future") or d["recorded"]], hide_index=True, width="stretch")
            cu = pm.get("cumulative") or {}
            st.caption(f"Cumulative: {cu.get('placed', 0)} placed, {cu.get('settled', 0)} settled · {cu.get('wins', 0)}-{cu.get('losses', 0)}-{cu.get('voids', 0)} · hit rate {_lx_pct(cu.get('hit_rate'))} · P/L {signed_money(cu.get('profit_loss', 0))} · "
                       f"ROI {_lx_pct(cu.get('roi'), 1)} · expected wins {cu.get('expected_wins', 0)} vs actual {cu.get('actual_wins', 0)} · Brier {cu.get('brier') if cu.get('brier') is not None else '—'}. "
                       "A handful of bets says almost nothing about skill; same-game bets are not independent.")
        dr = pm.get("decision_review") or {}
        if dr.get("grid"):
            st.markdown("**Decision quality against outcome (so far)**")
            st.dataframe([{"Decision": _words(r["decision"]), "Outcome": _words(r["outcome"]), "Bets": r["n"]} for r in dr["grid"]], hide_index=True, width="stretch")
            st.caption(f"{dr.get('lucky_wins', 0)} lucky win(s) (a bad decision that won: no confidence gained) · {dr.get('earned_normal_variance', 0)} loss(es) that were EARNED normal variance (a good decision that lost) · "
                       f"{dr.get('process_failures', 0)} process failure(s) · {dr.get('not_variance', 0)} good decision(s) that lost for a reason other than variance. "
                       + (f"{dr['reconstructed']} bet(s) recorded before the review existed were reviewed as it would have run on their own day's data. " if dr.get("reconstructed") else "") + esc(dr.get("note") or ""))
        if (pm.get("cumulative_edge_classes") or []) and any(r["n"] for r in pm["cumulative_edge_classes"]):
            st.markdown("**By edge class (so far)**")
            _edge_class_table(pm["cumulative_edge_classes"])
            st.caption("Positive-edge, neutral and learning-sample negative-edge bets are reported separately so we can see whether the edge calculation predicts results.")
        if pm.get("cumulative_price_age"):
            st.markdown("**By price age at recording (so far)**")
            _age_table(pm["cumulative_price_age"])
            st.caption("Price age is recorded, never a reason to skip a bet: it is the time since the last real DraftKings observation of the price. Hit rate against the model's probability (selection) and ROI against the model edge at the last observed price (price value) are separate questions; we do not assume which way age matters.")
        dep = pm.get("dependence") or {}
        if dep.get("games_with_multiple_bets"):
            with st.expander("Same-game dependence (never assumed away)"):
                st.write(dep.get("reading") or "")
                st.dataframe([{"Game": g["game_id"], "Bets": g["bets"], "Expected hits": g["expected_hits"], "Actual hits": g["actual_hits"], "Pairs both hit": f"{g['pairs_both_hit']} (independent: {g['pairs_both_hit_if_independent']})",
                               "Players": ", ".join(g["players"])} for g in dep["games_with_multiple_bets"]], hide_index=True, width="stretch")
                st.caption(dep.get("not_captured", ""))
        if pm.get("day_metrics") and pm["day_metrics"].get("placed"):
            dm = pm["day_metrics"]
            with st.expander(f"Postmortem of {pm.get('postmortem_for')}"):
                pdays = [p for p in (pm.get("parlays") or []) if p.get("et_date") == pm.get("postmortem_for")]
                if pdays:
                    st.markdown("**PARLAY SUMMARY** (the leg-level reviews follow inside each parlay)")
                    st.dataframe([{"#": p["rank"], "Legs": " + ".join(l["label"] for l in p["legs"]), "Priced": "no (unpriced)", "Hit / miss": p.get("hit_text"), "Weakest leg": f"{p['weakest_leg']['label']} ({p['weakest_leg']['estimate']:.0%})",
                                   "Killed by": ", ".join(p.get("killed_by") or []) or "—", "Construction": p.get("verdict")} for p in pdays], hide_index=True, width="stretch")
                    parlay_panel(pdays, doc.get("cards") or {}, title="Parlays, with their leg reviews")
                st.write(f"{dm['placed']} placed, {dm['settled']} settled: {dm['wins']} won, {dm['losses']} lost, {dm['voids']} void/push, {dm['unresolved']} unresolved, {dm['open']} open · hit rate {_lx_pct(dm.get('hit_rate'))} · "
                         f"staked {money(dm['staked'])}, returned {money(dm['returned'])}, P/L {signed_money(dm['profit_loss'])} (ROI {_lx_pct(dm.get('roi'), 1)}) · expected wins {dm['expected_wins']} vs actual {dm['actual_wins']}")
                st.caption(pm.get("closing_price_movement") or "")
                db_ = pm.get("day_bets") or []
                if db_:
                    st.dataframe([{"Rank": x["rank"], "Bet": x["bet"], "Outcome": str(x["result"]).title(), "Decision": _words(x.get("decision")), "Why (classification)": _words(x.get("classification")) if x.get("classification") else "—",
                                   "Action": _words(x.get("action")) if x.get("action") else "—"} for x in db_], hide_index=True, width="stretch")
                    _bet_expanders(db_, doc.get("cards") or {}, pm.get("bet_reviews") or {}, label_key="bet")
                if pm.get("day_edge_classes"):
                    st.markdown("**By edge class**")
                    _edge_class_table(pm["day_edge_classes"])
                if pm.get("day_price_age"):
                    st.markdown("**By price age**")
                    _age_table(pm["day_price_age"])
                for key, label in (("market_type", "By market"), ("price_band", "By price band"), ("model_probability_band", "By model-probability band"), ("edge_band", "By edge band"),
                                   ("selection_rank", "By selection rank"), ("data_freshness", "By data freshness"), ("team", "By team"), ("player", "By player")):
                    rows_ = (pm.get("day_breakdowns") or {}).get(key)
                    if rows_:
                        st.markdown(f"**{label}**")
                        st.dataframe([{"Group": r["group"], "Placed": r["placed"], "W": r["wins"], "L": r["losses"], "P/L": signed_money(r["profit_loss"]), "ROI": _lx_pct(r.get("roi"), 1)} for r in rows_], hide_index=True, width="stretch")
        cb = pm.get("cumulative_breakdowns") or {}
        if any(cb.values()):
            with st.expander("Everything so far, by group"):
                for key, label in (("market_type", "By market"), ("price_band", "By price band"), ("model_probability_band", "By model-probability band"), ("edge_band", "By edge band"), ("selection_rank", "By selection rank")):
                    if cb.get(key):
                        st.markdown(f"**{label}**")
                        st.dataframe([{"Group": r["group"], "Placed": r["placed"], "Settled": r["settled"], "W": r["wins"], "L": r["losses"], "Hit rate": _lx_pct(r.get("hit_rate")), "P/L": signed_money(r["profit_loss"]),
                                       "ROI": _lx_pct(r.get("roi"), 1), "Expected wins": r["expected_wins"]} for r in cb[key]], hide_index=True, width="stretch")
                if pm.get("calibration"):
                    st.markdown("**Calibration: predicted against actual**")
                    st.dataframe([{"Band": r["band"], "Settled": r["n"], "Predicted": _lx_pct(r.get("predicted"), 1), "Actual": _lx_pct(r.get("actual"), 1), "Wins vs expected": r.get("delta_wins")} for r in pm["calibration"]],
                                 hide_index=True, width="stretch")
        if doc.get("bankroll", {}).get("reset_count"):
            with st.expander(f"Bankroll resets ({bank.get('reset_count')}) · funding, not profit"):
                st.dataframe([{"When (ET)": r["at_et"], "Day": r["experiment_day"], "Before": money(r["balance_before"]), "Added": money(r["amount"]), "After": money(r["balance_after"]), "Reason": r["reason"]}
                              for r in (doc.get("resets") or [])], hide_index=True, width="stretch")
    fr = doc.get("final_report")
    if fr:
        with st.expander("Final experiment report (recommendations only)", expanded=True):
            st.caption(fr.get("statement", ""))
            if (fr.get("answers") or {}).get("edge_class_results"):
                st.markdown("**Results by edge class**")
                _edge_class_table(fr["answers"]["edge_class_results"])
                st.caption(str(fr["answers"].get("did_positive_edge_outperform", "")))
            if (fr.get("answers") or {}).get("price_age_results"):
                st.markdown("**Results by price age**")
                _age_table(fr["answers"]["price_age_results"])
                st.caption(str(fr["answers"].get("does_price_age_matter", "")))
            for k, v in (fr.get("answers") or {}).items():
                if k in ("market_verdicts", "edge_class_results", "did_positive_edge_outperform", "price_age_results", "does_price_age_matter"):
                    continue
                st.markdown(f"**{k.split('_', 1)[0]}. {k.split('_', 1)[1].replace('_', ' ').capitalize()}**")
                st.json(v, expanded=False)


def _stale_on_open(quoted_at, start_utc) -> tuple[bool | None, float | None]:
    """Judged when the page is opened, from the provider's own quote time and the engine's price limit (the document's flags are from when it was built, up to ten minutes ago or more)."""
    from operational import quote_freshness as qf
    now = _utcnow()
    start = qf.parse_utc(start_utc)
    if start is None or not quoted_at:
        return None, None
    hours = (start - now).total_seconds() / 3600.0
    r = qf.assess(quoted_at, None, now, qf.price_limit_min(hours), require_retrieval=False)
    return (not r["fresh"]), r.get("quote_age_min")


def _eggy_leg_line(l: dict) -> str:
    price = l.get("price")
    if price:
        stale, age = _stale_on_open(price.get("quote_updated_utc"), l.get("start_utc"))
        if stale is None:
            stale = price.get("fresh") is False
        when = et_time(price.get("quote_updated_utc"), True) if price.get("quote_updated_utc") else "no quote time"
        price_txt = f"(quoted {when}{'; STALE' if stale else ''})"
    else:
        price_txt = f"no price yet: {str(l.get('price_outlook') or 'UNKNOWN').replace('_', ' ').lower()}"
    bits = [f"**{esc(leg_dict_label(l))}**", price_txt]
    if l.get("model_probability") is not None:
        bits.append(f"model {pct(l['model_probability'])}")
    if l.get("recent_record"):
        bits.append(f"recent {l['recent_record']} games")
    if l.get("estimate") is not None:
        bits.append(f"estimate {pct(l['estimate'])}")
    if l.get("implied_probability"):
        bits.append(f"price implies {pct(l['implied_probability'])}")
    tags = []
    if not (l.get("gates") or {}).get("recording_policy_uses_market", True):
        tags.append("not used by the recording policy")
    if l.get("model_validation") not in (None, "BEATS_BASELINES", "STRENGTH_MODEL_AND_MARKET"):
        tags.append(f"model: {str(l['model_validation']).lower().replace('_', ' ')}")
    return "- " + " · ".join(bits) + (f" · _{'; '.join(tags)}_" if tags else "")


def _stage_b_note(primary: dict | None) -> None:
    """What the production recording policy (stage B, cross-game tickets at real prices) found, shown in its own words and labelled as such."""
    primary = primary or {}
    if primary.get("data_limit"):
        st.warning(primary["data_limit"]["note"])
    if primary.get("reason"):
        st.caption("Production recording policy (cross-game tickets only, at real prices): " + esc(primary["reason"]))


def _eggy_official_card(eggy: dict) -> None:
    """Eggy's official daily output: ONE flagship parlay (the learning pool's strongest GOOD ticket) or PASS. An unpriced flagship is labelled UNPRICED; no combined price, stake, return or ROI is shown or claimed."""
    from dashboard import theme
    img = f"<img alt='Eggy' src='{theme.eggy_pick_uri()}'/>"
    today = et.eastern_today(_utcnow())
    out, fl = eggy.get("daily_output"), eggy.get("flagship")
    if eggy.get("date_et") and eggy["date_et"] != today:
        st.caption(f"The Eggy output on file is for {esc(eggy['date_et'])}, not today ({today}); it is not shown.")
        return
    with st.container(border=True):
        if out != "OFFICIAL_EGGY_FLAGSHIP_PARLAY" or not fl:
            title, sub = (("Eggy's flagship is unavailable right now", eggy.get("error") or "")) if out == "UNAVAILABLE" else (
                "No strong learning ticket", "The learning parlay engine has no GOOD qualifying ticket today. It does not lower the bar to produce one.")
            st.markdown(f"<div class='eggy-pick-head eggy-pick-none'>{img}<div><div class='eyebrow'>Strongest learning ticket (not Eggy's pick)</div><div class='ttl'>{_h(title)}</div><div class='sub'>{_h(sub)}</div></div></div>", unsafe_allow_html=True)
            return
        st.markdown(f"<div class='eggy-pick-head'>{img}<div><div class='eyebrow'>Strongest learning ticket today (from the learning parlay engine, not Eggy's pick) · UNPRICED LEARNING PARLAY</div><div class='ttl'>{_h(EGGY_PICK_TITLE)}</div>"
                    f"<div class='sub'>Unpriced: no combined price exists, so no stake, return or ROI is claimed. It is judged by its legs.</div></div></div>", unsafe_allow_html=True)
        for l in fl["legs"]:
            st.markdown("- " + esc(l["label"]) + f" · estimate {l['estimate']:.0%}")
        wl = fl["weakest_leg"]
        bits = [f"Weakest leg: **{esc(wl['label'])}** ({wl['estimate']:.0%})", f"Construction: **{esc(fl['construction_quality'])}**", f"Dependence: **{esc(str(fl['dependence_level']).replace('_', ' '))}**"]
        bits.append(f"Combined probability: **{fl['combined_probability']:.0%}**" if fl.get("combined_probability") is not None else "Combined probability: **not given**")
        st.markdown(" · ".join(bits))
        if fl.get("reference_decimal"):
            st.caption(f"Reference only (the product of the legs' own quoted prices, {fl['reference_decimal']:.2f}x): not a sportsbook price.")
        st.caption("Hit chances are estimates, not promises; any parlay can lose.")


def _eggy_top_three(d: dict) -> None:
    """The three most researched sides of the day, with the verdict on each (shown on a PASS day too)."""
    top = d.get("top_three") or []
    if not top:
        return
    with st.expander("The three sides Eggy researched most" + (" (the bet is the first)" if d["decision"] == "PICK" else "")):
        for t in top:
            ln = f" {t['line']:+.1f}" if t.get("line") is not None else ""
            st.markdown(f"**{esc(t['side'])} {esc(t['market'].replace('_', ' '))}{esc(ln)} {t['price']:+.0f}** · market {t['market_probability_market']:.1%} · hockey model {t['model_probability']:.1%} · gap {t['gap'] * 100:+.1f} pts · {esc(t['trust_state'].replace('_', ' ').lower())} · evidence: {esc((t['structure'] or 'NONE').replace('_', ' ').lower())} · families: {esc(', '.join((t.get('edge_thesis') or {}).get('families', [])).lower())} ({'independent' if (t.get('edge_thesis') or {}).get('independent') else 'not independent'}) · {'QUALIFIES' if t['qualifies'] else 'does not qualify'}")
            st.caption("Edge thesis: " + esc((t.get('edge_thesis') or {}).get('thesis') or "none"))
            st.caption("Hockey: " + esc("; ".join(t["hockey_case"]) or "nothing in its favour") + " | Market / price: " + esc("; ".join(t["market_price_case"]) or "nothing in its favour") + " | Against: " + esc("; ".join(t["case_against"])))
            if t.get("not_qualifying_because"):
                st.caption("Why not: " + esc("; ".join(t["not_qualifying_because"][:3])))


def eggy_side_pick_card(doc: dict | None) -> None:
    """EGGY PICK OF THE DAY: one NHL side (moneyline or puck line) or PASS, with the case for and against, the model-versus-market state, the price and its source, and the record. Separate from the learning
    parlays, which are shown elsewhere. Nothing is invented: what the engine cannot see (public tickets, handle, reverse line movement, closing prices) is shown as NOT AVAILABLE."""
    from dashboard import theme
    doc = doc or {}
    today = et.eastern_today(_utcnow())
    img = f"<img alt='Eggy' src='{theme.eggy_pick_uri()}'/>"
    if not doc.get("policy"):
        st.caption("Eggy's side pick is not available in this data snapshot.")
        return
    if doc.get("date_et") and doc["date_et"] != today:
        st.caption(f"The Eggy side pick on file is for {esc(doc['date_et'])}, not today ({today}); it is not shown.")
        return
    d = doc.get("today")
    with st.container(border=True):
        if not d:
            st.markdown(f"<div class='eggy-pick-head eggy-pick-none'>{img}<div><div class='eyebrow'>EGGY'S PICK OF THE DAY</div><div class='ttl'>Eggy has not decided yet</div><div class='sub'>It makes ONE bet on every NHL slate day, after the pregame odds pull is in and before the first game.</div></div></div>", unsafe_allow_html=True)
        elif d["decision"] == "PASS":
            st.markdown(f"<div class='eggy-pick-head eggy-pick-none'>{img}<div><div class='eyebrow'>EGGY'S PICK OF THE DAY</div><div class='ttl'>NO BET TODAY</div><div class='sub'>{_h(d.get('pass_reason') or '')}</div></div></div>", unsafe_allow_html=True)
            if d.get("what_would_change_it"):
                st.caption("What would change it: " + esc(d["what_would_change_it"]))
            _eggy_top_three(d)
        else:
            p = d["pick"]
            card = p.get("card") or {}
            st.markdown(f"<div class='eggy-pick-head'>{img}<div><div class='eyebrow'>EGGY'S PICK OF THE DAY · {_h(p['matchup'])}</div><div class='ttl'>{_h(card.get('bet') or p['side'])}</div></div></div>", unsafe_allow_html=True)
            basis = "an edge thesis" if p.get("pick_basis") == "EDGE_THESIS" else "the best available bet (no independent edge family cleared the bar)"
            st.markdown(f"**CONFIDENCE:** {esc(p.get('confidence', 'LOW'))} (an evidence category, not a percentage) · chosen on {basis}")
            st.markdown("**WHY THIS IS TODAY'S #1 BET:** " + esc(card.get("why_number_one") or ""))
            th = p.get("edge_thesis") or {}
            st.caption("Edge thesis: " + esc(th.get("thesis") or "none") + " | independent evidence families: " + esc(", ".join(f.replace("_", " ").lower() for f in th.get("families", [])) or "none") + ". " + esc(th.get("expected_value_claim") or ""))
            st.markdown("**HOCKEY CASE:** " + esc("; ".join(card.get("hockey_case") or []) or f"model win probability {p['model_win_probability']:.1%}"))
            st.markdown("**MARKET CASE:** " + esc("; ".join(card.get("market_case") or []) or "no meaningful movement since the day's first capture; public ticket and handle data are NOT AVAILABLE"))
            wm = card.get("why_this_market") or {}
            st.markdown("**WHY THIS MARKET:** " + esc(wm.get("why") or "") + (f" (other market: {esc((wm.get('other') or {}).get('bet', ''))})" if wm.get("other") else ""))
            st.markdown("**CASE AGAINST:** " + esc("; ".join(card.get("case_against") or [])))
            st.markdown(f"**MODEL VS MARKET:** model {p['model_probability']:.1%} · market no-vig {p['market_probability_market']:.1%} · gap {p['gap'] * 100:+.1f} pts · trust state: {esc(p['trust_state'].replace('_', ' ').lower())} (weight {p.get('model_weight', 0):.2f}) · evidence-weighted probability {p['supported_probability']:.1%}")
            st.markdown(f"**PRICE:** {p['price']:+.0f} ({esc(p['price_freshness']['status'])}, DraftKings, captured {esc(p['capture_at_utc'])}); break-even {p['implied_with_margin']:.1%}; evidence-weighted edge {p['edge'] * 100:+.1f} pts"
                        + ("" if p["edge"] > 0 else " (no positive expected value is claimed: the bookmaker's margin is the cost of the bet)"))
            _eggy_top_three(d)
        rec = doc.get("record") or {}
        st.caption(f"Eggy's own record: {rec.get('picks', 0)} pick(s), {rec.get('wins', 0)}-{rec.get('losses', 0)} settled, {rec.get('passes', 0)} pass day(s). Hit or miss only; kept apart from the learning parlays.")
        with st.expander("What Eggy can and cannot see today, and which signals are earning trust"):
            av = doc.get("availability") or {}
            st.markdown("**Available:** " + esc("; ".join(av.get("AVAILABLE", []))))
            st.markdown("**NOT AVAILABLE:** " + esc("; ".join(av.get("NOT AVAILABLE", []))))
            sig = doc.get("signals") or {}
            if sig:
                st.dataframe([{"Signal": v["describes"], "Games": v["games"], "Wins": v["wins"], "Expected at the market price": v["expected_wins_at_the_market_price"], "z": v["z"], "State": _words(v["state"]),
                               "Needed to drive a decision": v["games_needed_to_drive_a_decision"]} for v in sig.values()], hide_index=True, width="stretch")
            st.caption("A signal changes a decision only after 100 independent games and |z| >= 2. None is assumed to work.")


def eggy_candidate_card(eggy: dict | None, primary: dict | None, *, recording_paused: bool = False) -> None:
    """Eggy's Parlay Pick of the Day with its two stages. An official pick (stage B, the production recording policy) is shown exactly as before. Without one, the card shows the BEST EGGY CANDIDATE
    the whole-slate search found and says which certification gates stand in its way, or a TRUE PASS with what was examined. It never invents a joint probability or a combined price."""
    from dashboard import theme
    eggy = eggy or {}
    out = eggy.get("daily_output")
    if eggy.get("official_policy"):                        # Eggy's official output is its ONE side pick (moneyline or puck line) or PASS; the learning engine's strongest ticket is shown beneath it, labelled as such
        try:
            from dashboard import product_source as _ps
            eggy_side_pick_card(_ps.eggy_pick())
        except Exception:  # noqa: BLE001 - the side pick is read from its own ledger; a failure must not hide the learning ticket
            st.caption("Eggy's side pick could not be read right now.")
        _eggy_official_card(eggy)
        return
    if (primary or {}).get("ticket"):                      # an official ticket (proposed or recorded) is always shown by the original card, whatever the candidate document says
        eggy_pick_card(primary, recording_paused=recording_paused)
        return
    today = et.eastern_today(_utcnow())
    if eggy and eggy.get("date_et") and eggy["date_et"] != today:
        st.caption(f"The candidate search on file is for {esc(eggy['date_et'])}, not today ({today}); it is not shown. The official card follows.")
        eggy_pick_card(primary, recording_paused=recording_paused)
        return
    if out in (None, "UNAVAILABLE", "OFFICIAL_EGGY_PICK") or eggy.get("error"):
        if eggy.get("error"):
            st.caption("Eggy's whole-slate candidate search could not be completed this cycle: " + esc(eggy["error"]))
        eggy_pick_card(primary, recording_paused=recording_paused)
        return
    img = f"<img alt='Eggy' src='{theme.eggy_pick_uri()}'/>"
    best = eggy.get("best_candidate")
    with st.container(border=True):
        if out == "TRUE_PASS" or not best:
            ex = eggy.get("examined") or {}
            sub = ("No game is left to examine (every game has started or finished). Eggy does not manufacture a pick." if not ex.get("games") else
                   f"After examining {ex.get('games', 0)} games and {ex.get('candidates', 0)} thesis candidates, none reaches the support bar ({ex.get('support_floor', 0.6):.0%}) with every leg's model validated. "
                   "Eggy does not manufacture a pick.")
            st.markdown(f"<div class='eggy-pick-head eggy-pick-none'>{img}<div><div class='eyebrow'>{_h(EGGY_PICK_TITLE)}</div><div class='ttl'>TRUE PASS</div><div class='sub'>{_h(sub)}</div></div></div>", unsafe_allow_html=True)
            _stage_b_note(primary)
            return
        sub = f"Best Eggy candidate: {best['matchup']} — {best['thesis']['title'].title()}. It is shown because the thesis is strong, not because it is certified."
        st.markdown(f"<div class='eggy-pick-head eggy-pick-none'>{img}<div><div class='eyebrow'>{_h(EGGY_PICK_TITLE)}</div><div class='ttl'>NO CERTIFIED EGGY PICK TODAY</div>"
                    f"<div class='sub'>{_h(sub)}</div></div></div>", unsafe_allow_html=True)
        _stage_b_note(primary)
        st.caption(f"Candidate search built {et_time(eggy.get('generated_at_utc'), True)}; each price is judged against its own quote time when you open the page. "
                   "Nothing here is recorded or staked: a candidate is a thesis to look at, not a qualified pick, and a day without a pick is a correct answer.")
        st.markdown(f"**Thesis.** {esc(best['thesis']['sentence'])}")
        if best["thesis"].get("evidence"):
            st.caption("Evidence: " + " · ".join(esc(e) for e in best["thesis"]["evidence"]))
        st.markdown(f"**{esc(best['label'])}**")
        for l in best["legs"]:
            st.markdown(_eggy_leg_line(l))
        leg_reviews(best["legs"])
        dep = best.get("dependence") or {}
        bounds = dep.get("joint_bounds")
        bits = ["Joint probability: **not certified**"]
        if bounds:
            bits.append(f"assumption-free bounds {pct(bounds['low'])} to {pct(bounds['high'])} (true for any dependence; not an estimate)")
        bits.append("Combined price: **not available** (the feed has no same-game-parlay price and none is made up)")
        bits.append(f"support score {best['support_score']:.2f} (ranks candidates; not a probability)")
        st.markdown(" · ".join(bits))
        with st.expander("Why it is not certified, how the legs depend on each other, and what would certify it"):
            st.markdown("**Gates that stand in the way**")
            for b in best.get("blockers") or []:
                st.markdown(f"- {esc(b)}")
            st.markdown("**How the legs depend on each other**")
            for pr in dep.get("pairs") or []:
                st.markdown(f"- {esc(pr['a'])} + {esc(pr['b'])}: {esc(pr['kind'].replace('_', ' ').lower())}, {esc(pr['direction'].lower())} ({esc(pr['strength'])}) — {esc(pr['note'])}")
            if best.get("other_compatible_legs"):
                st.markdown("**Other compatible legs considered:** " + "; ".join(esc(x) for x in best["other_compatible_legs"]))
            st.markdown("**What would certify it**")
            for t in best.get("to_certify") or []:
                st.markdown(f"- {esc(t)}")
            st.caption("Prices are DraftKings US-feed quotes with the provider's own times; Ontario offerings and prices are unverified. Nothing here is recorded or staked.")
        others = eggy.get("other_candidates") or []
        if others:
            with st.expander(f"Other candidates examined ({len(others)})"):
                for c in others:
                    st.markdown(f"**{esc(c['matchup'])} — {esc(c['thesis']['title'].title())}** · support {c['support_score']:.2f} · {c['priced_legs']} of {c['total_legs']} legs priced" + ("" if c.get("game_funded") else " · game outside today's credit plan"))
                    st.caption(" + ".join(esc(leg_dict_label(l)) for l in c["legs"]))


def eggy_availability(eggy: dict | None) -> None:
    """Per game and market: is there a price, and if not, exactly why (not yet fetched, outside the credit plan, not posted, fetch failed, not usable) with the market's contract status."""
    rows = (eggy or {}).get("availability") or {}
    if not rows:
        return
    for gid, g in rows.items():
        st.markdown(f"**{esc(g['matchup'])}** · {esc(g.get('start_et') or '')}")
        for m in g["markets"]:
            st.markdown(f"- {esc(m['label'])}: **{esc(str(m['state']).replace('_', ' ').lower())}** — {esc(m.get('why') or '')} · contract {esc(str(m.get('contract', '')).lower())} · recording policy: {esc(m.get('recording_policy', ''))}")
        if g.get("withdrawn_for_identity"):
            st.caption("Withdrawn for identity (not on the team's current official roster): " + ", ".join(esc(str(n)) for n in g["withdrawn_for_identity"]))


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
        st.caption("Value = estimated return per dollar; After haircut = the same with every leg's chance lowered 3 points (a fixed policy margin, not a measured uncertainty and not proof of an edge). Individual-player model error in testing was about 8 points, "
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
            st.markdown(f"**{esc(leg_dict_label(l))}**")
            st.caption(esc(" · ".join(c["lines"])))
            (st.warning if c["low_sample"] else st.caption)(esc(c["uncertainty"]))


# ------------------------------------------------------------------ best options ----

def option_card(opt: dict, *, key: str, show_people: bool = True) -> None:
    """One option, read-only: legs, prices, quote ages, the estimate and its evidence. There is no add/submit control anywhere in the product: nothing a visitor does is recorded."""
    with st.container(border=True):
        kind = "Single" if opt["kind"] == "SINGLE" else f"{len(opt['legs'])}-leg cross-game parlay"
        _card_head(" + ".join(leg_dict_label(l) for l in opt["legs"]), chip(kind, "info"))
        quoted = opt["price_basis"] == "SPORTSBOOK_QUOTE"
        decimal = opt.get("combined_decimal") or 1.0
        ev = opt["ev_after_haircut"] * 100
        stale_any = _slip(opt["legs"], [("Quoted price" if quoted else "Estimated price", american(opt["combined_american"]), "DraftKings quote" if quoted else "product of leg prices", "accent"),
                            ("Stake", money(opt["stake"]), None, ""),
                            ("To return", money(opt["potential_return"]), f"profit {money(opt['potential_profit'])}", ""),
                            ("Estimated chance" if opt.get("anchor") else "Model chance", pct(opt["hit_probability"]),
                             (f"range {opt['anchor']['hit_low'] * 100:.0f}–{opt['anchor']['hit_high'] * 100:.0f}% · price implies {pct(1.0 / decimal)}" if opt.get("anchor") else
                              (f"{chance_range_text(opt['legs']) or ''} · price implies {pct(1.0 / decimal)}").strip(" ·")), ""),
                            ("Value after haircut", f"{ev:+.0f}%", "3-pt policy margin", "good" if ev > 0 else "bad")], outcomes=False)
        prov_any = (not stale_any) and any(l.get("provisional") for l in opt["legs"])
        if stale_any:
            banner("<b>Stale price.</b> At least one price on this card is past its freshness limit now. It is shown for reference only: it is no longer a recommendation.", "bad")
        elif prov_any:
            banner("<b>Provisional.</b> An earlier look at DraftKings' price today, now older than the freshness limit. It can be browsed, but it is not a current recommendation until its "
                   "price is refreshed (the engine looks again every 10 minutes).", "warn")
        elif opt.get("early"):
            banner("<b>Morning price.</b> From an earlier look today and still inside the freshness limit; the engine re-checks it at the pregame look.", "info")
        st.caption(esc(opt["rationale"]))
        leg_why(opt["legs"])
        if show_people and opt.get("best_for"):
            names = ", ".join(p["name"] for p in opt["best_for"])
            st.caption(f"Best option for: {names}")
        st.caption(f"{opt['price_label']}. Prices are DraftKings US-feed quotes; they have not been verified against DraftKings Ontario.")
        ontario_check(opt, key=key)


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
            lines.append(f"{leg_dict_label(l)}: Ontario {american(c['ontario_price'])} seen {et_time(c['observed_at_utc'], True)} ({c['where_seen']}) vs US feed {american(l['american_price'])}")
    if lines:
        st.caption("Ontario spot check — " + " · ".join(esc(x) for x in lines))
    with st.expander("Check this on DraftKings Ontario"):
        st.caption("Prices here come from the US feed. Open the same selection in DraftKings Ontario and record the price you see; it is stored as evidence "
                   "with the time and where you saw it, and does not change any ticket.")
        sess = st.session_state.setdefault("_verifications", {})
        for i, l in enumerate(opt["legs"]):
            with st.form(f"{key}_ont_{i}", clear_on_submit=False):
                st.write(leg_dict_label(l))
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


def book_split(origins: dict | None, *, expanded: bool = False, as_table_only: bool = False) -> None:
    """The model account read apart: the parlay experiment's own result, the earlier single bets (moneyline and props, no longer recorded), and the whole account they add up to.
    Nothing is reset; the account is the combined one."""
    o = origins or {}
    p, s_, rec = o.get("PARLAY_TICKETS"), o.get("SINGLE_BETS"), o.get("RECONCILIATION")
    if not p or not s_ or not rec:
        return

    def row(label, b):
        opened = b.get("open", b.get("pending", 0) + b.get("unresolved", 0))
        return {"Part of the account": label, "Bets": b.get("bets", b.get("tickets", 0)), "Won": b["wins"], "Lost": b["losses"], "Open": opened, "Settled P&L": signed_money(b["settled_pnl"]),
                "ROI": pct(b["roi"], 1) if b.get("roi") is not None else "—"}
    rows = [row("Parlay experiment (tickets)", p), row("Single bets: moneyline and props (earlier; no longer recorded)", s_), row("Whole account", o.get("ALL") or {})]

    def render():
        st.dataframe(rows, hide_index=True, width="stretch")
        st.caption(f"{rec['sentence']}".replace("$", "\\$") + ". " + ("The parts add up to the account." if rec.get("all_agree") else "THE PARTS DO NOT ADD UP: this is a bookkeeping fault, not a result."))
        st.caption("Single bets were taken out of this experiment on 2026-10-10: the book is now parlay tickets only. The earlier single bet stays on the record, nothing was reset or rewritten.")
    if as_table_only:
        render()
    else:
        with st.expander("How the account splits: parlay tickets and single bets", expanded=expanded):
            render()


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
                  {"Check": "Direct (one-click) filing path ready", "Result": "yes" if stat["direct_ready"] else "no — the click would open a pre-filled GitHub issue instead"}],
                 hide_index=True, width="stretch")
    if stat["login_configured"] and not stat["signed_in"]:
        if st.button("Sign in to enable direct filing", key="order_signin"):
            st.login()
    elif stat["signed_in"]:
        if st.button("Sign out", key="order_signout"):
            st.logout()
    elif not stat["login_configured"]:
        st.info("This panel is about the owner-only evidence records (goalie confirmations, Ontario price checks): they change what everyone sees, so they need a supported sign-in "
                "(`st.login()`, OIDC; steps in docs/MANUAL_ORDERS.md) because Streamlit says the platform's own viewer header is not a supported identity.")
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
