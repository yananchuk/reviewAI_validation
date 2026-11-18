"""Validator page 2: record problems the skill missed, and confirm answer-key suggestions."""

from __future__ import annotations

import streamlit as st

from review_app import limits
from review_app import skill_validation as sv
from review_app.addresses import format_ref
from review_app.skill_validation import OTHER
from review_app.ui.state import esc, get_state
from review_app.ui.validate.common import go, step_bar


def _rule_label(state, rule_id: str) -> str:
    if rule_id == OTHER:
        return "Other"
    title = state.rule_titles.get(rule_id, "")
    return f"{rule_id} - {title}" if title else rule_id


def _warn(msg: str | None) -> None:
    st.session_state["v_miss_warning"] = msg


def _add() -> None:
    state = get_state()
    v = state.validation
    if v.selection is None:
        return
    sheet, ref = v.selection
    res = sv.add_miss(v, sheet, ref, st.session_state["v_miss_rule"], st.session_state.get("v_miss_note", ""))
    if isinstance(res, str):
        _warn(res)
        return
    _warn(None)
    v.selection = None
    st.session_state["v_miss_note"] = ""


def _remove(miss_id: str) -> None:
    _warn(None)
    sv.remove_miss(get_state().validation, miss_id)


def _accept(miss_id: str) -> None:
    _warn(sv.accept_suggestion(get_state().validation, miss_id))


def _dismiss(miss_id: str) -> None:
    _warn(None)
    sv.dismiss_suggestion(get_state().validation, miss_id)


def render() -> None:
    state = get_state()
    v = state.validation
    step_bar("v_misses")
    st.subheader("Record missed problems")
    st.caption("Select the cell or range of a problem the skill did not report, then add it.")
    if v.selection:
        st.markdown(f"Selected: **{format_ref(*v.selection)}**")
    else:
        st.info(
            "Select a cell or range in the workbook "
            f"(up to {limits.MISS_RANGE_MAX_CELLS:,} cells; a larger selection is ignored)."
        )
    st.selectbox(
        "Rule",
        [*state.rule_ids, OTHER],
        format_func=lambda r: _rule_label(state, r),
        key="v_miss_rule",
    )
    st.text_input("Note (optional)", key="v_miss_note")
    st.button("Add missed problem", key="v_add_miss", disabled=v.selection is None, on_click=_add)
    warning = st.session_state.get("v_miss_warning")
    if warning:
        st.warning(warning)

    st.markdown(f"**Missed problems ({len(v.misses)})**")
    for m in v.misses:
        c1, c2 = st.columns([8, 2])
        with c1:
            note = f" - {esc(m.note)}" if m.note else ""
            st.markdown(f"`{m.id}` {esc(format_ref(m.sheet, m.ref))} - {esc(_rule_label(state, m.rule_id))}{note}")
        with c2:
            st.button("Remove", key=f"rm:{m.id}", on_click=_remove, args=(m.id,))

    if v.answer_key or v.suggested or v.dismissed:  # suggestions only come from an answer key
        with st.expander(f"Suggested from the answer key ({len(v.suggested)})"):
            for m in v.suggested:
                st.markdown(f"`{m.id}` {esc(format_ref(m.sheet, m.ref))} - {esc(_rule_label(state, m.rule_id))}")
                c1, c2 = st.columns(2)
                c1.button("Confirm", key=f"ok:{m.id}", on_click=_accept, args=(m.id,))
                c2.button("Dismiss", key=f"no:{m.id}", on_click=_dismiss, args=(m.id,))

    st.button("Continue to check", key="v_to_check", on_click=go, args=("v_check",))
