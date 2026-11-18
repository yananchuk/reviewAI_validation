"""Shared pieces of the validator pages: the step bar and navigation."""

from __future__ import annotations

import streamlit as st

from review_app.ui.state import esc, get_state, reset

STEPS: list[tuple[str, str]] = [
    ("v_label", "1 Label"),
    ("v_misses", "2 Missed"),
    ("v_check", "3 Check"),
    ("v_results", "4 Results"),
]


def go(page: str) -> None:
    get_state().page = page


def run_details() -> None:
    """Workbook and report run, as captions."""
    state = get_state()
    meta = state.report_meta
    st.caption(f"{esc(state.original_name)} · report run {esc(meta.run_id if meta else 'n/a')}")
    if meta:
        st.caption(f"Skill {esc(meta.skill_version)} · reviewed at {esc(meta.reviewed_at)}")


def step_bar(current: str) -> None:
    """The four steps in order (the current one bold, the others jump to their page), then the run menu."""
    cols = st.columns([*([1] * len(STEPS)), 1])
    for col, (page, text) in zip(cols, STEPS, strict=False):
        with col:
            if page == current:
                st.markdown(f"**{text}**")
            else:
                st.button(text, key=f"step:{page}", on_click=go, args=(page,), type="tertiary")
    with cols[-1].popover("⋯", width="stretch"):
        run_details()
        st.button("Start over", key="v_start_over", on_click=reset, width="stretch")
        st.caption("The validation lives only in this browser tab. Download the report before closing it.")
