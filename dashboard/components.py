"""
Shared UI components for the NHL Model Research + Intelligence dashboard
-- the provenance panel, model-status header, and data-mode badge that
appear on every page, plus small reusable label helpers. Kept separate
from data_access.py/model_view.py/research_view.py so pages don't embed
their own copies of this boilerplate.
"""
from __future__ import annotations

import streamlit as st

from dashboard import data_access as da

MODEL_INPUT = "MODEL INPUT"
RESEARCH_METRIC = "RESEARCH METRIC — NOT CURRENTLY USED BY MODEL"
NOT_AVAILABLE = "NOT AVAILABLE IN HISTORICAL RESEARCH MODE"


def render_model_status_header() -> None:
    st.markdown(
        f"""
        <div style="border:1px solid #3a3f4b; border-radius:8px; padding:10px 16px;
                    background:#161a22; margin-bottom:10px;">
          <span style="color:#8b93a7; font-size:0.78rem; letter-spacing:.05em;">MODEL STATUS</span><br/>
          <span style="color:#e8ecf5; font-size:1.05rem; font-weight:600;">{da.MODEL_STATUS}</span>
          <span style="color:#5c6579; font-size:0.85rem;"> &mdash; not a proven profitable betting model</span>
        </div>
        """,
        unsafe_allow_html=True,
    )


def render_data_mode_badge() -> None:
    st.markdown(
        f"""
        <div style="display:inline-block; border:1px solid #3a5f4a; border-radius:6px;
                    padding:4px 10px; background:#132018; color:#7fd9a0;
                    font-size:0.78rem; letter-spacing:.04em; margin-bottom:12px;">
          DATA MODE: {da.DATA_MODE}
        </div>
        """,
        unsafe_allow_html=True,
    )


def render_provenance_panel() -> None:
    with st.expander("Data provenance & status", expanded=False):
        st.markdown(
            f"""
- **NHL results source:** NHL Web API / real historical corpus (`research/real_nhl_results/`)
- **MoneyPuck:** ARCHIVAL_RESEARCH (downloaded once, not a live feed)
- **MoneyPuck xG model version semantics:** UNKNOWN (see `MONEYPUCK_TEAM_INGESTION_REPORT.md`)
- **Current model status:** {da.MODEL_STATUS} — baseline production Elo model, unmodified
- **Historical odds:** NOT YET INTEGRATED
- **Goalie starter intelligence:** NOT YET INTEGRATED
- **Data mode:** {da.DATA_MODE} — no live current-season game feed is wired up in this project yet
            """
        )


def render_missing_data_page(error: Exception) -> None:
    st.error("Required local research data is missing.")
    st.code(str(error))
    st.markdown(
        "See `README.md`'s dashboard section, or the reports named above, for setup steps. "
        "This page cannot render without the file(s) listed."
    )


def render_odds_not_connected() -> None:
    st.markdown(
        """
        <div style="border:1px dashed #3a3f4b; border-radius:6px; padding:8px 12px;
                    color:#8b93a7; font-size:0.85rem;">
          ODDS DATA: NOT CONNECTED
        </div>
        """,
        unsafe_allow_html=True,
    )


CONFIDENCE_COLORS = {"HIGH": ("#123a24", "#3ecf8e"), "MEDIUM": ("#4a3a12", "#e8c46a"), "LOW": ("#4a1414", "#e8776a")}


