"""
The dashboard's page registry -- single source of truth for navigation,
page classification, and per-runtime-mode availability (Community Cloud
memory sprint, 2026-09-25). dashboard/app.py builds st.navigation from
this; nothing else hardcodes the page list.

Classes (memory-oriented; measured RSS deltas are in
docs/STREAMLIT_MEMORY_AUDIT.md):
  CORE_USER          what a friend needs: Today, games, recommendations,
                     odds/edge/confidence, Game Detail, performance.
  LIGHTWEIGHT_ADMIN  small status/operations views (Morning Review, Data
                     Status, Diagnostics).
  DEMO               simulated-market boards. In COMMUNITY_CLOUD_MODE they
                     read the compact snapshot, never the live model stack.
  HEAVY_RESEARCH     loads research corpora / fits or replays models.
                     NOT registered in COMMUNITY_CLOUD_MODE.
  LEGACY             superseded/status-only research surfaces. NOT
                     registered in COMMUNITY_CLOUD_MODE.

`cloud=False` means: not registered (so never executed, never imported)
in COMMUNITY_CLOUD_MODE. Nothing is deleted from the repository; LOCAL_MODE
and PRODUCTION_MODE register every page exactly as before.
"""
from __future__ import annotations

from dataclasses import dataclass

from operational import runtime_mode

CORE_USER = "CORE_USER"
LIGHTWEIGHT_ADMIN = "LIGHTWEIGHT_ADMIN"
DEMO = "DEMO"
HEAVY_RESEARCH = "HEAVY_RESEARCH"
LEGACY = "LEGACY"


@dataclass(frozen=True)
class PageSpec:
    file: str
    title: str
    icon: str
    section: str
    klass: str
    cloud: bool
    default: bool = False
    admin_only: bool = False
    # ADMIN-only in COMMUNITY_CLOUD_MODE only (Cloud live-data sprint): Morning Review and the
    # Data Status / health detail are operational surfaces. LOCAL/PRODUCTION visibility is unchanged.
    cloud_admin_only: bool = False


