"""
Visual layer: layered slate surfaces (not black), one restrained blue accent, consistent cards, metrics, buttons, badges and a clear active
navigation state; and Eggy, the product's mascot, placed in the sidebar, the page header and empty states.

Everything is CSS over Streamlit's own components (no layout is replaced), so every page and the personal-log views inherit it. Colours are
tokens below; badge/banner tones live in dashboard/ui.py (TONES) and use the same family.
"""
from __future__ import annotations

from pathlib import Path

import streamlit as st

ASSETS = Path(__file__).resolve().parent / "assets" / "eggy"
EGGY_ORIGINAL = ASSETS / "eggy_original.webp"
EGGY_400 = ASSETS / "eggy_400.webp"
EGGY_200 = ASSETS / "eggy_200.webp"
EGGY_ICON = ASSETS / "eggy_icon_128.png"

TOKENS = {"bg": "#151a23", "surface": "#1c2330", "raised": "#242d3d", "border": "#2c3648", "border_strong": "#3a4660", "text": "#eef2f8", "text2": "#c3cddd", "muted": "#9fadc2",
          "accent": "#4f8cff", "accent_soft": "rgba(79,140,255,.16)", "good": "#34c38f", "bad": "#f06a6a", "warn": "#e8b84a", "sidebar": "#111620"}

CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap');
:root { --bg:@@bg@@; --surface:@@surface@@; --raised:@@raised@@; --border:@@border@@; --border-strong:@@border_strong@@; --text:@@text@@; --text2:@@text2@@; --muted:@@muted@@;
        --accent:@@accent@@; --accent-soft:@@accent_soft@@; --good:@@good@@; --bad:@@bad@@; --warn:@@warn@@; --radius:12px; }
html, body, .stApp, [data-testid="stAppViewContainer"], button, input, textarea, select { font-family: 'Inter', -apple-system, 'Segoe UI', Roboto, 'Source Sans Pro', sans-serif; }
.stApp { background: var(--bg); color: var(--text); font-feature-settings: 'tnum' 1, 'cv11' 1; }
.block-container { padding-top: 4rem; padding-bottom: 3rem; max-width: 1240px; }   /* clears Streamlit's fixed top bar */

