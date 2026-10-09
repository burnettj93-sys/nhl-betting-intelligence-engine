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

TOKENS = {"bg": "#161b24", "surface": "#1e2531", "raised": "#252d3b", "border": "#2d3748", "text": "#e7ecf4", "muted": "#9aa6ba",
          "accent": "#4f8cff", "accent_soft": "rgba(79,140,255,.16)", "sidebar": "#121720"}

CSS = """
<style>
:root { --bg:%(bg)s; --surface:%(surface)s; --raised:%(raised)s; --border:%(border)s; --text:%(text)s; --muted:%(muted)s; --accent:%(accent)s; --accent-soft:%(accent_soft)s; }
.stApp { background: var(--bg); }
.block-container { padding-top: 2.2rem; padding-bottom: 4rem; max-width: 1280px; }
h1, h2, h3, h4 { letter-spacing: -0.01em; }
h1 { font-weight: 700; margin-bottom: .1rem; }
h2, h3 { font-weight: 650; margin-top: 1.4rem; }
[data-testid="stCaptionContainer"], .stCaption { color: var(--muted); }

/* sidebar */
[data-testid="stSidebar"] { background: %(sidebar)s; border-right: 1px solid var(--border); }
[data-testid="stSidebarNav"] a, [data-testid="stSidebar"] a[data-testid="stSidebarNavLink"] { border-radius: 8px; margin: 1px 6px; padding: 5px 10px; color: var(--muted); }
[data-testid="stSidebarNav"] a:hover, [data-testid="stSidebar"] a[data-testid="stSidebarNavLink"]:hover { background: var(--raised); color: var(--text); }
[data-testid="stSidebarNav"] a[aria-current="page"], [data-testid="stSidebar"] a[data-testid="stSidebarNavLink"][aria-current="page"] {
    background: var(--accent-soft); color: #ffffff; font-weight: 650; box-shadow: inset 3px 0 0 var(--accent); }
[data-testid="stSidebarNavSeparator"], [data-testid="stSidebar"] [data-testid="stSidebarNavItems"] header { color: var(--muted); text-transform: uppercase; font-size: .72rem; letter-spacing: .06em; }

/* cards: bordered containers */
[data-testid="stVerticalBlockBorderWrapper"] { background: var(--surface); border: 1px solid var(--border) !important; border-radius: 14px; }
[data-testid="stVerticalBlockBorderWrapper"] [data-testid="stVerticalBlockBorderWrapper"] { background: var(--raised); }

/* metrics */
[data-testid="stMetric"] { background: var(--surface); border: 1px solid var(--border); border-radius: 12px; padding: 10px 14px; }
[data-testid="stMetricLabel"] p { color: var(--muted); font-size: .74rem; text-transform: uppercase; letter-spacing: .05em; }
[data-testid="stMetricValue"] { font-variant-numeric: tabular-nums; font-weight: 650; }
[data-testid="stVerticalBlockBorderWrapper"] [data-testid="stMetric"] { background: transparent; border: 0; padding: 4px 2px; }

/* tables */
[data-testid="stDataFrame"] { border: 1px solid var(--border); border-radius: 10px; overflow: hidden; font-variant-numeric: tabular-nums; }

/* buttons */
.stButton > button, [data-testid="stLinkButton"] a { border-radius: 9px; border: 1px solid var(--border); background: var(--raised); color: var(--text); font-weight: 600; }
.stButton > button:hover, [data-testid="stLinkButton"] a:hover { border-color: var(--accent); color: #fff; }
.stButton > button[kind="primary"] { background: var(--accent); border-color: var(--accent); color: #fff; }
.stButton > button[kind="primary"]:hover { filter: brightness(1.1); }
.stButton > button:disabled { opacity: .45; }

/* inputs, tabs, expanders */
[data-baseweb="input"], [data-baseweb="select"] > div, textarea { border-radius: 9px !important; }
[data-testid="stExpander"] { border: 1px solid var(--border); border-radius: 12px; background: var(--surface); }
[data-testid="stTabs"] [role="tab"][aria-selected="true"] { color: #fff; }

/* Eggy */
.eggy-brand { display:flex; align-items:center; gap:10px; margin: 2px 0 6px 0; }
.eggy-brand img { width: 46px; height: 46px; border-radius: 12px; box-shadow: 0 0 0 1px var(--border); }
.eggy-brand .t { font-weight: 700; line-height: 1.15; }
.eggy-brand .s { color: var(--muted); font-size: .76rem; }
.eggy-empty { display:flex; align-items:center; gap:16px; background: var(--surface); border:1px dashed var(--border); border-radius: 14px; padding: 12px 16px; margin: 6px 0 12px 0; }
.eggy-empty img { width: 64px; height: 64px; border-radius: 14px; flex: none; }
.eggy-empty .m { color: var(--text); }
.eggy-empty .h { color: var(--muted); font-size: .84rem; margin-top: 2px; }
@media (max-width: 640px) { .block-container { padding-left: 1rem; padding-right: 1rem; } .eggy-empty { flex-direction: column; text-align: center; } }
</style>
""" % TOKENS


def inject() -> None:
    """Call once per run from the app entry point (the entry script reruns for every page)."""
    st.markdown(CSS, unsafe_allow_html=True)


def _data_uri(path: Path) -> str:
    import base64
    mime = "image/webp" if path.suffix == ".webp" else "image/png"
    return f"data:{mime};base64," + base64.b64encode(path.read_bytes()).decode()


@st.cache_data(show_spinner=False, ttl=86400, max_entries=8)
def _icon_uri() -> str:
    return _data_uri(EGGY_ICON)


def brand(title: str, subtitle: str) -> None:
    """Sidebar brand block: Eggy + product name."""
    st.markdown(f"<div class='eggy-brand'><img alt='Eggy, the mascot' src='{_icon_uri()}'/><div><div class='t'>{title}</div><div class='s'>{subtitle}</div></div></div>",
                unsafe_allow_html=True)


def empty_state(message: str, hint: str | None = None) -> None:
    """A friendly empty state: Eggy plus what is missing and why."""
    h = f"<div class='h'>{hint}</div>" if hint else ""
    st.markdown(f"<div class='eggy-empty'><img alt='Eggy' src='{_icon_uri()}'/><div><div class='m'>{message}</div>{h}</div></div>", unsafe_allow_html=True)
