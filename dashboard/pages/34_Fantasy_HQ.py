"""Page 34 -- Fantasy HQ (Yahoo Fantasy Hockey sprint, 2026-09-01, Parts
22-25). Not connected to a real Yahoo account yet (OWNER_AUTH_REQUIRED
-- see 35_Fantasy_Settings.py), so this shows FANTASY DEMO MODE: the
real demo roster (same real player identities the betting engine's
demo already uses) scored against the owner's own real league settings
(Part 121), never presented as the owner's actual current roster."""
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import streamlit as st

from dashboard import auth
from dashboard import components as comp
from dashboard import demo_data as dd
from fantasy.league.demo_league import build_demo_league_settings
from fantasy.league.scoring import points_value
from fantasy.projections.fantasy_projection import project_goalie_categories, project_skater_categories
from fantasy.recommendations.lineup_optimizer import RosterPlayer, optimize_lineup, start_sit_recommendation
from fantasy.yahoo import oauth as yoauth
from fantasy.yahoo.token_store import EncryptedFileTokenStore

# P0.7 (2026-09-24 hardening block): server-side enforcement, not just a
# hidden nav link -- a USER-role (or logged-out) session's script
# execution stops on the next line, before any Yahoo/fantasy logic runs.
auth.require_admin()

st.title("Fantasy HQ")
comp.render_model_status_header()

creds = yoauth.load_app_credentials()
# Yahoo compliance rebuild (2026-09-24): connection state now comes from
# the encrypted token store, not fantasy_store's old plaintext table --
# see docs/YAHOO_COMPLIANCE_REBUILD.md. This page still only shows demo
# data either way (Part above): real Yahoo-sourced roster/settings
# content must never be persisted, so it is never read into this page
# from a snapshot table -- only fetched transiently by the diagnostic.
_connected = EncryptedFileTokenStore().get() is not None

if not _connected:
    st.markdown(
        """
        <div style="border:1px solid #5a4420; border-radius:6px; padding:8px 12px;
                    background:#241c10; color:#e8c46a; font-size:0.85rem; margin-bottom:12px;">
          FANTASY DEMO MODE — not connected to a real Yahoo account. Shown below is this engine's
          existing real demo roster, scored against a real reference league's settings
          (Goals/Assists/PIM/PPP/SOG/HIT/BLK for skaters; W/GA/SV/SHO for goalies) — never your
          actual current Yahoo roster or matchup.
        </div>
        """,
        unsafe_allow_html=True,
    )
    if st.button("Connect Yahoo Fantasy"):
        st.switch_page("pages/35_Fantasy_Settings.py")
else:
    st.caption("Connected — real roster/matchup sync is NOT_IMPLEMENTED_THIS_SPRINT yet; "
               "showing Fantasy Demo Mode below regardless of connection status.")

st.divider()

settings = build_demo_league_settings()
stack = dd._demo_context()
roster = dd.build_demo_roster()
goalies = dd.build_demo_goalies()

_POSITION_MAP = {"C": ("C",), "D": ("D",), "L": ("LW",), "R": ("RW",)}

roster_players = []
for p in roster:
    projections = project_skater_categories(stack, p.player_id, p.team, p.opponent, dd.SIMULATED_DATE,
                                             dd.SIMULATED_SEASON)
    if not projections:
        continue
    eligible = _POSITION_MAP.get(p.position, ()) + ("Util",)
    roster_players.append(RosterPlayer(player_id=p.player_id, name=p.name, eligible_positions=eligible,
                                        projections=projections))

for g in goalies:
    if g["expected_saves"] is None:
        continue
    projections = project_goalie_categories(g["expected_saves"], g["confidence"])
    roster_players.append(RosterPlayer(player_id=g["goalie_id"], name=g["name"],
                                        eligible_positions=("G",), projections=projections, is_goalie=True))

assignments = optimize_lineup(settings, roster_players)
starters = [a for a in assignments if a.assigned_slot != "BN"]
bench = [a for a in assignments if a.assigned_slot == "BN"]
total_value = sum(a.value or 0.0 for a in starters)

st.markdown("## Today's Best Lineup")
c1, c2, c3 = st.columns(3)
c1.metric("Projected starting lineup points", f"{total_value:.1f}")
c2.metric("Players with a real projection", len(roster_players))
c3.metric("Bench", len(bench))

st.markdown("### Starters")
for a in sorted(starters, key=lambda a: -( a.value or 0)):
    rec = start_sit_recommendation(a)
    col1, col2, col3 = st.columns([2, 1, 1])
    col1.markdown(f"**{a.name}** — {a.assigned_slot}")
    col2.caption(f"Value {a.value:.2f}" if a.value is not None else "—")
    col3.markdown(comp.label_badge(rec, "input"), unsafe_allow_html=True)

with st.expander(f"Bench ({len(bench)})"):
    for a in sorted(bench, key=lambda a: -(a.value or 0)):
        rec = start_sit_recommendation(a)
        value_str = f"{a.value:.2f}" if a.value is not None else "0"
        st.caption(f"{a.name} — value {value_str} — {rec} — {a.reason}")

st.divider()
st.markdown("## League Scoring (real reference league)")
st.caption("Goals 4.5 · Assists 3 · PIM 0.5 · PPP 0.5 · SOG 0.5 · HIT 0.5 · BLK 0.75 "
           "(skaters) — Wins 4.5 · GA -1.5 · Saves 0.3 · Shutouts 4.5 (goalies). "
           "PIM/PPP/HIT/Wins/GA/Shutouts have no validated projection model in this engine yet "
           "(PROJECTION_NOT_AVAILABLE) — only Goals/Assists/SOG/Blocks/Saves are real projections "
           "here; the rest are honestly absent, never fabricated.")

st.divider()
st.markdown("## What Should I Do Today?")
top_value_starters = sorted(starters, key=lambda a: -(a.value or 0))[:3]
if top_value_starters:
    for a in top_value_starters:
        st.markdown(f"- **START** {a.name} ({a.assigned_slot}) — {a.reason}")
else:
    comp.render_empty_state("NO_QUALIFYING_OPPORTUNITIES", "No players with a real projection today.")

comp.render_provenance_panel()
