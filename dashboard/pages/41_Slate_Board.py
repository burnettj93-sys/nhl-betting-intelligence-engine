"""Slate Board — every game of the Eastern day: likely goal scorers, the two best shots values, goalie saves and the better side, then the ONE primary parlay.

Read-only. Nothing here records a bet; the primary slip is what the engine's selector would record (it is paused until the owner approves the policy)."""
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import streamlit as st

from dashboard import product_source as ps
from dashboard import ui

from operational import quote_freshness as qf


def stale_now(quoted_at, start_utc) -> tuple[bool, str]:
    """Judged when the page is opened, from the provider's own quote time and the engine's price limit (the board's own flags are from when it was built, up to 15 minutes ago)."""
    now = ui._utcnow()
    start = qf.parse_utc(start_utc)
    hours = (start - now).total_seconds() / 3600.0 if start is not None else 99.0
    r = qf.assess(quoted_at, None, now, qf.price_limit_min(hours), require_retrieval=False)
    age = r.get("quote_age_min")
    return (not r["fresh"], "no quote time" if age is None else f"quoted {age:.0f} min ago")


ui.header("Slate Board", "Every game today, analysed, and one primary parlay built from the strongest supported selections.")
board = ui.load(ps.slate_board, "The slate board")
if not board.get("games") and not board.get("generated_at_utc"):
    ui.unavailable("The board has not been written yet; the engine writes it with every 10-minute update.", "The slate board")
    st.stop()

if board.get("policy_error"):
    ui.banner("<b>The ticket policy could not be loaded.</b> " + ui.esc(board["policy_error"]) + " Recording is blocked. Value figures below use a fallback 3-point haircut and are not the slip's.", "bad")
if board.get("input_errors"):
    st.warning(f"A saves capture could not be read for {len(board['input_errors'])} game(s); those games' saves lines are shown as unavailable, not as 'not posted'.")
st.caption(f"Board built {ui.et_time(board.get('generated_at_utc'), True)} for Eastern date {board.get('date_et')}. Prices are DraftKings US-feed quotes with the provider's own quote times; "
           "probabilities are estimates, and a lower threshold is not a safe one. Nothing is recorded by looking at this page.")

# ---------------------------------------------------------------- the primary parlay
primary = board.get("primary") or {}
st.subheader("Eggy's Parlay Pick of the Day")
pol = board.get("policy") or {}
_official = bool((board.get("eggy") or {}).get("official_policy"))          # the parlay product: Eggy's output is the flagship parlay or PASS, not the retired primary slip
if _official:
    try:
        ui.parlay_product_banner(ps.learning_experiment())
    except Exception:  # noqa: BLE001 - the banner is context; the card below stands on its own
        pass
else:
    ui.recording_status_banner(board.get("recording"))
    if board.get("recording_paused"):
        st.caption("Automatic recording is paused: this is the slip the selector would record, shown for review. Nothing is staked.")
ui.eggy_candidate_card(board.get("eggy"), primary, recording_paused=bool(board.get("recording_paused")))
ticket = None if _official else primary.get("ticket")
if ticket:
    st.caption(f"{ui.esc(primary.get('price_note') or '')}")
    with st.expander("Calculations and why not the others"):
        anchor = ticket.get("anchor") or {}
        st.markdown(ui.esc(anchor.get("rationale") or ticket.get("rationale") or ""))
        if anchor:
            st.write({k: v for k, v in anchor.items() if k in ("hit_low", "hit_high", "ev_estimated", "ev_after_haircut", "ev_midpoint", "ev_after_shade") and v is not None})
else:
    closest = None if _official else primary.get("closest")
    if closest:
        legs_txt = " + ".join(f"{ui.esc(str(l['participant']))}{(' ' + str(l['threshold']) + '+') if l.get('threshold') else ' to win'} ({ui.american(l['american_price'])})" for l in closest["legs"])
        st.markdown(f"**Closest combination (does not qualify):** {legs_txt} → {ui.american(closest['combined_american'])} calculated · estimated hit chance {ui.pct(closest['hit_probability'])} · "
                    f"value {closest['ev_estimated']:+.1%} ({closest['ev_after_haircut']:+.1%} after the haircut)")
        st.caption(ui.esc(closest.get("note") or ""))
    singles = primary.get("strongest_singles") or []
    if singles:
        st.markdown("**Candidate legs: the strongest individual selections** (informational: a single leg is a candidate for a parlay, not a bet or a recommendation)" if _official
                    else "**Strongest individual selections** (informational: a single is not a recommendation)")
        for s in singles:
            stale, age = stale_now(s.get("quote_updated_utc"), None)
            st.markdown(f"- **{ui.esc(ui.leg_dict_label(s))}** · estimate {ui.pct(s['probability'])} · value {s['ev_estimated']:+.0%} "
                        f"({s['ev_after_haircut']:+.0%} after the haircut) · quote {ui.et_time(s.get('quote_updated_utc'), True)} ({age}){' · STALE' if stale else ''}"
                        f"{' · provisional' if s.get('provisional') else ''}")
    st.caption("Eggy passes when no GOOD parlay exists: a PASS is a correct answer, and the engine does not loosen its rules to produce a parlay." if _official
               else "A slot left empty is a correct answer: the engine does not loosen its rules to produce a slip.")

