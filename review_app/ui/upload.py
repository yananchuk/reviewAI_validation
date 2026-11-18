"""Step 1 and 2: upload the workbook and the report, show validation results, confirm and start."""

from __future__ import annotations

import streamlit as st

from review_app import review_session as rs
from review_app.ui.state import esc, get_state, reset, set_state, upload_key

MODE_REVIEW = "Review the workpaper"
MODE_VALIDATE = "Validate the skill (label findings and score it)"
CONFLICT_KEY = "mode_conflict"
# Review mode is not available yet: the choice is shown but locked to validation. Flip to re-enable it.
REVIEW_MODE_AVAILABLE = False


def _wanted_mode() -> str:
    if not REVIEW_MODE_AVAILABLE:
        return "validate"
    return "validate" if st.session_state.get("mode") == MODE_VALIDATE else "review"


def _start() -> None:
    state = get_state()
    wanted = _wanted_mode()
    if wanted != state.mode and rs.has_work(state):
        st.session_state[CONFLICT_KEY] = True  # the page asks the user to confirm discarding first
        return
    st.session_state.pop(CONFLICT_KEY, None)
    (rs.start_validation if wanted == "validate" else rs.start_review)(state)


def _discard_mode_work() -> None:
    rs.discard_work(get_state())
    st.session_state.pop(CONFLICT_KEY, None)


def mode_choice() -> None:
    if REVIEW_MODE_AVAILABLE:
        st.radio("What do you want to do?", [MODE_REVIEW, MODE_VALIDATE], key="mode")
        return
    st.session_state["mode"] = MODE_VALIDATE
    st.radio(
        "What do you want to do?",
        [MODE_REVIEW, MODE_VALIDATE],
        key="mode",
        format_func=lambda m: f"{m} (to-do)" if m == MODE_REVIEW else m,
        disabled=True,
        help="Reviewing the workpaper is not available yet. Only skill validation can be run for now.",
    )


def _confirm() -> None:
    get_state().name_mismatch_confirmed = bool(st.session_state.get("confirm_mismatch"))


def render() -> None:
    st.title("Review AI")
    st.write(
        "Upload the workbook that was reviewed and the JSON report produced by the review skill. "
        "Everything stays on this computer. The uploaded workbook is never changed: fixes go into a copy."
    )
    mode_choice()
    col1, col2 = st.columns(2)
    wb = col1.file_uploader(
        "Workbook (.xlsx or .xlsm)", type=["xlsx", "xlsm", "xls", "xlsb"], key=upload_key("workbook")
    )
    rep = col2.file_uploader("Review report (.json)", type=["json"], key=upload_key("report"))
    if not (wb and rep):
        st.info("Upload both files to validate them.")
        return

    state = get_state()
    wb_bytes, rep_bytes = wb.getvalue(), rep.getvalue()
    token = rs.upload_token(wb_bytes, rep_bytes)
    if token != state.upload_token:
        if state.decisions or state.applied or state.validation.labels or state.validation.misses:
            st.warning("These files differ from the ones you were reviewing. Continuing discards the current review.")
            if not st.button("Discard the current review and load the new files", key="discard_review"):
                return
        reset(clear_uploads=False)
        with st.spinner("Validating the files…"):
            state = rs.start_session(wb.name, wb_bytes, rep.name, rep_bytes)
        set_state(state)
    render_validation(state, with_mode_choice=False)


def render_validation(state: rs.SessionState, with_mode_choice: bool = True) -> None:
    if with_mode_choice:  # render() shows the choice above the uploaders instead
        mode_choice()
    st.subheader("Validation")
    for issue in state.validation_errors:
        st.error(issue.message)
    if state.validation_errors:
        st.error("The review cannot start until these problems are fixed.")
        return
    meta, profile = state.report_meta, state.profile
    st.success(f"The report and the workbook were read: {len(state.order)} findings.")
    c1, c2, c3 = st.columns(3)
    c1.metric("Findings", len(state.order))
    progress = rs.progress(state)
    c2.metric("Quick Fixes", progress["quick_fix"])
    c3.metric("Follow-ups", progress["follow_up"])
    st.markdown(
        f"**Report:** run {esc(meta.run_id)}, schema {esc(meta.schema_version)}, skill {esc(meta.skill_version)}, "
        f"reviewed {esc(meta.reviewed_at)}"
    )
    hidden = [s.name for s in profile.sheets if s.state != "visible"]
    st.markdown(
        f"**Workbook:** {len(profile.sheets)} sheets"
        + (f", hidden: {esc(', '.join(hidden))}" if hidden else "")
        + (", contains macros" if profile.has_vba else "")
    )
    if _wanted_mode() == "validate":
        st.markdown(":green-badge[Read only: nothing is changed]")
    elif profile.writable:
        st.markdown(":green-badge[Changes can be written to a copy]")
    else:
        st.markdown(":orange-badge[Review only]")
    if state.validation_warnings:
        with st.expander(f"Warnings ({len(state.validation_warnings)})", expanded=state.name_mismatch):
            for w in state.validation_warnings:
                st.warning(w.message)
    if state.name_mismatch:
        st.checkbox(
            "I confirm this report belongs to the uploaded workbook", key="confirm_mismatch", on_change=_confirm
        )
    if st.session_state.get(CONFLICT_KEY):
        st.warning(
            "Switching mode discards the work you have done in the current mode. "
            "Discard it to continue, or switch the choice back."
        )
        st.button("Discard the current work and switch mode", key="discard_review", on_click=_discard_mode_work)
    st.button(
        "Start validation" if _wanted_mode() == "validate" else "Start review",
        type="primary",
        disabled=not state.can_start,
        on_click=_start,
        key="start_review",
    )
