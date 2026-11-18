"""Validator page 3: self-review before scoring."""

from __future__ import annotations

import streamlit as st

from review_app import review_session as rs
from review_app import skill_validation as sv
from review_app.addresses import format_ref
from review_app.skill_validation import LABEL_TEXT, Label
from review_app.ui.state import get_state
from review_app.ui.validate.common import go, step_bar


def _edit(target: str) -> None:
    """edit:{fid} opens the finding on the label page; edit:{miss_id} goes to the misses page."""
    state = get_state()
    if target in state.findings:
        rs.jump(state, target)
        go("v_label")
    else:
        go("v_misses")


def _location(state, fid: str) -> str:
    sheet, cells, _ = rs.finding_cells(state, fid)
    if sheet is None:
        return "—"
    return format_ref(sheet, cells[0]) if cells else sheet


def render() -> None:
    state = get_state()
    v = state.validation
    step_bar("v_check")
    st.subheader("Check before scoring")

    o = sv.outcomes(state.order, v)
    manual = sum(1 for m in v.misses if m.source == "manual")
    keyed = sum(1 for m in v.misses if m.source == "answer_key")
    cols = st.columns(6)
    cols[0].metric("Labelled real", o.tp)
    cols[1].metric("Labelled false alarm", o.fp)
    cols[2].metric("Labelled duplicate", o.duplicates)
    cols[3].metric("Unlabelled", o.unlabelled)
    cols[4].metric("Misses (manual, from the answer key)", f"{manual}, {keyed}")
    cols[5].metric("Open suggestions", len(v.suggested))

    todo = sv.unlabelled(state.order, v)
    if todo:
        st.warning(f"{len(todo)} findings are not labelled")
        for col, fid in zip(st.columns(min(len(todo), 8)), todo[:8], strict=False):
            col.button(fid, key=f"open:{fid}", on_click=_edit, args=(fid,))
        for fid in todo[8:]:
            st.button(fid, key=f"open:{fid}", on_click=_edit, args=(fid,))
    if v.suggested:
        st.warning(f"{len(v.suggested)} answer-key suggestions are still open")
    bare = [f for f in state.order if v.labels.get(f) is Label.FALSE_ALARM and not v.comments.get(f)]
    if bare:
        st.warning(f"{len(bare)} false alarms have no comment")

    st.markdown("**Findings**")
    st.dataframe(
        [
            {
                "ID": fid,
                "Rule": state.findings[fid].rule.rule_id,
                "Location": _location(state, fid),
                "Label": LABEL_TEXT[v.labels[fid]] if fid in v.labels else "",
                "Comment": v.comments.get(fid, ""),
            }
            for fid in state.order
        ],
        hide_index=True,
        width="stretch",
    )
    st.markdown("**Missed problems**")
    if v.misses:
        st.dataframe(
            [
                {
                    "ID": m.id,
                    "Location": format_ref(m.sheet, m.ref),
                    "Rule": m.rule_id,
                    "Note": m.note,
                    "Source": m.source,
                }
                for m in v.misses
            ],
            hide_index=True,
            width="stretch",
        )
    else:
        st.caption("No missed problems recorded.")

    with st.expander("Edit a finding or a missed problem"):
        for fid in state.order:
            st.button(f"Edit {fid}", key=f"edit:{fid}", on_click=_edit, args=(fid,))
        for m in v.misses:
            st.button(f"Edit {m.id}", key=f"edit:{m.id}", on_click=_edit, args=(m.id,))

    st.button("Next: results", key="v_check_next", on_click=go, args=("v_results",), type="primary")