if (board.get("eggy") or {}).get("availability"):
    with st.expander("Why prices are missing: every market, game by game"):
        ui.eggy_availability(board.get("eggy"))
else:
    st.caption("The per-market explanation of missing prices is not in this snapshot yet; it appears with the next engine update.")

with st.expander("Today's capture timeline: waves, games priced, games excluded by budget, credits"):
    ui.day_timeline()

# ---------------------------------------------------------------- coverage
c = board.get("counts") or {}
st.divider()
if c.get("failed_games"):
    st.warning(f"{c['failed_games']} game(s) could not be analysed in the latest update; they are marked below. The rest of the board is current.")
cols = st.columns(5)
cols[0].metric("Games", c.get("scheduled", 0))
cols[1].metric("Analysed", c.get("analysed", 0))
cols[2].metric("Shots priced", c.get("shots_priced", 0))
cols[3].metric("Goals priced", c.get("goals_priced", 0))
cols[4].metric("Moneylines", c.get("moneyline_priced", 0))
prev = board.get("previous_day")
if prev and prev.get("error"):
    st.caption(f"The previous-day coverage check could not be built: {ui.esc(prev['error'])}")
elif prev:
    with st.expander(f"Coverage check: {prev['date_et']} ({prev['scheduled']} games)"):
        st.write(f"{prev['scheduled']} games scheduled · shots prices captured for {prev['shots_priced']} · games with shots legs evaluated in the frozen log: "
                 f"{prev['shots_legs_evaluated_in_games']} · games with a moneyline evaluated: {prev['moneyline_legs_evaluated_in_games']}.")
        st.caption(prev.get("note") or "")

# ---------------------------------------------------------------- games
SECTION_NOTE = {"scorers": "Most likely to score is not the same as good value at the price; goals are never put in a slip.",
                "shots": "Two best balances of likelihood and price on the offered 1+ to 5+ ladder (not the player's average)."}