def render_confidence_badge(label: str, low_confidence_negative_skill: bool = False,
                             market_type: str | None = None) -> None:
    """Standardized confidence badge (Confidence Framework audit, Part
    28) -- the SAME visual language across every player-prop page (SOG,
    Blocks, Assists, Points), rather than each page inventing its own
    label styling. `low_confidence_negative_skill=True` renders the
    explicit reliability warning from Part 29 for LOW predictions on
    props where that bucket has been directly measured to show negative
    Brier skill (see CONFIDENCE_FRAMEWORK_REDESIGN_REPORT.md) -- LOW is
    never allowed to look like "just a softer MEDIUM" on those props.

    `market_type`, if given, folds the Part 12/13 bet-eligibility policy
    note into this SAME warning (never a second box) whenever the
    registry marks that market WATCH_ONLY for LOW confidence -- one
    coherent explanation, not a methodology text wall."""
    bg, fg = CONFIDENCE_COLORS.get(label, ("#2a2d35", "#7d8394"))
    st.markdown(
        f'<span style="background:{bg}; color:{fg}; padding:3px 10px; border-radius:4px; '
        f'font-weight:600; letter-spacing:.03em; font-size:0.85rem;">CONFIDENCE: {label}</span>',
        unsafe_allow_html=True,
    )
    if label != "LOW":
        return

    policy_note = ""
    if market_type is not None:
        from research.player_props import registry
        entry = registry.get(market_type)
        if entry is not None and entry.low_confidence_bet_eligibility == "WATCH_ONLY":
            policy_note = (f" Reliability gate: LOW-confidence {market_type.replace('_', ' ').title()} "
                            f"predictions are not currently BET-eligible (WATCH only) under future live pricing.")

    if low_confidence_negative_skill or policy_note:
        base = ("⚠ MODEL HISTORICALLY WEAK IN SIMILAR CASES — this prop's LOW-confidence bucket has "
                "measured NEGATIVE Brier skill in real evaluation (worse than the naive base rate)."
                if low_confidence_negative_skill else "⚠")
        st.caption(f"{base}{policy_note} See CONFIDENCE_FRAMEWORK_REDESIGN_REPORT.md.")


STATUS_BANNER_STYLES = {
    "VALIDATED": ("#123a24", "#3ecf8e", "VALIDATED"),
    "PARTIAL": ("#4a3a12", "#e8c46a", "PARTIAL"),
    "RESEARCH": ("#16283f", "#7fb3e8", "RESEARCH"),
    "REJECTED": ("#4a1414", "#e8776a", "REJECTED"),
    "INSUFFICIENT_DATA": ("#2a2d35", "#9aa3b5", "INSUFFICIENT DATA"),
    "SHADOW_VALIDATED": ("#123a37", "#4fd1c5", "SHADOW VALIDATED"),
    "NOT_OPERATIONAL": ("#2a2d35", "#9aa3b5", "NOT OPERATIONAL"),
}


def render_status_banner(status: str, headline: str, detail: str = "") -> None:
    """Section 49: one shared status banner replacing the 17 hand-written
    near-duplicate per-page banners found in the dashboard audit.
    `status` must be one of STATUS_BANNER_STYLES' keys."""
    bg, fg, label = STATUS_BANNER_STYLES.get(status, ("#2a2d35", "#9aa3b5", status))
    detail_html = f'<div style="color:#8b93a7; font-size:0.82rem; margin-top:4px;">{detail}</div>' if detail else ""
    st.markdown(
        f"""
        <div style="border:1px solid {fg}44; border-radius:8px; padding:10px 16px;
                    background:{bg}; margin-bottom:12px;">
          <span style="color:{fg}; font-weight:700; letter-spacing:.04em; font-size:0.82rem;">{label}</span>
          <span style="color:#e8ecf5; margin-left:8px;">{headline}</span>
          {detail_html}
        </div>
        """,
        unsafe_allow_html=True,
    )


EMPTY_STATE_MESSAGES = {
    "NO_GAMES": ("🏒", "No games today", "The NHL schedule shows no games scheduled for this date."),
    "NO_LIVE_MARKETS": ("📡", "Waiting for live NHL markets", "No sportsbook markets are currently posted."),
    "WAITING_FOR_ODDS": ("⏳", "Waiting for odds", "Odds have not been fetched for this slate yet."),
    "WAITING_FOR_STARTER": ("🧤", "Waiting for starter confirmation", "Projections withheld until a starter is confirmed."),
    "STALE_DATA": ("⚠", "Data is stale", "The underlying data source has not refreshed recently."),
    "MODEL_NOT_OPERATIONAL": ("🚧", "Model not operational", "This market has no validated model backing it yet."),
    "NO_QUALIFYING_OPPORTUNITIES": ("🔍", "No qualifying opportunities", "Try widening your filters."),
    "ERROR": ("❗", "Something went wrong", "See technical detail below."),
}


