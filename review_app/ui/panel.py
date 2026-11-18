"""The decision panel on the right of the workbook: tabs, progress, filters, finding list and the finding card."""

from __future__ import annotations

import streamlit as st

from review_app import review_session as rs
from review_app.models import Resolution
from review_app.ui import finding_card
from review_app.ui.state import esc, get_state, reset

STATUS_LABELS = {"all": "All", "open": "Open", "reviewed": "Reviewed"}


def _on_tab() -> None:
    rs.set_tab(get_state(), st.session_state["nav_tab"])


def _on_filters() -> None:
    rs.set_filters(
        get_state(),
        status=st.session_state["f_status"],
        rules=st.session_state["f_rules"],
        sheets=st.session_state["f_sheets"],
        themes=st.session_state["f_themes"],
    )


def _on_select() -> None:
    rs.jump(get_state(), st.session_state["nav_select"])


def _set_page(page: str) -> None:
    get_state().page = page


def _label(state: rs.SessionState, fid: str) -> str:
    f = state.findings[fid]
    mark = "✓" if rs.is_reviewed(state, fid) else "○"
    return f"{mark} {fid} · {f.rule.rule_id} · {f.primary_label}"


def filter_popover(state: rs.SessionState) -> None:
    """The Filter popover (status, rule, worksheet, theme); shared by review and the validator."""
    filtered = rs.filters_on(state)
    with st.popover("Filter" + (" (on)" if filtered else ""), width="stretch"):
        findings = [state.findings[f] for f in state.order]
        st.session_state["f_status"] = state.filters.status
        st.session_state["f_rules"] = state.filters.rules
        st.session_state["f_sheets"] = state.filters.sheets
        st.session_state["f_themes"] = state.filters.themes
        st.radio(
            "Status",
            list(STATUS_LABELS),
            format_func=STATUS_LABELS.get,
            key="f_status",
            on_change=_on_filters,
            horizontal=True,
        )
        st.multiselect("Rule", sorted({f.rule.rule_id for f in findings}), key="f_rules", on_change=_on_filters)
        st.multiselect(
            "Worksheet", sorted({s for f in findings for s in f.sheets}), key="f_sheets", on_change=_on_filters
        )
        st.multiselect(
            "Theme", sorted({f.rule.theme for f in findings if f.rule.theme}), key="f_themes", on_change=_on_filters
        )


def render(state: rs.SessionState) -> None:
    filtered = rs.filters_on(state)
    p = rs.progress(state, filtered=True)
    counts = {Resolution.QUICK_FIX: p["quick_fix"], Resolution.FOLLOW_UP: p["follow_up"]}
    names = {Resolution.QUICK_FIX: "Quick fixes", Resolution.FOLLOW_UP: "Follow-up"}
    # Widget values follow the session state, which callbacks and the workbook pane may change.
    st.session_state["nav_tab"] = state.tab
    st.radio(
        "Findings",
        list(names),
        format_func=lambda r: f"{names[r]} ({counts[r]})",
        key="nav_tab",
        on_change=_on_tab,
        horizontal=True,
        label_visibility="collapsed",
    )
    in_tab = [f for f in state.order if state.findings[f].resolution == state.tab and rs.matches_filters(state, f)]
    done = sum(rs.is_reviewed(state, f) for f in in_tab)
    st.progress(
        done / len(in_tab) if in_tab else 1.0,
        text=f"{done} of {len(in_tab)} addressed · {p['remaining']} open in total" + (" (filtered)" if filtered else ""),
    )

    c1, c2, c3 = st.columns([2, 2, 1])
    with c1:
        filter_popover(state)
    c2.button("Finish review", key="to_complete", on_click=_set_page, args=("complete",), width="stretch")
    with c3.popover("⋯", width="stretch"):
        st.caption(f"{esc(state.original_name)} · report run {esc(state.report_meta.run_id)}")
        st.caption(f"Schema {state.report_meta.schema_version} · skill {state.report_meta.skill_version}")
        st.button("Start over", key="start_over", on_click=reset, width="stretch")
        st.caption("The review lives only in this browser tab. Download the review log before closing it.")

    ids = rs.visible_ids(state)
    if ids:
        st.session_state["nav_select"] = state.current_id
        st.selectbox(
            "Finding",
            ids,
            key="nav_select",
            on_change=_on_select,
            format_func=lambda fid: _label(state, fid),
            label_visibility="collapsed",
        )
    if not state.profile.writable:
        st.warning("Review only: changes cannot be written to this workbook.")
    finding_card.render()