PAGES: tuple[PageSpec, ...] = (
    # ---- Operate
    PageSpec("21_Today.py", "Today", "☀️", "Operate", CORE_USER, True, default=True),
    PageSpec("8_Live_SOG_Markets.py", "Live SOG Markets", "📡", "Operate", HEAVY_RESEARCH, False),
    PageSpec("26_Player_Props.py", "Player Props", "🎫", "Operate", DEMO, True),
    PageSpec("27_Goalies.py", "Goalies", "🛡️", "Operate", DEMO, True),
    PageSpec("28_Combinations.py", "Combinations", "🧮", "Operate", DEMO, True),
    PageSpec("1_Game_Slate.py", "Games", "🗓️", "Operate", CORE_USER, True),
    PageSpec("2_Game_Detail.py", "Game Detail", "🔍", "Operate", CORE_USER, True),
    # ---- Track & Monitor
    PageSpec("29_Market_Movement.py", "Market Movement", "📉", "Track & Monitor", DEMO, True),
    PageSpec("30_Players.py", "Players", "🧑‍🤝‍🧑", "Track & Monitor", CORE_USER, True),
    PageSpec("31_Team_Intelligence.py", "Team Intelligence", "🏟️", "Track & Monitor", CORE_USER, True),
    PageSpec("25_Player_Intelligence.py", "Player Intelligence", "🧠", "Track & Monitor", CORE_USER, True),
    PageSpec("22_Model_Health.py", "Model Health", "🩺", "Track & Monitor", CORE_USER, True),
    PageSpec("32_Model_Learning.py", "Model Learning", "🔄", "Track & Monitor", CORE_USER, True),
    PageSpec("33_Paper_Performance.py", "Paper Performance", "💰", "Track & Monitor", CORE_USER, True),
    PageSpec("36_Morning_Review.py", "Morning Review", "🧭", "Track & Monitor", LIGHTWEIGHT_ADMIN, True, cloud_admin_only=True),
    PageSpec("23_Ledger.py", "Ledger", "📒", "Track & Monitor", CORE_USER, True),
    PageSpec("9_Data_Status.py", "Data Status", "🗂️", "Track & Monitor", LIGHTWEIGHT_ADMIN, True, cloud_admin_only=True),
    PageSpec("13_Play_By_Play_Status.py", "Play-by-Play Status", "🧩", "Track & Monitor", LEGACY, False),
    PageSpec("3_Team_Ratings.py", "Team Ratings", "📊", "Track & Monitor", HEAVY_RESEARCH, False),
    # ---- Research
    PageSpec("24_Research_Hub.py", "Research Hub", "🧪", "Research", LEGACY, False),
    PageSpec("10_Prop_Registry.py", "Prop Registry", "📋", "Research", LEGACY, False),
    PageSpec("7_Player_SOG_Research.py", "Player SOG Research", "🎯", "Research", HEAVY_RESEARCH, False),
    PageSpec("14_Player_SOG_By_Period_Research.py", "Player SOG by Period", "⏱️", "Research", HEAVY_RESEARCH, False),
    PageSpec("12_Player_Goals_Research.py", "Player Goals Research", "🚨", "Research", HEAVY_RESEARCH, False),
    PageSpec("11_Player_Points_Research.py", "Player Points Research", "🏒", "Research", HEAVY_RESEARCH, False),
    PageSpec("17_Team_SOG_Research.py", "Team SOG Research", "📐", "Research", HEAVY_RESEARCH, False),
    PageSpec("16_Goalie_Saves_Research.py", "Goalie Saves Research", "🧤", "Research", HEAVY_RESEARCH, False),
    PageSpec("6_Goalie_Intelligence.py", "Goalie Intelligence", "🥅", "Research", HEAVY_RESEARCH, False),
    PageSpec("18_Joint_Shot_Workload_Research.py", "Joint Shot/Workload Research", "🔗", "Research", HEAVY_RESEARCH, False),
    PageSpec("19_Joint_Scoring_Dependence_Research.py", "Joint Scoring Dependence", "🎲", "Research", HEAVY_RESEARCH, False),
    PageSpec("15_Team_Goals_By_Period_Research.py", "Team Goals by Period", "🧊", "Research", HEAVY_RESEARCH, False),
    PageSpec("20_Player_Context_State_Research.py", "Player Context State", "🌡️", "Research", HEAVY_RESEARCH, False),
    PageSpec("4_Model_Performance.py", "Model Performance", "📈", "Research", HEAVY_RESEARCH, False),
    PageSpec("5_Research_Lab.py", "Research Lab", "🔬", "Research", HEAVY_RESEARCH, False),
    # ---- Fantasy (ADMIN only; Yahoo is unavailable on Community Cloud: no
    # durable encrypted-token store, and Fantasy HQ builds the model stack)
    PageSpec("34_Fantasy_HQ.py", "Fantasy HQ", "🏆", "Fantasy", DEMO, False, admin_only=True),
    PageSpec("35_Fantasy_Settings.py", "Fantasy Settings", "🔌", "Fantasy", LEGACY, False, admin_only=True),
    # ---- Diagnostics (ADMIN only, every mode)
    PageSpec("37_Diagnostics.py", "Diagnostics", "🩻", "Admin", LIGHTWEIGHT_ADMIN, True, admin_only=True),
)

_SECTION_ORDER = ("Operate", "Track & Monitor", "Research", "Fantasy", "Admin")


def page_available(spec: PageSpec, role: str, mode: str | None = None) -> bool:
    mode = mode or runtime_mode.current_mode()
    if spec.admin_only and role != "ADMIN":
        return False
    if mode == runtime_mode.COMMUNITY_CLOUD_MODE:
        if not spec.cloud:
            return False
        if spec.cloud_admin_only and role != "ADMIN":
            return False
    return True


def pages_for(role: str, mode: str | None = None) -> dict[str, list[PageSpec]]:
    """Ordered {section: [PageSpec, ...]} for st.navigation."""
    mode = mode or runtime_mode.current_mode()
    sections: dict[str, list[PageSpec]] = {}
    for section in _SECTION_ORDER:
        specs = [p for p in PAGES if p.section == section and page_available(p, role, mode)]
        if specs:
            sections[section] = specs
    return sections


def spec_for_file(filename: str) -> PageSpec | None:
    name = filename.rsplit("/", 1)[-1]
    return next((p for p in PAGES if p.file == name), None)


def is_registered(filename: str, role: str, mode: str | None = None) -> bool:
    spec = spec_for_file(filename)
    return spec is not None and page_available(spec, role, mode)