/* ---- type scale: 30 / 21 / 17 / 15 body / 13.5 secondary / 12 labels */
h1 { font-size: 1.9rem !important; font-weight: 800; letter-spacing: -0.025em; line-height: 1.15; margin: 0 0 .25rem 0 !important; padding: 0 !important; }
h2 { font-size: 1.3rem !important; font-weight: 700; letter-spacing: -0.015em; margin: 1.5rem 0 .5rem 0 !important; padding: 0 !important; }
h3 { font-size: 1.08rem !important; font-weight: 650; letter-spacing: -0.01em; margin: 1.1rem 0 .4rem 0 !important; padding: 0 !important; }
h4 { font-size: .98rem !important; font-weight: 650; margin: .9rem 0 .3rem 0 !important; padding: 0 !important; }
p, li, [data-testid="stMarkdownContainer"] { font-size: .95rem; line-height: 1.5; }
[data-testid="stCaptionContainer"], [data-testid="stCaptionContainer"] p { color: var(--muted) !important; font-size: .84rem !important; line-height: 1.5 !important; }
[data-testid="stVerticalBlock"] { gap: .7rem; }
strong, b { font-weight: 650; color: var(--text); }
a { color: #8db4ff; }

/* ---- the Eggy mark sits in front of every page title */
[data-testid="stMain"] h1::before { content: ''; display: inline-block; width: 38px; height: 38px; margin: -4px 12px -6px 0; vertical-align: middle; border-radius: 10px;
    background: url('@@icon48@@') center/cover no-repeat; box-shadow: 0 0 0 1px var(--border-strong); }

/* ---- sidebar */
[data-testid="stSidebar"] { background: @@sidebar@@; border-right: 1px solid var(--border); }
[data-testid="stSidebar"] [data-testid="stSidebarHeader"] img { height: 44px; border-radius: 11px; box-shadow: 0 0 0 1px var(--border-strong); }
[data-testid="stSidebarNav"] a, [data-testid="stSidebar"] a[data-testid="stSidebarNavLink"] { border-radius: 9px; margin: 1px 8px; padding: 7px 12px; color: var(--text2); font-size: .92rem; font-weight: 500;
    transition: background .12s ease, color .12s ease; min-height: 38px; }
[data-testid="stSidebarNav"] a:hover, [data-testid="stSidebar"] a[data-testid="stSidebarNavLink"]:hover { background: var(--raised); color: #fff; }
[data-testid="stSidebarNav"] a[aria-current="page"], [data-testid="stSidebar"] a[data-testid="stSidebarNavLink"][aria-current="page"] {
    background: var(--accent-soft); color: #fff; font-weight: 650; box-shadow: inset 3px 0 0 var(--accent); }
[data-testid="stSidebarNavSeparator"], [data-testid="stSidebar"] [data-testid="stSidebarNavItems"] header { color: var(--muted); text-transform: uppercase; font-size: .7rem; letter-spacing: .08em; font-weight: 650; margin-top: 12px; }

/* ---- cards (bordered containers), metrics, tables */
[data-testid="stVerticalBlockBorderWrapper"] { background: var(--surface); border: 1px solid var(--border) !important; border-radius: var(--radius); box-shadow: 0 1px 0 rgba(255,255,255,.03) inset; }
[data-testid="stVerticalBlockBorderWrapper"] [data-testid="stVerticalBlockBorderWrapper"] { background: var(--raised); }
[data-testid="stMetric"] { background: var(--surface); border: 1px solid var(--border); border-radius: var(--radius); padding: 10px 14px; }
[data-testid="stMetricLabel"] p { color: var(--muted) !important; font-size: .7rem !important; text-transform: uppercase; letter-spacing: .06em; font-weight: 600; }
[data-testid="stMetricValue"] { font-variant-numeric: tabular-nums; font-weight: 700; }
[data-testid="stMetricValue"] > div { font-size: clamp(1.15rem, 1.6vw, 1.7rem); white-space: normal; overflow: visible; text-overflow: clip; line-height: 1.2; }
[data-testid="stMetricLabel"] *, [data-testid="stMetricDelta"] *, [data-testid="stMetricValue"] * { white-space: normal !important; overflow: visible !important; text-overflow: clip !important; overflow-wrap: anywhere; }
[data-testid="stMetricDelta"], [data-testid="stMetricDelta"] > div { white-space: normal; overflow: visible; text-overflow: clip; line-height: 1.25; font-size: .78rem; }
[data-testid="stVerticalBlockBorderWrapper"] [data-testid="stMetric"] { background: transparent; border: 0; padding: 4px 2px; }
[data-testid="stMarkdownContainer"] table { max-width: 100%; }
[data-testid="stMarkdownContainer"] td, [data-testid="stMarkdownContainer"] th { overflow-wrap: anywhere; word-break: normal; vertical-align: top; }
[data-testid="stDataFrame"] { border: 1px solid var(--border); border-radius: 10px; overflow: hidden; font-variant-numeric: tabular-nums; }
[data-testid="stExpander"] { border: 1px solid var(--border); border-radius: var(--radius); background: var(--surface); }
[data-testid="stExpander"] summary { font-weight: 600; font-size: .92rem; }

/* ---- bet slip: selections, prices, chance and stake read first */
.card-head { display: flex; justify-content: space-between; align-items: flex-start; gap: 10px; flex-wrap: wrap; margin-bottom: 8px; }
.card-title { font-weight: 700; font-size: 1.02rem; line-height: 1.35; min-width: 0; flex: 1 1 260px; }
.card-chips { flex: 0 0 auto; display: flex; gap: 2px; flex-wrap: wrap; }
.slip { display: flex; flex-direction: column; gap: 8px; margin: 2px 0 4px 0; }
.leg { display: flex; justify-content: space-between; align-items: center; gap: 14px; background: var(--bg); border: 1px solid var(--border); border-left: 3px solid var(--accent);
    border-radius: 10px; padding: 9px 14px; }
.leg.stale { border-left-color: var(--warn); background: rgba(232,184,74,.06); } .leg.stale .leg-sel, .leg.stale .odds { opacity: .6; } .leg.stale .leg-meta { color: var(--warn); font-weight: 600; }
.leg.won { border-left-color: var(--good); } .leg.lost { border-left-color: var(--bad); } .leg.void { border-left-color: var(--muted); }
.leg-main { min-width: 0; flex: 1 1 auto; }
.leg-sel { font-weight: 650; font-size: .98rem; color: var(--text); line-height: 1.3; }
.leg-meta { color: var(--muted); font-size: .8rem; margin-top: 1px; }
.leg-right { display: flex; align-items: center; gap: 10px; flex: 0 0 auto; flex-wrap: wrap; justify-content: flex-end; }
.odds { display: inline-block; min-width: 58px; text-align: center; font-weight: 750; font-size: 1.02rem; font-variant-numeric: tabular-nums; background: var(--raised); border: 1px solid var(--border-strong);
    border-radius: 9px; padding: 3px 10px; color: #fff; }
.chance { color: var(--text2); font-size: .8rem; white-space: nowrap; }
.edge { margin-left: 4px; font-weight: 650; } .edge.pos { color: var(--good); } .edge.neg { color: var(--bad); } .edge.flat { color: var(--muted); }
.res { font-size: .72rem; font-weight: 700; text-transform: uppercase; letter-spacing: .05em; padding: 2px 8px; border-radius: 999px; }
.res.won { color: var(--good); background: rgba(52,195,143,.14); } .res.lost { color: var(--bad); background: rgba(240,106,106,.14); } .res.void { color: var(--muted); background: var(--raised); }
.stats { display: grid; grid-template-columns: repeat(5, minmax(0, 1fr)); gap: 8px; margin-top: 2px; }
.stat { background: var(--bg); border: 1px solid var(--border); border-radius: 10px; padding: 8px 12px; display: flex; flex-direction: column; min-width: 0; }
.stat .k { color: var(--muted); font-size: .68rem; text-transform: uppercase; letter-spacing: .06em; font-weight: 600; }
.stat .v { font-size: 1.15rem; font-weight: 750; font-variant-numeric: tabular-nums; line-height: 1.25; color: var(--text); }
.stat .sub { color: var(--muted); font-size: .72rem; }
.stat.accent .v { color: #9fc0ff; } .stat.good .v { color: var(--good); } .stat.bad .v { color: var(--bad); } .stat.open .v { color: var(--text2); }

/* ---- controls: one hover, focus, disabled language */
.stButton > button, [data-testid="stLinkButton"] a { border-radius: 10px; border: 1px solid var(--border-strong); background: var(--raised); color: var(--text); font-weight: 600; font-size: .9rem;
    min-height: 40px; transition: border-color .12s ease, background .12s ease, transform .06s ease; }
.stButton > button:hover, [data-testid="stLinkButton"] a:hover { border-color: var(--accent); background: #2a3447; color: #fff; }
.stButton > button:active { transform: translateY(1px); }
.stButton > button[kind="primary"] { background: var(--accent); border-color: var(--accent); color: #fff; }
.stButton > button[kind="primary"]:hover { filter: brightness(1.08); background: var(--accent); }
.stButton > button:disabled { opacity: .45; cursor: not-allowed; }
button:focus-visible, a:focus-visible, [role="tab"]:focus-visible, input:focus-visible, textarea:focus-visible, [data-baseweb="select"]:focus-within {
    outline: 2px solid var(--accent) !important; outline-offset: 2px; }
[data-baseweb="input"], [data-baseweb="select"] > div, textarea { border-radius: 10px !important; background: var(--bg) !important; }
[data-baseweb="input"]:focus-within { border-color: var(--accent) !important; }
[data-testid="stTabs"] [role="tab"] { font-weight: 600; color: var(--muted); } [data-testid="stTabs"] [role="tab"][aria-selected="true"] { color: #fff; }
[data-testid="stAlert"] { border-radius: var(--radius); border: 1px solid var(--border); }
[data-testid="stStatusWidget"] { font-size: .8rem; color: var(--muted); }
[data-testid="stSpinner"] { color: var(--text2); }

/* ---- Eggy: brand block, empty states */
.eggy-brand { display:flex; align-items:center; gap:12px; margin: 2px 0 8px 0; padding: 10px 12px; background: var(--surface); border: 1px solid var(--border); border-radius: var(--radius); }
.eggy-brand img { width: 52px; height: 52px; border-radius: 13px; box-shadow: 0 0 0 1px var(--border-strong); flex: none; }
.eggy-brand .t { font-weight: 750; line-height: 1.15; font-size: 1rem; letter-spacing: -0.01em; }
.eggy-brand .s { color: var(--muted); font-size: .76rem; line-height: 1.3; margin-top: 2px; }
.eggy-empty { display:flex; align-items:center; gap:18px; background: var(--surface); border:1px dashed var(--border-strong); border-radius: 14px; padding: 14px 18px; margin: 4px 0 10px 0; }
.eggy-empty img { width: 76px; height: 76px; border-radius: 16px; flex: none; box-shadow: 0 0 0 1px var(--border-strong); }
.eggy-empty .m { color: var(--text); font-weight: 600; } .eggy-empty .h { color: var(--muted); font-size: .84rem; margin-top: 3px; }

/* ---- phones: two-up metrics instead of a tall stack, roomier tap targets, tighter margins */
@media (max-width: 760px) {
  [data-testid="stMarkdownContainer"] table { display: block; overflow-x: auto; }
  .block-container { padding: 3.6rem .9rem 2.5rem .9rem; }
  h1 { font-size: 1.55rem !important; } [data-testid="stMain"] h1::before { width: 32px; height: 32px; margin-right: 10px; }
  [data-testid="stHorizontalBlock"]:has([data-testid="stMetric"]) { flex-wrap: wrap !important; gap: .5rem !important; }
  [data-testid="stHorizontalBlock"]:has([data-testid="stMetric"]) > [data-testid="stColumn"] { min-width: calc(50% - .5rem) !important; flex: 1 1 calc(50% - .5rem) !important; width: auto !important; }
  [data-testid="stMetric"] { padding: 8px 10px; }
  [data-testid="stMetricValue"] > div { font-size: 1.25rem; }
  .leg { flex-direction: column; align-items: flex-start; gap: 6px; } .leg-right { justify-content: flex-start; }
  .stats { grid-template-columns: repeat(2, minmax(0, 1fr)); } .stat .v { font-size: 1.05rem; }
  .eggy-empty { flex-direction: column; text-align: center; } .eggy-empty img { width: 64px; height: 64px; }
  .stButton > button, [data-testid="stLinkButton"] a { min-height: 44px; }
  [data-testid="stSidebarNav"] a, [data-testid="stSidebar"] a[data-testid="stSidebarNavLink"] { min-height: 46px; font-size: 1rem; }
}
@media (prefers-reduced-motion: reduce) { * { transition: none !important; } }
</style>
"""
def inject() -> None:
    """Call once per run from the app entry point (the entry script reruns for every page)."""
    css = CSS
    for k, v in TOKENS.items():
        css = css.replace(f"@@{k}@@", v)
    css = css.replace("@@icon48@@", _icon48_uri())
    st.markdown(css, unsafe_allow_html=True)


def _data_uri(path: Path) -> str:
    import base64
    mime = "image/webp" if path.suffix == ".webp" else "image/png"
    return f"data:{mime};base64," + base64.b64encode(path.read_bytes()).decode()


@st.cache_data(show_spinner=False, ttl=86400, max_entries=8)
def _icon_uri() -> str:
    return _data_uri(EGGY_ICON)


@st.cache_data(show_spinner=False, ttl=86400, max_entries=8)
def _icon48_uri() -> str:
    return _data_uri(ASSETS / "eggy_icon_48.png")


def brand(title: str, subtitle: str) -> None:
    """Sidebar brand block: Eggy + product name."""
    st.markdown(f"<div class='eggy-brand'><img alt='Eggy, the mascot' src='{_icon_uri()}'/><div><div class='t'>{title}</div><div class='s'>{subtitle}</div></div></div>",
                unsafe_allow_html=True)


def empty_state(message: str, hint: str | None = None) -> None:
    """A friendly empty state: Eggy plus what is missing and why."""
    h = f"<div class='h'>{hint}</div>" if hint else ""
    st.markdown(f"<div class='eggy-empty'><img alt='Eggy' src='{_icon_uri()}'/><div><div class='m'>{message}</div>{h}</div></div>", unsafe_allow_html=True)