def render_empty_state(kind: str, detail: str = "") -> None:
    """Section 50: one shared empty-state renderer. `kind` must be a key
    of EMPTY_STATE_MESSAGES."""
    icon, title, default_detail = EMPTY_STATE_MESSAGES.get(kind, ("❔", kind, ""))
    st.markdown(
        f"""
        <div style="border:1px dashed #3a3f4b; border-radius:8px; padding:32px 16px;
                    text-align:center; color:#8b93a7;">
          <div style="font-size:1.6rem; margin-bottom:6px;">{icon}</div>
          <div style="color:#c7ccd9; font-weight:600; font-size:0.95rem; margin-bottom:4px;">{title}</div>
          <div style="font-size:0.85rem;">{detail or default_detail}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


DECISION_COLORS = {
    "BET": ("#123a24", "#3ecf8e"), "WATCH": ("#4a3a12", "#e8c46a"),
    "WAIT": ("#16283f", "#7fb3e8"), "PASS": ("#2a2d35", "#9aa3b5"),
    "DATA_UNAVAILABLE": ("#4a1414", "#e8776a"), "MODEL_NOT_OPERATIONAL": ("#4a1414", "#e8776a"),
}

# Plain-language context-state labels for operational cards (UX refinement
# Section B5) -- the exact machine state name (e.g. COLD_AND_TOI_DECLINE)
# stays available in the tooltip/technical detail, never hidden, just not
# the loudest text on the card.
CONTEXT_STATE_PLAIN_LABEL = {
    "COLD_AND_TOI_DECLINE": "COLD + ROLE DECLINE",
}
CONTEXT_STATE_TOOLTIP = (
    "Recent production below expectation combined with a PIT-safe historical decline in ice "
    "time/role. Historically validated as a probability adjustment for Goals 1+ and Points 1+ "
    "(COLD_AND_TOI_DECLINE); prospective 2026-27 validation is still PENDING."
)


def render_opportunity_card(card: dict) -> None:
    """Section 41/42/45: the canonical reusable opportunity card, ported
    from the HTML prototype's opportunityCard() and refined per the UX
    addendum's decision hierarchy:
      1. decision/readiness (biggest, top-right)
      2. current market price + max acceptable price + conservative edge
         (most prominent numeric row)
      3. secondary probability row (raw/adjusted/no-vig/fair odds)
      4. confidence
      5. compact drivers/risks (2 max shown, rest in an expander)
      6. compact freshness footer

    `card` keys (all optional except player/market/threshold/decision):
      player, team, opponent, market, threshold, decision, confidence,
      raw_probability, context_adjusted_probability, coherent_probability,
      conservative_probability, market_no_vig_probability, fair_odds,
      current_odds, max_acceptable_price, conservative_edge, ev,
      context_state, context_raw, context_adjusted, context_delta,
      drivers (list[str]), risks (list[str]),
      prediction_timestamp, odds_timestamp, price_status
    """
    from dashboard import formatting as fmt

    decision = card.get("decision", "PASS")
    bg, fg = DECISION_COLORS.get(decision, ("#2a2d35", "#9aa3b5"))
    confidence = card.get("confidence")

    header_cols = st.columns([3, 1])
    with header_cols[0]:
        st.markdown(f"**{card.get('player', '—')}** &nbsp;·&nbsp; {card.get('team', '')} vs {card.get('opponent', '')}")
        st.caption(f"{card.get('market', '')} {card.get('threshold', '')}")
    with header_cols[1]:
        st.markdown(
            f'<div style="text-align:right;"><span style="background:{bg}; color:{fg}; padding:3px 10px; '
            f'border-radius:5px; font-weight:700; font-size:0.85rem;">{decision}</span></div>',
            unsafe_allow_html=True,
        )
        if confidence:
            render_confidence_badge(confidence)

    if card.get("context_state") == "COLD_AND_TOI_DECLINE":
        plain = CONTEXT_STATE_PLAIN_LABEL.get(card["context_state"], card["context_state"])
        st.markdown(
            f"""
            <div style="border:1px solid #2c7a7244; border-radius:6px; padding:6px 10px;
                        background:#123a3722; font-size:0.82rem; margin:6px 0;" title="{CONTEXT_STATE_TOOLTIP}">
              <span style="color:#4fd1c5; font-weight:700;">CONTEXT ADJUSTMENT ACTIVE — {plain}</span><br/>
              Raw {fmt.format_probability(card.get('context_raw'))} → Adjusted
              {fmt.format_probability(card.get('context_adjusted'))}
              ({fmt.format_pp_delta(card.get('context_delta'))})
              <span style="color:#8b93a7;"> — HISTORICALLY VALIDATED, PROSPECTIVE PENDING</span>
            </div>
            """,
            unsafe_allow_html=True,
        )

    # Primary row: the most decision-relevant numbers (UX addendum B2)
    primary = st.columns(4)
    primary[0].metric("Current Odds", fmt.format_american_odds(card.get("current_odds")))
    primary[1].metric("Max Acceptable Price", fmt.format_american_odds(card.get("max_acceptable_price")))
    primary[2].metric("Conservative Edge", fmt.format_edge(card.get("conservative_edge")))
    primary[3].metric("Conservative P", fmt.format_probability(card.get("conservative_probability")) or
                       fmt.NOT_YET_AVAILABLE)

    # Secondary row: model/market detail
    secondary = st.columns(4)
    secondary[0].caption(f"Raw P: {fmt.format_probability(card.get('raw_probability'))}")
    secondary[1].caption(f"Adjusted P: {fmt.format_probability(card.get('context_adjusted_probability'))}")
    secondary[2].caption(f"Market No-Vig: {fmt.format_probability(card.get('market_no_vig_probability')) if card.get('market_no_vig_probability') is not None else fmt.NOT_AVAILABLE}")
    secondary[3].caption(f"Fair Odds: {fmt.format_american_odds(card.get('fair_odds'))}")

    drivers = card.get("drivers") or []
    risks = card.get("risks") or []
    if drivers[:2]:
        for d in drivers[:2]:
            st.markdown(f"<span style='color:#3ecf8e; font-size:0.82rem;'>+ {d}</span>", unsafe_allow_html=True)
    if risks[:2]:
        for r in risks[:2]:
            st.markdown(f"<span style='color:#e8c46a; font-size:0.82rem;'>− {r}</span>", unsafe_allow_html=True)
    if len(drivers) > 2 or len(risks) > 2:
        with st.expander("View details"):
            for d in drivers[2:]:
                st.markdown(f"+ {d}")
            for r in risks[2:]:
                st.markdown(f"− {r}")

    footer_bits = []
    if card.get("prediction_timestamp"):
        footer_bits.append(f"Prediction: {fmt.format_timestamp(card['prediction_timestamp'])}")
    if card.get("odds_timestamp"):
        footer_bits.append(f"Odds: {fmt.format_timestamp(card['odds_timestamp'])}")
    if footer_bits:
        st.caption(" · ".join(footer_bits))
    st.divider()


def _route_to_search_result(r) -> None:
    if r.entity_type == "PLAYER":
        st.session_state["selected_player_id"] = r.entity_id
        st.switch_page("pages/25_Player_Intelligence.py")
    elif r.entity_type == "GOALIE":
        # Goalies have no skater props (SOG/Goals/Assists/Points/Blocked
        # Shots), so Player Intelligence can't represent one -- route to
        # Team Intelligence, which already surfaces per-team goalie
        # context, instead of a page that will always 404 on a goalie_id.
        from dashboard import demo_data as dd
        goalie = next((g for g in dd.build_demo_goalies() if g["goalie_id"] == r.entity_id), None)
        if goalie is not None:
            st.session_state["selected_team"] = goalie["team"]
            st.switch_page("pages/31_Team_Intelligence.py")
        else:
            st.switch_page("pages/27_Goalies.py")
    elif r.entity_type == "TEAM":
        st.session_state["selected_team"] = r.entity_id
        st.switch_page("pages/31_Team_Intelligence.py")
    elif r.entity_type == "GAME":
        st.session_state["selected_game_id"] = r.entity_id
        st.switch_page("pages/2_Game_Detail.py")
    elif r.entity_type == "MARKET":
        st.session_state["selected_market_filter"] = r.entity_id
        st.switch_page("pages/26_Player_Props.py")


def render_global_search(key_prefix: str = "global") -> None:
    """Part 25: a global smart search bar, callable from any page's
    header. Real fuzzy matching (dashboard/search.py) over a real
    canonical index (demo roster/goalies/games + market_registry
    aliases) -- never a per-keystroke corpus scan (the index is built
    once and cached)."""
    from dashboard import search as search_mod

    query = st.text_input("🔍 Search player, team, game or market...",
                           key=f"{key_prefix}_search_query", placeholder='Try "Connor McDavid"')
    if not query:
        return
    results = search_mod.search(query, limit=6)
    if not results:
        st.caption("No matches.")
        return
    for r in results:
        if st.button(f"{r.display} — {r.subtitle}", key=f"{key_prefix}_result_{r.entity_type}_{r.entity_id}"):
            _route_to_search_result(r)


def render_odds_detail_panel(o: dict) -> None:
    """Preseason Closing sprint, Track 3 (Sections 41-46): a compact
    detail panel for one opportunity's full pricing breakdown. Demo
    prices are prominently labeled SIMULATED MARKET PRICE (Section 43);
    a real price would show its actual sportsbook/source instead
    (Section 44) -- callers pass `o["is_simulated_price"]` to control
    which label renders, never both."""
    from dashboard import formatting as fmt

    is_simulated = o.get("is_simulated_price", True)
    with st.container(border=True):
        if is_simulated:
            st.markdown(
                '<span style="color:#e8c46a; font-weight:700; font-size:0.8rem;">⚠ SIMULATED MARKET PRICE</span>',
                unsafe_allow_html=True)
        else:
            st.markdown(
                f'<span style="color:#4fd1c5; font-weight:700; font-size:0.8rem;">'
                f'{o.get("sportsbook", "SPORTSBOOK")} — LIVE PRICE</span>',
                unsafe_allow_html=True)

        d1, d2, d3 = st.columns(3)
        d1.metric("Player", o.get("player", "—"))
        d1.caption(f"{o.get('market', '')} {o.get('threshold', '')}")
        d2.metric("Current Odds", fmt.format_american_odds(o.get("current_odds")))
        d2.caption(f"Decimal: {fmt.format_decimal_odds(_american_to_decimal_safe(o.get('current_odds')))}")
        d3.metric("Max Buy", fmt.format_american_odds(o.get("max_acceptable_price")),
                   help="Maximum acceptable sportsbook price under current model and policy assumptions.")
        price_status = "NO LIVE PRICE"
        if o.get("current_odds") is not None and o.get("max_acceptable_price") is not None:
            from pricing import odds_math as pm
            current_p = pm.american_to_prob(o["current_odds"])
            max_p = pm.american_to_prob(o["max_acceptable_price"])
            price_status = "PRICE OK" if current_p <= max_p else "TOO EXPENSIVE"
        d3.caption(f"Status: **{price_status}**")

        st.markdown("---")
        e1, e2, e3, e4 = st.columns(4)
        e1.metric("Raw P", fmt.format_probability(o.get("raw_probability")))
        e2.metric("No-Vig P", fmt.format_probability(o.get("market_no_vig_probability")))
        e3.metric("Fair Odds", fmt.format_american_odds(o.get("fair_odds")))
        e4.metric("Edge", fmt.format_edge(o.get("conservative_edge")))
        st.caption(f"Decision: **{o.get('decision', '—')}** — {o.get('decision_reason', '')}")


def _american_to_decimal_safe(odds: float | None) -> float | None:
    if odds is None:
        return None
    from pricing import odds_math as pm
    return pm.american_to_decimal(odds)


_PP_TRANSITION_VERBS = {
    "PROMOTED_PP2_TO_PP1": "PROMOTED", "ADDED_TO_PP1": "PROMOTED", "ADDED_TO_PP2": "ADDED",
    "DEMOTED_PP1_TO_PP2": "DEMOTED", "REMOVED_FROM_PP": "REMOVED",
}
_PP_CERTAINTY_LABEL = [(0.8, "HIGH"), (0.4, "MEDIUM"), (0.0, "LOW")]


def render_pp_role_badge(role_state: dict) -> None:
    """Live Special-Teams Role Shadow sprint, Parts 26-28: the compact
    POWER PLAY ROLE display, driven entirely by
    operational.special_teams_roles_live's real role_state dict -- never
    a second, dashboard-side reimplementation of the role classifier.
    Uses PROJECTED (Part 10), never CONFIRMED -- this is always an
    inference from realized ice time, not a lineup confirmation from any
    legitimate source."""
    state = role_state.get("state")
    certainty = role_state.get("certainty")
    if certainty is None:
        n_recent, n_baseline = role_state.get("n_recent") or 0, role_state.get("n_baseline") or 0
        from research.special_teams_role_overlay import core as ov_core
        certainty = ov_core.role_certainty(n_recent, n_baseline)
    certainty_label = next(label for threshold, label in _PP_CERTAINTY_LABEL if certainty >= threshold)

    st.markdown("**Power Play Role**")
    if state in (None, "ROLE_UNCERTAIN"):
        st.markdown(label_badge("ROLE UNCERTAIN", "unavailable"), unsafe_allow_html=True)
        st.caption(role_state.get("reason") or "Insufficient recent PP usage history.")
        return

    recent_role = role_state.get("recent_role")
    if recent_role:
        st.markdown(label_badge(f"PROJECTED {recent_role}", "research"), unsafe_allow_html=True)

    if state in _PP_TRANSITION_VERBS:
        verb = _PP_TRANSITION_VERBS[state]
        baseline_role = role_state.get("baseline_role") or "NONE"
        games_since = role_state.get("games_since_onset")
        st.caption(f"Previous: {baseline_role}  ·  Change: {verb}"
                   + (f"  ·  Games in new role: {games_since}" if games_since is not None else ""))
    elif state.startswith("STABLE_"):
        st.caption(f"Stable: {role_state.get('n_recent', 0) + role_state.get('n_baseline', 0)} games")
    elif state == "NO_MEANINGFUL_PP":
        st.caption("No meaningful power-play usage in recent games.")

    st.caption(f"Role certainty: {certainty_label}")


def label_badge(text: str, kind: str = "input") -> str:
    """Returns an HTML span for MODEL_INPUT (blue) / RESEARCH_METRIC
    (amber) / NOT_AVAILABLE (grey) labels -- use via st.markdown(...,
    unsafe_allow_html=True)."""
    colors = {
        "input": ("#1c3a5c", "#8ec4f5"),
        "research": ("#4a3a12", "#e8c46a"),
        "unavailable": ("#2a2d35", "#7d8394"),
    }
    bg, fg = colors.get(kind, colors["unavailable"])
    return (f'<span style="background:{bg}; color:{fg}; padding:2px 8px; border-radius:4px; '
            f'font-size:0.72rem; letter-spacing:.03em;">{text}</span>')
