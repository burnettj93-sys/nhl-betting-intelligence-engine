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

ui.header("Slate Board", "Every game today, analysed, and one primary parlay built from the strongest supported selections.")
board = ui.load(ps.slate_board, "The slate board")
if not board.get("games") and not board.get("generated_at_utc"):
    ui.unavailable("The board has not been written yet; the engine writes it with every 15-minute update.", "The slate board")
    st.stop()

st.caption(f"Board built {ui.et_time(board.get('generated_at_utc'), True)} for Eastern date {board.get('date_et')}. Prices are DraftKings US-feed quotes with the provider's own quote times; "
           "probabilities are estimates, and a lower threshold is not a safe one. Nothing is recorded by looking at this page.")

# ---------------------------------------------------------------- the primary parlay
primary = board.get("primary") or {}
st.subheader("Primary parlay")
pol = board.get("policy") or {}
if board.get("recording_paused"):
    st.caption("Automatic recording is paused: this is the slip the selector would record, shown for review. Nothing is staked.")
ticket = primary.get("ticket")
if ticket:
    ui.ticket_card(ticket)
    anchor = ticket.get("anchor") or {}
    kind = ("Calculated price: the product of the legs' DraftKings prices (the legs are from different games, so no combined quote exists or is needed)."
            if primary.get("price_kind") == "CALCULATED" else "")
    st.caption(f"{kind} Preferred because it has the highest estimated hit chance among the qualifying combinations priced from +100 to +500, using the fewest legs. "
               f"{ui.esc(primary.get('reason') or '')}")
    with st.expander("Calculations and why not the others"):
        st.markdown(ui.esc(anchor.get("rationale") or ticket.get("rationale") or ""))
        if anchor:
            st.write({k: v for k, v in anchor.items() if k in ("hit_low", "hit_high", "ev_estimated", "ev_after_haircut", "ev_midpoint", "ev_after_shade") and v is not None})
else:
    ui.banner("<b>No primary parlay qualifies right now.</b> " + ui.esc(primary.get("reason") or "No combination of supported selections reaches +100 to +500 with value at the prices available."), "warn")
    singles = primary.get("strongest_singles") or []
    if singles:
        st.markdown("**Strongest individual selections** (informational: a single is not a recommendation)")
        for s in singles:
            st.markdown(f"- **{ui.esc(s['label'])}** {ui.american(s['american_price'])} · estimate {ui.pct(s['probability'])} · value {s['ev_estimated']:+.0%} "
                        f"({s['ev_after_haircut']:+.0%} after the haircut) · quote {ui.et_time(s.get('quote_updated_utc'), True)}{' · provisional' if s.get('provisional') else ''}")
    st.caption("A slot left empty is a correct answer: the engine does not loosen its rules to produce a slip.")

# ---------------------------------------------------------------- coverage
c = board.get("counts") or {}
st.divider()
cols = st.columns(5)
cols[0].metric("Games", c.get("scheduled", 0))
cols[1].metric("Analysed", c.get("analysed", 0))
cols[2].metric("Shots priced", c.get("shots_priced", 0))
cols[3].metric("Goals priced", c.get("goals_priced", 0))
cols[4].metric("Moneylines", c.get("moneyline_priced", 0))
prev = board.get("previous_day")
if prev:
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
    with st.expander(f"{title}   ·   " + " · ".join(chips), expanded=len(board.get("games", [])) <= 3):
        if g.get("state") != "SCHEDULED":
            st.info(f"This game is {str(g.get('state')).lower()}; the pregame board applies to scheduled games.")
            continue
        t1, t2, t3, t4, t5 = st.tabs(["Goal scorers", "Shots value", "Goalie saves", "Side", "Coverage"])
        with t1:
            st.caption(SECTION_NOTE["scorers"])
            sc = g.get("scorers") or []
            if not sc:
                st.write("No skater has a supported projection for this game (sample or role too small).")
            for r in sc:
                price = r.get("price")
                val = (f" · DraftKings {ui.american(price['american'])} (value {price['ev_estimated']:+.0%}, {price['ev_after_haircut']:+.0%} after the haircut)" if price else " · no price on file")
                tag = " · **best value at its price**" if r.get("best_value_at_price") else (" · outside the top five, listed for value" if r.get("outside_top") else "")
                st.markdown(f"**{(str(r['rank']) + '. ') if r.get('rank') else ''}{ui.esc(r['name'])}** ({r['team']}) — {ui.pct(r['probability'])} to score{val}{tag}")
                st.caption(ui.esc(r["explanation"]))
        with t2:
            st.caption(SECTION_NOTE["shots"])
            sh = g.get("shots") or {}
            for c_ in sh.get("picks") or []:
                ch = c_["chosen"]
                st.markdown(f"**{ui.esc(c_['name'])}** ({c_['team']}) — **{ch['threshold']}+ shots at {ui.american(ch['american'])}** · estimate {ui.pct(ch['estimate'])} · value {ch['ev_estimated']:+.0%} "
                            f"({ch['ev_after_haircut']:+.0%} after the haircut) · quote {ui.et_time(c_.get('quote_updated_utc'), True)}")
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
                if pr:
                    lo, hi = pr["saves_range_80"]
                    st.markdown(f"Expected saves **{pr['expected_saves']:.0f}** (80% range {lo}–{hi}) · shots against {pr['expected_shots_against']:.0f} · save rate used {pr['save_pct_used'] * 100:.1f}%")
                    st.caption(ui.esc(pr["explanation"]))
                ln = b.get("line")
                if ln:
                    st.markdown(f"DraftKings line **{ln['line']}** · over {ui.american(ln.get('over'))} · under {ui.american(ln.get('under'))} · quote {ui.et_time(ln.get('quote_updated_utc'), True)}")
                rec = b.get("recommendation") or {}
                st.caption(ui.esc(rec.get("text") or ""))
                if b.get("alternate"):
                    st.caption(f"Alternate: {ui.esc(b['alternate']['name'])} ({ui.pct(b['alternate'].get('start_probability'))}).")
        with t4:
            sd = g.get("side") or {}
            rec = sd.get("recommendation")
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