for g in board.get("games", []):
    cov = g.get("coverage") or {}
    title = f"{g['away']} @ {g['home']} · {g.get('start_et') or ''}"
    chips = []
    for key, label in (("player_shots_on_goal_alternate", "shots"), ("player_goal_scorer_anytime", "goals"), ("player_total_saves", "saves")):
        st_ = (cov.get("markets") or {}).get(key, {})
        chips.append(f"{label}: {st_.get('word', '—')}")
    state_word = cov.get("price_state_word")
    with st.expander(f"{title}   ·   " + (f"[{state_word}]   ·   " if state_word else "") + " · ".join(chips), expanded=len(board.get("games", [])) <= 3):
        if g.get("error"):
            st.error(f"The analysis of this game failed and is not shown: {ui.esc(g['error'])}. Other games are unaffected; the engine retries on its next update.")
            continue
        if g.get("state") != "SCHEDULED":
            if g.get("frozen_at_utc"):
                src = {"PUBLISHED_SNAPSHOT_ARCHIVE": " (recovered from the published-snapshot archive: what the hosted board showed then)", "SAVED_PREGAME_BLOCK": ""}.get(g.get("frozen_source"), "")
                st.info(f"This game is {str(g.get('state')).lower()}. What follows is the pregame analysis as it stood at {ui.et_time(g['frozen_at_utc'], True)}{src}, kept for reference: "
                        "it is not recomputed with in-game information and is not a current recommendation.")
            else:
                st.info(f"This game is {str(g.get('state')).lower()}. Pregame analysis unavailable: none was saved for it before puck drop and no archive holds one.")
                continue
        t1, t2, t3, t4, t5 = st.tabs(["Goal scorers", "Shots value", "Goalie saves", "Side", "Coverage"])
        with t1:
            st.caption(SECTION_NOTE["scorers"])
            sc = g.get("scorers") or []
            if not sc:
                st.write("No skater has a supported projection for this game (sample or role too small).")
            for r in sc:
                price = r.get("price")
                p_stale, p_age = stale_now(price.get("quote_updated_utc"), g.get("start_utc")) if price else (False, "")
                val = (f" · DraftKings {ui.american(price['american'])} (value {price['ev_estimated']:+.0%}, {price['ev_after_haircut']:+.0%} after the haircut; {p_age}"
                       f"{' — STALE: shown for reference only' if p_stale else ''})" if price else " · no price on file")
                tag = " · **best value at its price**" if r.get("best_value_at_price") else (" · outside the top five, listed for value" if r.get("outside_top") else "")
                st.markdown(f"**{(str(r['rank']) + '. ') if r.get('rank') else ''}{ui.esc(r['name'])}** ({r['team']}) — {ui.pct(r['probability'])} to score{val}{tag}")
                st.caption(ui.esc(r["explanation"]))
        with t2:
            st.caption(SECTION_NOTE["shots"])
            sh = g.get("shots") or {}
            for c_ in sh.get("picks") or []:
                ch = c_["chosen"]
                c_stale, c_age = stale_now(c_.get("quote_updated_utc"), g.get("start_utc"))
                st.markdown(f"**{ui.esc(c_['name'])}** ({c_['team']}) — **{ch['threshold']}+ shots at {ui.american(ch['american'])}** · estimate {ui.pct(ch['estimate'])} · value {ch['ev_estimated']:+.0%} "
                            f"({ch['ev_after_haircut']:+.0%} after the haircut) · quote {ui.et_time(c_.get('quote_updated_utc'), True)} ({c_age}){' · STALE: reference only, not a current pick' if c_stale else ''}")
                st.caption(ui.esc(c_["explanation"]))
                with st.expander(f"Ladder for {c_['name']}"):
                    st.dataframe([{"Line": f"{x['threshold']}+", "Price": ui.american(x["american"]), "Estimate": ui.pct(x["estimate"]), "Model": ui.pct(x["model"]),
                                   "Record": f"{x['recent_hits']}/{x['recent_games']}", "Price implies": ui.pct(x["implied"]), "Value": f"{x['ev_estimated']:+.0%}",
                                   "After haircut": f"{x['ev_after_haircut']:+.0%}", "Slip-eligible": "yes" if x["supported_for_slip"] else "no"} for x in c_["ladder"]],
                                 hide_index=True, use_container_width=True)
            if sh.get("shortfall"):
                st.info("Fewer than two selections: " + ui.esc(sh["shortfall"]) + ".")
        with t3:
            for b in g.get("goalies") or []:
                s = b.get("starter")
                if not s:
                    st.write(f"{b.get('team')}: {b.get('reason')}")
                    continue
                conf = ("confirmed" if s["status"] == "CONFIRMED" else "UNCONFIRMED (estimate from recent usage)")
                st.markdown(f"**{ui.esc(s['name'])}** ({b['team']}) — {conf}; start chance {ui.pct(s.get('start_probability'))}"
                            + (f" · source {ui.esc(s['source'])} at {ui.et_time(s.get('checked_at_utc'), True)}" if s.get("source") else " · no confirmation source on file"))
                pr = b.get("projection")
                if not pr and b.get("projection_unavailable"):
                    st.caption(ui.esc(b["projection_unavailable"]))
                if pr:
                    lo, hi = pr["saves_range_80"]
                    st.markdown(f"Expected saves **{pr['expected_saves']:.0f}** (80% range {lo}–{hi}) · shots against {pr['expected_shots_against']:.0f} · save rate used {pr['save_pct_used'] * 100:.1f}%")
                    st.caption(ui.esc(pr["explanation"]))
                ln = b.get("line")
                if ln:
                    l_stale, l_age = stale_now(ln.get("quote_updated_utc"), g.get("start_utc"))
                    st.markdown(f"DraftKings line **{ln['line']}** · over {ui.american(ln.get('over'))} · under {ui.american(ln.get('under'))} · quote {ui.et_time(ln.get('quote_updated_utc'), True)} "
                                f"({l_age}){' · STALE: no recommendation from this line' if l_stale else ''}")
                rec = b.get("recommendation") or {}
                st.caption(ui.esc(rec.get("text") or ""))
                if b.get("book_line_note"):
                    st.info(ui.esc(b["book_line_note"]))
                alt = b.get("alternate")
                if alt:
                    st.caption(f"Alternate: {ui.esc(alt['name'])} ({ui.pct(alt.get('start_probability'))} to start; {'confirmed' if alt.get('status') == 'CONFIRMED' else 'unconfirmed'}).")
                    apr, aln = alt.get("projection"), alt.get("line")
                    if apr and apr.get("expected_saves") is not None and apr.get("saves_range_80"):
                        lo, hi = apr["saves_range_80"]
                        st.caption(f"If {ui.esc(alt['name'])} starts: expected saves {apr['expected_saves']:.0f} (80% range {lo}–{hi}).")
                    if aln:
                        a_stale, a_age = stale_now(aln.get("quote_updated_utc"), g.get("start_utc"))
                        chance = (f" · model over {ui.pct(aln['p_over'])} / under {ui.pct(aln['p_under'])}" if aln.get("p_over") is not None else "")
                        st.markdown(f"DraftKings line for {ui.esc(alt['name'])} **{aln['line']}** · over {ui.american(aln.get('over'))} · under {ui.american(aln.get('under'))}{chance} · quote {ui.et_time(aln.get('quote_updated_utc'), True)} "
                                    f"({a_age}){' · STALE' if a_stale else ''}")
                        st.caption(ui.esc((alt.get("recommendation") or {}).get("text") or ""))
        with t4:
            sd = g.get("side") or {}
            rec = sd.get("recommendation")
            if rec:
                _both = [stale_now(v_.get("quote_captured_at_utc"), g.get("start_utc")) for v_ in (sd.get("sides") or {}).values()] or [stale_now(rec.get("quote_captured_at_utc"), g.get("start_utc"))]
                r_stale = any(x[0] for x in _both)                    # the no-vig estimate rests on BOTH sides' quotes: either one aging out withdraws it
                r_age = next((x[1] for x in _both if x[0]), _both[0][1])
                if r_stale:                       # the board was built when the quote was fresh; it has aged past its limit since
                    ui.banner(f"<b>Not a current recommendation.</b> The {ui.esc(rec['team'])} moneyline quote is {ui.esc(r_age)}, past its freshness limit. It is shown for reference and "
                              "cannot be recorded; it returns when the price is refreshed.", "warn")
                    rec = None
                    sd = {**sd, "reason": "The moneyline quote has aged past its freshness limit."}
            if rec:
                st.markdown(f"**{rec['market']}: {rec['team']} {ui.american(rec['american'])}** · estimate {ui.pct(rec['estimate'])} · model {ui.pct(rec.get('model'))}")
                st.caption(ui.esc(rec["explanation"]))
            else:
                st.markdown("**No recommended side**")
                st.caption(ui.esc(sd.get("reason") or ""))
            for k_, v_ in (sd.get("sides") or {}).items():
                st.caption(f"{v_['team']}: {ui.american(v_['american'])} · no-vig {ui.pct(v_['no_vig'])} · model {ui.pct(v_.get('model'))}")
            st.caption(ui.esc((sd.get("puck_line") or {}).get("text") or ""))
        with t5:
            st.markdown("**Analysed:** " + ("yes" if cov.get("analysis_available") else "no") + f" · skaters projected {cov.get('skaters_projected')} (eligible {cov.get('skaters_eligible')}) · "
                        f"goalies projected {cov.get('goalies_projected')} · team model {'yes' if cov.get('team_model') else 'no'}")
            for key, label in (("player_shots_on_goal_alternate", "Shots prices"), ("player_goal_scorer_anytime", "Anytime-goal prices"), ("player_total_saves", "Saves line")):
                m = (cov.get("markets") or {}).get(key, {})
                st.caption(f"{label}: {m.get('word', '—')}" + (f" · checked {ui.et_time(m.get('checked_utc'), True)}" if m.get("checked_utc") else "") + (f" · {ui.esc(m['detail'])}" if m.get("detail") else ""))
            st.markdown("**Models:** " + ", ".join(f"{k.replace('_', ' ')}: {v.replace('_', ' ').lower()}" for k, v in (cov.get("models") or {}).items()))
            st.markdown("**Recommended:** " + (", ".join(cov.get("recommended") or []) or "none"))
            for m_ in cov.get("missing") or []:
                st.caption(f"Missing — {m_['item']}: {ui.esc(m_['reason'])}")

st.divider()
for n in board.get("notes") or []:
    st.caption(ui.esc(n))
