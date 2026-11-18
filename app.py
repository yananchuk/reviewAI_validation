"""Review AI: Streamlit entry point. Run with `uv run streamlit run app.py`.

This file only routes between the three screens; all logic lives in review_app."""

import logging

import streamlit as st

from review_app import limits
from review_app.ui import completion, panel, upload, workbook_pane
from review_app.ui.state import get_state
from review_app.ui.validate import check, label, misses, results

st.set_page_config(page_title="Review AI", page_icon="📋", layout="wide")

if not logging.getLogger("review_app").handlers:
    # Metadata only (counts, ids, timings). Workbook contents and reviewer comments are never logged.
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    logging.getLogger("review_app").addHandler(handler)
    logging.getLogger("review_app").setLevel(logging.INFO)

# Less space above the page; the right panel is as tall as the window and scrolls on its own (like the workbook pane).
st.html(
    "<style>.block-container { padding-top: 1.5rem; padding-bottom: 0.5rem; }"
    f".st-key-rai-panel {{ height: {limits.PANE_HEIGHT_CSS}; min-height: {limits.PANE_MIN_HEIGHT_PX}px;"
    " overflow-y: auto; overflow-x: hidden; }</style>"
)

state = get_state()
if state.page == "upload":
    upload.render()
elif state.page == "complete":
    completion.render()
elif state.page == "v_check":
    check.render()
elif state.page == "v_results":
    results.render()
elif state.page in ("v_label", "v_misses"):
    left, right = st.columns([7, 3], gap="small")
    with left:
        workbook_pane.render(state, select_mode=state.page == "v_misses")
    with right, st.container(key="rai-panel", border=False):
        (label if state.page == "v_label" else misses).render()
else:
    left, right = st.columns([7, 3], gap="small")
    with left:
        workbook_pane.render(state)
    with right, st.container(key="rai-panel", border=False):
        panel.render(state)
