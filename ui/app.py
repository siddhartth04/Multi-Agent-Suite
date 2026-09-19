"""Agent Workspace -- the product face of the multi-agent application.

A chat interface over the four agent modules. Deliberately free of telemetry:
no spans, no token counts, no trace ids. Those remain available on the services'
own HTTP endpoints for engineers and for the external testing platform; this
screen is for showing what the agents actually produce.

    streamlit run ui/app.py
"""

from __future__ import annotations

import os
import re
import sys
import time
from pathlib import Path
from typing import Any

import streamlit as st
import streamlit.components.v1 as components

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ui.api import DEFAULT_GATEWAY, MODULE_PORTS, SutClient  # noqa: E402

st.set_page_config(
    page_title="Agent Workspace",
    page_icon="◆",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ---------------------------------------------------------------- catalogue
# What each module is, in the user's language. The agent ids are internal, so
# the workspace shows the human role names instead.
ASSISTANTS: dict[str, dict[str, Any]] = {
    "research": {
        "name": "Research",
        "icon": "🔍",
        "tagline": "Researches a topic and reviews the evidence",
        "placeholder": "What would you like researched?",
        "team": ["Researcher", "Analyst", "Reviewer"],
        "examples": [
            "The current state of solid-state battery technology",
            "How remote work has changed office real estate",
            "Recent progress in desalination costs",
        ],
    },
    "fact_checker": {
        "name": "Fact Check",
        "icon": "🔎",
        "tagline": "Checks a claim against the evidence",
        "placeholder": "What claim should be checked?",
        "team": ["Fact Researcher", "Verification Specialist"],
        "examples": [
            "Solid-state batteries are in mass production today.",
            "Electric cars produce more lifetime emissions than petrol cars.",
            "Reading in dim light damages your eyesight.",
        ],
    },
    "marketing": {
        "name": "Marketing",
        "icon": "📣",
        "tagline": "Builds a campaign strategy and the copy to go with it",
        "placeholder": "What product should we market?",
        "team": ["Market Researcher", "Strategist", "Copywriter"],
        "examples": [
            "A budget e-bike for city commuters",
            "A meal-kit subscription for busy families",
            "A note-taking app for university students",
        ],
    },
    "travel": {
        "name": "Travel",
        "icon": "✈",
        "tagline": "Plans a trip and prepares a booking checklist",
        "placeholder": "Where would you like to go?",
        "team": ["Planner", "Search Specialist", "Booking Advisor"],
        "examples": [
            "5 days in Lisbon in May, two adults, mid-range budget",
            "A long weekend in Kyoto in autumn",
            "Two weeks around northern Italy by train",
        ],
    },
}

# ------------------------------------------------------------------- styling
# Every surface sets its own text colour explicitly. Streamlit inherits its
# palette from the viewer's OS theme, so a white background without a matching
# ink colour renders white-on-white for anyone in dark mode.
st.markdown(
    """
<style>
  :root {
    --brand:        #2563eb;
    --brand-dark:   #1d4ed8;
    --brand-soft:   #dbeafe;
    --brand-tint:   #eff6ff;
    --ink:          #0f172a;
    --ink-soft:     #334155;
    --ink-muted:    #64748b;
    --line:         #e2e8f0;
    --line-soft:    #f1f5f9;
    --surface:      #ffffff;
  }

  /* ---- base: force the light palette, whatever the OS is set to ---- */
  .stApp, [data-testid="stAppViewContainer"], [data-testid="stMain"] {
      background: var(--surface); color: var(--ink);
  }
  /* A faint dot grid keeps the canvas from reading as a blank sheet. */
  [data-testid="stMain"] {
      background-image: radial-gradient(rgba(37,99,235,.055) 1px, transparent 1px);
      background-size: 22px 22px;
  }
  /* The conversation column sits on clean paper above that texture. */
  [data-testid="stMain"] .block-container {
      background: var(--surface);
      border-left: 1px solid var(--line-soft);
      border-right: 1px solid var(--line-soft);
      box-shadow: 0 0 40px rgba(15,23,42,.04);
  }
  .stApp p, .stApp li, .stApp span, .stApp div, .stApp td, .stApp th,
  .stApp label, .stApp strong, .stApp em { color: var(--ink); }
  .stApp a { color: var(--brand); text-decoration: none; }
  .stApp a:hover { text-decoration: underline; }

  .block-container { padding-top: 1.4rem; padding-bottom: 7rem; max-width: 900px; }

  h1, h2, h3, h4, h5, h6 { color: var(--ink) !important; letter-spacing: -.018em; }

  /* ---- sidebar ---- */
  section[data-testid="stSidebar"] {
      background: var(--brand-tint); border-right: 1px solid var(--line);
  }
  section[data-testid="stSidebar"] * { color: var(--ink); }
  section[data-testid="stSidebar"] .block-container { padding-top: 1.7rem; }
  section[data-testid="stSidebar"] div[data-testid="stElementContainer"] { margin-bottom: -.55rem; }

  section[data-testid="stSidebar"] div[data-testid="stButton"] > button {
      width: 100%; text-align: left; justify-content: flex-start;
      background: transparent; border: 1px solid transparent;
      color: var(--ink-soft); font-weight: 500; font-size: 14.5px;
      padding: .6rem .85rem; border-radius: 10px;
      transition: background .15s ease, border-color .15s ease, color .15s ease;
  }
  section[data-testid="stSidebar"] div[data-testid="stButton"] > button:hover {
      background: var(--surface); border-color: var(--brand-soft); color: var(--brand);
  }
  section[data-testid="stSidebar"] div[data-testid="stButton"] > button[kind="primary"],
  section[data-testid="stSidebar"] div[data-testid="stButton"] > button[kind="primary"]:hover,
  section[data-testid="stSidebar"] button[data-testid="stBaseButton-primary"],
  section[data-testid="stSidebar"] button[data-testid="stBaseButton-primary"]:hover,
  section[data-testid="stSidebar"] button[data-testid="stBaseButton-primary"]:focus {
      background: var(--surface) !important; border-color: var(--brand) !important;
      color: var(--brand) !important; font-weight: 600 !important;
      box-shadow: 0 1px 3px rgba(37,99,235,.14) !important;
  }
  section[data-testid="stSidebar"] button[data-testid="stBaseButton-primary"] * {
      color: var(--brand) !important;
  }
  /* A left marker makes the active assistant unmistakable. */
  section[data-testid="stSidebar"] button[data-testid="stBaseButton-primary"] {
      border-left: 3px solid var(--brand) !important;
  }
  section[data-testid="stSidebar"] div[data-testid="stButton"] > button:focus:not(:active) {
      border-color: var(--brand); color: var(--brand);
  }
  /* "Clear conversation" is a secondary action, not another assistant. */
  section[data-testid="stSidebar"] div[data-testid="stButton"]:has(button[kind="secondary"]) button#clear,
  section[data-testid="stSidebar"] button[data-testid="stBaseButton-secondary"][aria-label*="Clear"] {
      color: var(--ink-muted);
  }

  /* ---- chat ---- */
  div[data-testid="stChatMessage"] {
      background: transparent; border: none; padding: .55rem 0 .9rem; gap: .8rem;
  }
  /* A hairline between exchanges gives the transcript structure. */
  div[data-testid="stChatMessage"]:has(+ div[data-testid="stChatMessage"] .user-bubble) {
      border-bottom: 1px solid var(--line-soft); margin-bottom: .6rem;
  }
  div[data-testid="stChatMessageAvatarUser"],
  div[data-testid="stChatMessageAvatarAssistant"] {
      background: var(--brand-tint) !important; border: 1px solid var(--brand-soft);
  }

  .user-bubble {
      background: var(--brand); color: #ffffff !important;
      padding: .68rem 1.05rem; border-radius: 16px 16px 4px 16px;
      display: inline-block; max-width: 88%;
      font-size: 15px; line-height: 1.55; font-weight: 450;
  }
  .user-bubble * { color: #ffffff !important; }

  /* The agents answer in markdown, so its elements need real styling. */
  div[data-testid="stChatMessage"] .stMarkdown { font-size: 15px; line-height: 1.7; }
  div[data-testid="stChatMessage"] .stMarkdown h1,
  div[data-testid="stChatMessage"] .stMarkdown h2 { font-size: 1.22rem; margin: 1.3rem 0 .6rem; }
  div[data-testid="stChatMessage"] .stMarkdown h3,
  div[data-testid="stChatMessage"] .stMarkdown h4 { font-size: 1.05rem; margin: 1.1rem 0 .45rem; }
  div[data-testid="stChatMessage"] .stMarkdown p  { margin: 0 0 .7rem; color: var(--ink-soft); }
  div[data-testid="stChatMessage"] .stMarkdown li { color: var(--ink-soft); margin-bottom: .25rem; }
  div[data-testid="stChatMessage"] .stMarkdown strong { color: var(--ink); font-weight: 650; }
  div[data-testid="stChatMessage"] .stMarkdown hr { margin: 1.3rem 0; border-color: var(--line); }

  div[data-testid="stChatMessage"] .stMarkdown table {
      border-collapse: collapse; width: 100%; margin: .6rem 0 1rem;
      font-size: 13.5px; border: 1px solid var(--line); border-radius: 8px;
      overflow: hidden;
  }
  div[data-testid="stChatMessage"] .stMarkdown th {
      background: var(--brand-tint); color: var(--ink) !important;
      font-weight: 600; text-align: left; padding: .55rem .75rem;
      border-bottom: 1px solid var(--line);
  }
  div[data-testid="stChatMessage"] .stMarkdown td {
      padding: .55rem .75rem; border-top: 1px solid var(--line-soft);
      color: var(--ink-soft); vertical-align: top;
  }
  div[data-testid="stChatMessage"] .stMarkdown code {
      background: var(--line-soft); color: var(--ink); padding: .1rem .35rem;
      border-radius: 4px; font-size: 13px;
  }
  div[data-testid="stChatMessage"] .stMarkdown blockquote {
      border-left: 3px solid var(--brand-soft); padding-left: .9rem;
      margin: .8rem 0; color: var(--ink-muted);
  }

  /* ---- page header: anchors the main column ---- */
  .page-head {
      display: flex; align-items: center; justify-content: space-between;
      gap: 1rem; padding: 0 0 .85rem; margin-bottom: 1.1rem;
      border-bottom: 1px solid var(--line);
  }
  .page-head-left { display: flex; align-items: center; gap: .7rem; }
  .page-icon {
      font-size: 15px; width: 34px; height: 34px; border-radius: 10px;
      display: inline-flex; align-items: center; justify-content: center;
      background: var(--brand-tint); border: 1px solid var(--brand-soft);
  }
  .page-title { display: block; font-size: 15.5px; font-weight: 680; color: var(--ink); }
  .page-sub   { display: block; font-size: 12px; color: var(--ink-muted) !important; margin-top: 1px; }
  .page-head-right { display: flex; gap: .3rem; flex-wrap: wrap; justify-content: flex-end; }

  .hint {
      margin-top: 1.6rem; padding: .7rem .9rem; border-radius: 10px;
      background: var(--brand-tint); border: 1px solid var(--brand-soft);
      font-size: 12.5px; color: var(--ink-muted) !important; text-align: center;
  }
  .hint b { color: var(--brand) !important; font-weight: 600; }

  /* ---- welcome panel ---- */
  .hero { padding: 2.2rem 0 .4rem; text-align: center; }
  .hero-icon {
      font-size: 1.6rem; width: 62px; height: 62px; line-height: 62px;
      margin: 0 auto .9rem; border-radius: 18px;
      background: var(--brand-tint); border: 1px solid var(--brand-soft);
  }
  .hero h2 { margin: 0 0 .35rem; font-size: 1.7rem; font-weight: 680; }
  .hero p  { color: var(--ink-muted) !important; font-size: 14.5px; margin: 0; }

  .team-line { margin-top: 1.5rem; }
  .team-chip {
      display: inline-block; background: var(--surface); color: var(--brand) !important;
      border: 1px solid var(--brand-soft); border-radius: 999px;
      padding: 3px 12px; margin: 0 3px; font-size: 12px; font-weight: 550;
  }

  .eyebrow {
      color: var(--ink-muted) !important; font-size: 12px; font-weight: 600;
      letter-spacing: .05em; text-transform: uppercase; margin: 1.8rem 0 .7rem;
  }

  /* Example prompts read as suggestions, not primary actions. */
  div[data-testid="stMain"] div[data-testid="stButton"] > button {
      background: var(--surface); border: 1px solid var(--line);
      color: var(--ink-soft); font-weight: 450; font-size: 14px;
      padding: .6rem 1rem; border-radius: 10px; text-align: left;
      justify-content: flex-start; transition: all .15s ease;
  }
  div[data-testid="stMain"] div[data-testid="stButton"] > button:hover {
      border-color: var(--brand); color: var(--brand); background: var(--brand-tint);
  }

  .step { color: var(--ink-muted) !important; font-size: 14px; padding: 3px 0; }

  /* ---- chat input ---- */
  div[data-testid="stChatInput"] {
      background: var(--surface); border: 1px solid var(--line);
      border-radius: 14px; box-shadow: 0 2px 12px rgba(15,23,42,.06);
  }
  div[data-testid="stChatInput"]:focus-within { border-color: var(--brand); }
  div[data-testid="stChatInput"] textarea { font-size: 15px; color: var(--ink) !important; }
  div[data-testid="stChatInput"] textarea::placeholder { color: var(--ink-muted) !important; }
  [data-testid="stBottomBlockContainer"] { background: var(--surface); }

  /* ---- misc ---- */
  .side-title { font-size: 11.5px; font-weight: 700; color: var(--ink-muted) !important;
                letter-spacing: .07em; text-transform: uppercase; margin: 0 0 .6rem 2px; }
  .side-foot  { color: var(--ink-muted) !important; font-size: 11.5px; line-height: 1.6; }

  /* ---- sidebar furniture ---- */
  .brand { display: flex; align-items: center; gap: .65rem; margin-bottom: .2rem; }
  .brand-mark {
      width: 38px; height: 38px; border-radius: 11px; flex: 0 0 38px;
      background: linear-gradient(135deg, var(--brand) 0%, #3b82f6 100%);
      display: flex; align-items: center; justify-content: center;
      box-shadow: 0 2px 8px rgba(37,99,235,.28);
  }
  .brand-name { font-size: 15.5px; font-weight: 700; letter-spacing: -.01em; color: var(--ink); }
  .brand-sub  { font-size: 11.5px; color: var(--ink-muted) !important; margin-top: 1px; }

  .rule { height: 1px; background: var(--brand-soft); margin: 1.15rem 0 .95rem; }

  .member {
      display: flex; align-items: center; gap: .5rem;
      font-size: 12.5px; color: var(--ink-soft) !important;
      padding: .22rem 0 .22rem 3px;
  }
  .member-dot {
      width: 5px; height: 5px; border-radius: 50%;
      background: var(--brand); opacity: .55; flex: 0 0 5px;
  }

  .side-badge {
      display: flex; align-items: center; gap: .45rem; margin-top: 1.3rem;
      font-size: 11px; color: var(--ink-muted) !important;
  }
  .dot-live {
      width: 6px; height: 6px; border-radius: 50%; background: #10b981;
      box-shadow: 0 0 0 3px rgba(16,185,129,.16);
  }
  [data-testid="stCaptionContainer"], [data-testid="stCaptionContainer"] * {
      color: var(--ink-muted) !important; font-size: 12.5px;
  }
  /* Hide Streamlit's chrome, but keep the header itself: it hosts the control
     that reopens a collapsed sidebar, and hiding it strands the user with no
     way back. Only its menu and status widget are removed. */
  #MainMenu, footer, [data-testid="stStatusWidget"],
  [data-testid="stToolbarActions"], [data-testid="stDecoration"] {
      display: none !important;
  }
  header[data-testid="stHeader"] {
      background: transparent; height: 2.6rem; pointer-events: none;
  }
  header[data-testid="stHeader"] * { pointer-events: auto; }

  /* Streamlit only reveals the collapse control on hover, and an earlier
     visibility rule suppressed it entirely. Keep both controls permanently
     visible: a sidebar you cannot reopen is a dead end. */
  [data-testid="stSidebarCollapseButton"],
  [data-testid="stSidebarCollapseButton"] button,
  [data-testid="stSidebarCollapsedControl"],
  [data-testid="stSidebarCollapsedControl"] button,
  [data-testid="stExpandSidebarButton"],
  [data-testid="stExpandSidebarButton"] button {
      visibility: visible !important; opacity: 1 !important; display: inline-flex !important;
  }

  /* Make the reopen control obvious rather than a faint ghost arrow. */
  [data-testid="stSidebarCollapsedControl"] button,
  [data-testid="stSidebarCollapseButton"] button,
  [data-testid="stExpandSidebarButton"] button,
  [data-testid="collapsedControl"] button {
      background: var(--surface) !important; color: var(--brand) !important;
      border: 1px solid var(--brand-soft) !important; border-radius: 9px !important;
      box-shadow: 0 1px 4px rgba(15,23,42,.10) !important;
  }
  [data-testid="stSidebarCollapsedControl"] button:hover,
  [data-testid="stExpandSidebarButton"] button:hover,
  [data-testid="stSidebarCollapseButton"] button:hover {
      border-color: var(--brand) !important; background: var(--brand-tint) !important;
  }
  [data-testid="stSidebarCollapsedControl"] {
      top: .7rem !important; left: .7rem !important; z-index: 999 !important;
  }
  [data-testid="stSidebarCollapsedControl"] button {
      width: 34px !important; height: 34px !important;
  }
  [data-testid="stSidebarCollapsedControl"] svg,
  [data-testid="stExpandSidebarButton"] svg,
  [data-testid="stSidebarCollapseButton"] svg { fill: var(--brand) !important; }
  [data-testid="stExpandSidebarButton"] button {
      width: 34px !important; height: 34px !important;
  }
</style>
""",
    unsafe_allow_html=True,
)


# Streamlit renders the "reopen sidebar" control inconsistently across versions
# and only on hover, so a collapsed sidebar can become a dead end. This observer
# keeps whichever control exists permanently visible, and adds our own if none
# is present. It is a UI guarantee, not a style tweak.
components.html(
    """
<script>
(function () {
  const doc = window.parent.document;
  const SELECTORS = [
    '[data-testid="stExpandSidebarButton"]',
    '[data-testid="stSidebarCollapsedControl"]',
    '[data-testid="collapsedControl"]',
  ];

  function nativeControl() {
    for (const sel of SELECTORS) {
      const el = doc.querySelector(sel);
      if (el) return el;
    }
    return null;
  }

  function ensure() {
    const sidebar = doc.querySelector('section[data-testid="stSidebar"]');
    const collapsed = !sidebar || sidebar.getBoundingClientRect().width < 60;

    // Keep whatever native control exists permanently visible.
    const native = nativeControl();
    if (native) {
      native.style.visibility = 'visible';
      native.style.opacity = '1';
      native.style.zIndex = '999';
    }

    let fallback = doc.getElementById('aw-reopen');
    if (!fallback) {
      fallback = doc.createElement('button');
      fallback.id = 'aw-reopen';
      fallback.type = 'button';
      fallback.title = 'Show assistants';
      fallback.setAttribute('aria-label', 'Show assistants');
      fallback.textContent = '≡';
      fallback.style.cssText = [
        'position:fixed', 'top:12px', 'left:12px', 'z-index:100000',
        'width:34px', 'height:34px', 'border-radius:9px', 'cursor:pointer',
        'background:#ffffff', 'color:#2563eb', 'border:1px solid #dbeafe',
        'box-shadow:0 1px 4px rgba(15,23,42,.10)', 'font-size:16px',
        'line-height:1', 'font-weight:700', 'display:none',
      ].join(';');
      fallback.onclick = function () {
        const el = nativeControl();
        if (el) { (el.querySelector('button') || el).click(); return; }
        const sb = doc.querySelector('section[data-testid="stSidebar"]');
        if (sb) {
          sb.style.width = '244px';
          sb.style.minWidth = '244px';
          sb.style.transform = 'none';
          sb.style.visibility = 'visible';
        }
      };
      doc.body.appendChild(fallback);
    }
    // Show it whenever the sidebar is collapsed, regardless of what Streamlit
    // renders -- that is the guarantee.
    fallback.style.display = collapsed ? 'block' : 'none';
  }

  ensure();
  new MutationObserver(ensure).observe(doc.body, {childList: true, subtree: true});
  setInterval(ensure, 600);
})();
</script>
""",
    height=0,
)


# ------------------------------------------------------------------ helpers
@st.cache_resource
def get_client(gateway: str) -> SutClient:
    return SutClient(gateway)


client = get_client(os.getenv("GATEWAY_URL", DEFAULT_GATEWAY))

if "assistant" not in st.session_state:
    st.session_state.assistant = "research"
if "threads" not in st.session_state:
    # One conversation per assistant, so switching never loses a demo.
    st.session_state.threads = {key: [] for key in ASSISTANTS}


_BR = re.compile(r"<br\s*/?>", re.IGNORECASE)
# A header row and its |---| separator that the model ran onto one line. Without
# the break, the whole table renders as literal pipes.
_RUN_ON_TABLE = re.compile(r"(\|[^\n|]*\|)\s*(\|[\s:\-]+\|)")


def clean(text: str) -> str:
    """Tidy model output so it renders as the markdown it is meant to be.

    Models emit literal <br> inside table cells, and occasionally run a table's
    header and separator together on one line, which stops the table parsing.
    """
    text = _BR.sub(" ", text)
    text = _RUN_ON_TABLE.sub(r"\1\n\2", text)
    return text


def thread() -> list[dict[str, Any]]:
    return st.session_state.threads[st.session_state.assistant]


def select(assistant_id: str) -> None:
    st.session_state.assistant = assistant_id


def clear_thread() -> None:
    st.session_state.threads[st.session_state.assistant] = []


def avatar(icon: str) -> str:
    """A chat avatar Streamlit will accept.

    Streamlit resolves a non-emoji glyph as a file path and raises, which takes
    the whole page down, so anything unusual falls back to a neutral mark.
    """
    return icon if icon and max(map(ord, icon)) > 0x1F000 else "🤖"


# ------------------------------------------------------------------ sidebar
with st.sidebar:
    # Brand mark: a drawn logo rather than a glyph, so the rail has an anchor.
    st.markdown(
        """
<div class="brand">
  <div class="brand-mark">
    <svg width="22" height="22" viewBox="0 0 24 24" fill="none">
      <circle cx="12" cy="5.2" r="2.6" fill="#ffffff"/>
      <circle cx="5.4" cy="16.4" r="2.6" fill="#ffffff" opacity=".85"/>
      <circle cx="18.6" cy="16.4" r="2.6" fill="#ffffff" opacity=".85"/>
      <path d="M12 7.8 6.6 14.2M12 7.8l5.4 6.4M7.9 16.4h8.2"
            stroke="#ffffff" stroke-width="1.5" stroke-linecap="round" opacity=".75"/>
    </svg>
  </div>
  <div>
    <div class="brand-name">Agent Workspace</div>
    <div class="brand-sub">Specialist teams, on demand</div>
  </div>
</div>
""",
        unsafe_allow_html=True,
    )

    st.markdown('<div class="rule"></div>', unsafe_allow_html=True)
    st.markdown('<div class="side-title">Assistants</div>', unsafe_allow_html=True)

    for key, item in ASSISTANTS.items():
        active = st.session_state.assistant == key
        count = len([m for m in st.session_state.threads[key] if m["role"] == "user"])
        st.button(
            f"{item['icon']}  {item['name']}" + (f"   ·  {count}" if count else ""),
            key=f"pick_{key}",
            type="primary" if active else "secondary",
            on_click=select,
            args=(key,),
            help=item["tagline"],
        )

    # The team behind the current assistant, so the rail always says something
    # about what is selected.
    current = ASSISTANTS[st.session_state.assistant]
    st.markdown(
        '<div class="rule"></div>'
        '<div class="side-title">This team</div>'
        + "".join(
            f'<div class="member"><span class="member-dot"></span>{name}</div>'
            for name in current["team"]
        ),
        unsafe_allow_html=True,
    )

    st.markdown('<div class="rule"></div>', unsafe_allow_html=True)
    if thread():
        st.button("Clear conversation", key="clear", on_click=clear_thread)
    else:
        st.markdown(
            '<div class="side-foot">Pick an assistant and describe what you need. '
            "Each one is a small team that works through your request in sequence.</div>",
            unsafe_allow_html=True,
        )

    st.markdown(
        '<div class="side-badge">'
        '<span class="dot-live"></span>All assistants available</div>',
        unsafe_allow_html=True,
    )

# --------------------------------------------------------------------- chat
spec = ASSISTANTS[st.session_state.assistant]
messages = thread()

# A persistent header, so the page is anchored even with an empty conversation.
st.markdown(
    f"""
<div class="page-head">
  <div class="page-head-left">
    <span class="page-icon">{spec["icon"]}</span>
    <span>
      <span class="page-title">{spec["name"]}</span>
      <span class="page-sub">{spec["tagline"]}</span>
    </span>
  </div>
  <div class="page-head-right">
    {"".join(f'<span class="team-chip">{m}</span>' for m in spec["team"])}
  </div>
</div>
""",
    unsafe_allow_html=True,
)

# An example click is queued for this run, so the welcome panel is already
# stale -- skip it rather than drawing it above the first exchange.
starting = bool(messages) or "pending" in st.session_state

if not starting:
    st.markdown(
        f'<div class="hero">'
        f'<div class="hero-icon">{spec["icon"]}</div>'
        f'<h2>How can the {spec["name"].lower()} team help?</h2>'
        f'<p>{spec["tagline"]}. Describe what you need, or start from an example.</p>'
        "</div>",
        unsafe_allow_html=True,
    )

    st.markdown('<div class="eyebrow">Suggested starting points</div>', unsafe_allow_html=True)
    for i, example in enumerate(spec["examples"]):
        if st.button(example, key=f"eg_{st.session_state.assistant}_{i}"):
            st.session_state.pending = example
            st.rerun()

    st.markdown(
        '<div class="hint">Your request passes through '
        + " → ".join(f"<b>{m}</b>" for m in spec["team"])
        + ", each building on the last.</div>",
        unsafe_allow_html=True,
    )


for message in messages:
    if message["role"] == "user":
        with st.chat_message("user", avatar="🧑"):
            st.markdown(
                f'<div class="user-bubble">{message["content"]}</div>',
                unsafe_allow_html=True,
            )
    else:
        with st.chat_message("assistant", avatar=avatar(spec["icon"])):
            if message.get("failed"):
                st.warning(message["content"])
            else:
                st.markdown(clean(message["content"]))
                if message.get("seconds"):
                    st.caption(f"{spec['name']} team · {message['seconds']:.0f}s")

prompt = st.chat_input(spec["placeholder"])
if "pending" in st.session_state:
    prompt = st.session_state.pop("pending")

if prompt:
    messages.append({"role": "user", "content": prompt})

    with st.chat_message("user", avatar="🧑"):
        st.markdown(f'<div class="user-bubble">{prompt}</div>', unsafe_allow_html=True)

    with st.chat_message("assistant", avatar=avatar(spec["icon"])):
        progress = st.empty()
        started = time.time()

        # The specialists run server-side in one call, so their names are shown
        # in sequence to make the teamwork visible while the request is in flight.
        with progress.container():
            for member in spec["team"]:
                st.markdown(
                    f'<div class="step">◌ {member} is working…</div>', unsafe_allow_html=True
                )

        result = client.run_module(
            st.session_state.assistant, prompt, use_dependencies=True, timeout=300.0
        )
        elapsed = time.time() - started
        progress.empty()

        body = result.data or {}
        answer = body.get("result") if isinstance(body, dict) else None

        if answer:
            st.markdown(clean(answer))
            st.caption(f"{spec['name']} team · {elapsed:.0f}s")
            messages.append(
                {"role": "assistant", "content": answer, "seconds": elapsed}
            )
        else:
            # Never show a stack trace or a status code to a demo audience.
            note = (
                f"The {spec['name']} team could not complete that request. "
                "Please try again in a moment."
            )
            st.warning(note)
            messages.append({"role": "assistant", "content": note, "failed": True})
