"""Validator page 1: label each finding as a real problem, a false alarm or a duplicate."""

from __future__ import annotations

import streamlit as st

from review_app import review_session as rs
from review_app import skill_validation as sv
from review_app.skill_validation import LABEL_TEXT, Label
from review_app.ui import finding_card, panel
from review_app.ui.state import get_state
from review_app.ui.validate.common import go, step_bar


def _ids(state: rs.SessionState) -> list[str]:
    return [f for f in state.order if rs.matches_filters(state, f)]


def _on_select() -> None:
    rs.jump(get_state(), st.session_state["v_select"])


def _label(fid: str, label: Label) -> None:
    v = get_state().validation
    sv.set_label(v, fid, None if v.labels.get(fid) == label else label)


def _store_comment(fid: str) -> None:
    sv.set_comment(get_state().validation, fid, st.session_state.get(f"v_comment:{fid}", ""))


def _step(direction: int) -> None:
    state = get_state()
    ids = _ids(state)
    if not ids:
        return
    i = ids.index(state.current_id) if state.current_id in ids else 0
    rs.jump(state, ids[max(0, min(i + direction, len(ids) - 1))])


def _next_unlabelled() -> None:
    state = get_state()
    ids = _ids(state)
    i = ids.index(state.current_id) if state.current_id in ids else -1
    pending = set(sv.unlabelled(ids, state.validation))
    nxt = next((x for x in ids[i + 1 :] + ids[: i + 1] if x in pending and x != state.current_id), None)
    if nxt is not None:
        rs.jump(state, nxt)


def render() -> None:
    state = get_state()
    step_bar("v_label")
    ids = _ids(state)
    if state.current_id not in ids and ids:
        state.current_id = next((i for i in ids if i not in state.validation.labels), ids[0])
    panel.filter_popover(state)
    done = len(state.validation.labels)
    total = len(state.order)
    st.progress(done / total if total else 1.0, text=f"{done} of {total} labelled")
    if not ids or state.current_id is None:
        st.info("No finding to show. Change the filters above.")
        return
    fid = state.current_id
    st.session_state["v_select"] = fid
    st.selectbox(
        "Finding",
        ids,
        key="v_select",
        on_change=_on_select,
        format_func=lambda x: panel._label(state, x),
        label_visibility="collapsed",
    )
    finding_card.show_finding(state, state.findings[fid])
    current = state.validation.labels.get(fid)
    cols = st.columns(len(Label))
    for col, label in zip(cols, Label, strict=True):
        col.button(
            LABEL_TEXT[label],
            key=f"label:{label}:{fid}",
            type="primary" if current == label else "secondary",
            on_click=_label,
            args=(fid, label),
            width="stretch",
        )
    ckey = f"v_comment:{fid}"
    if ckey not in st.session_state:
        st.session_state[ckey] = state.validation.comments.get(fid, "")
    st.text_area("Comment (optional)", key=ckey, on_change=_store_comment, args=(fid,), height=90)
    p, n = st.columns(2)
    p.button("◀ Previous", key="v_prev", on_click=_step, args=(-1,), width="stretch")
    n.button("Next unlabelled ▶", key="v_next_unlabelled", on_click=_next_unlabelled, width="stretch")
    st.button("Continue to missed problems", key="v_to_misses", on_click=go, args=("v_misses",))
