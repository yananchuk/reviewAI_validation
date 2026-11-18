"""The review workspace: one finding at a time, with its locations, the proposed change and the decision controls."""

from __future__ import annotations

import streamlit as st

from review_app import review_session as rs
from review_app.addresses import format_ref
from review_app.comments import comment_required
from review_app.models import DecisionValue as D
from review_app.models import NormalizedFinding, Resolution
from review_app.quick_fixes import STALE_MESSAGE, describe_proposed, target_label
from review_app.ui import preview
from review_app.ui.state import STALE_KEY, action_id, esc, flash, flash_outcome, get_state, show_flash
from review_app.workbook import display, open_reader

DECISION_LABELS = {
    D.OPEN: "Open",
    D.ACCEPTED: "Fix applied",
    D.REJECTED: "Rejected",
    D.RESOLVED: "Resolved",
    D.NOT_APPLICABLE: "Not applicable",
    D.MANUALLY_RESOLVED: "Manually resolved",
    D.KEPT_OPEN: "Kept open",
}
FOLLOW_UP_CHOICES = [D.RESOLVED, D.NOT_APPLICABLE, D.KEPT_OPEN]


def _comment_key(fid: str) -> str:
    return f"comment:{fid}"


def _comment(fid: str) -> str:
    return st.session_state.get(_comment_key(fid), "")


def _store_comment(fid: str) -> None:
    get_state().comments[fid] = _comment(fid)


# ---------------------------------------------------------------- callbacks


def _go(where: str) -> None:
    rs.go(get_state(), where)


def _jump(fid: str) -> None:
    rs.jump(get_state(), fid)


def _apply(fid: str, aid: str) -> None:
    state = get_state()
    out = rs.apply_fix(state, fid, aid, comment=_comment(fid))
    if out.status == "stale":
        st.session_state[STALE_KEY] = fid
    else:
        flash_outcome(out, "Fix applied to the working copy.")


def _decide(fid: str, value: D, aid: str) -> None:
    out = rs.decide(get_state(), fid, value, _comment(fid), aid)
    flash_outcome(out, f"Decision saved: {DECISION_LABELS[value]}.")


def _decide_follow_up(fid: str, aid: str) -> None:
    _decide(fid, st.session_state[f"fu_choice:{fid}"], aid)


def _undo(fid: str, aid: str) -> None:
    flash_outcome(rs.undo(get_state(), fid, aid), "The fix was undone. The working copy is back to its previous state.")


def _reopen(fid: str, aid: str) -> None:
    flash_outcome(rs.clear_decision(get_state(), fid, aid), "The finding is open again.")


def _toggle_reveal(fid: str, index: int, key: str) -> None:
    state = get_state()
    request = rs.hidden_targets(state, state.findings[fid])[index]
    out = rs.toggle_reveal(state, request, bool(st.session_state[key]), action_id(state, f"reveal{index}", fid))
    if out.status == "blocked":
        flash("error", out.message)


# ---------------------------------------------------------------- stale dialog


CLOSE_KEY = "stale_close"
ERROR_KEY = "stale_error"


def _stale_action(fid: str, kind: str, aid: str) -> None:
    """Callbacks run before the next run whether or not the dialog renders again, so the action is never lost."""
    state = get_state()
    if kind == "apply":
        out = rs.apply_fix(state, fid, aid, override_stale=True, comment=_comment(fid))
        flash_outcome(out, "Fix applied although the workbook had changed. The override is recorded in the log.")
    elif kind == "manual":
        out = rs.decide(state, fid, D.MANUALLY_RESOLVED, st.session_state.get(f"stale_comment:{fid}", ""), aid)
        if out.status == "invalid":
            st.session_state[ERROR_KEY] = out.message  # keep the dialog open and show why
            return
        flash_outcome(out, "Decision saved: Manually resolved.")
    elif kind == "keep":
        flash_outcome(rs.decide(state, fid, D.KEPT_OPEN, "", aid), "Decision saved: Kept open.")
    st.session_state[CLOSE_KEY] = True


@st.dialog("The workbook has changed")
def _stale_dialog(fid: str) -> None:
    if st.session_state.pop(CLOSE_KEY, False):
        st.rerun()  # an action inside the dialog finished: close it and refresh the whole page
    state = get_state()
    check = rs.check_fix(state, fid)
    st.write(STALE_MESSAGE)
    for detail in check.stale_details if check else []:
        st.markdown(f"- {esc(detail)}")
    st.text_area("Comment (required for Mark as manually resolved)", key=f"stale_comment:{fid}")
    error = st.session_state.pop(ERROR_KEY, None)
    if error:
        st.error(error)
    c1, c2, c3, c4 = st.columns(4)
    for col, kind, label in (
        (c1, "cancel", "Cancel"),
        (c2, "apply", "Apply anyway"),
        (c3, "manual", "Mark as manually resolved"),
        (c4, "keep", "Keep open"),
    ):
        col.button(
            label,
            key=f"stale_{kind}:{fid}",
            type="primary" if kind == "apply" else "secondary",
            on_click=_stale_action,
            args=(fid, kind, action_id(state, f"stale_{kind}", fid)),
        )


# ---------------------------------------------------------------- rendering


def render() -> None:
    """The finding card for the decision panel."""
    state = get_state()
    show_flash()
    fid = state.current_id
    if fid is None:
        st.info("No finding to show. Change the filters or switch between Quick fixes and Follow-up above.")
        return
    f = state.findings[fid]
    # Consumed on open: dismissing the dialog with its close button must not reopen it on the next rerun.
    # Buttons inside the dialog rerun only the dialog, so they do not need the flag.
    st.session_state.pop(CLOSE_KEY, None)
    if st.session_state.pop(STALE_KEY, None) == fid:
        _stale_dialog(fid)
    _header(state, f)
    if f.resolution == Resolution.QUICK_FIX:
        _quick_fix(state, f)
    else:
        _follow_up(state, f)
    _details(state, f)
    st.divider()
    _nav(state)


def _header(state: rs.SessionState, f: NormalizedFinding) -> None:
    status = rs.status_of(state, f.id)
    kind = ":blue-badge[Quick fix]" if f.resolution == Resolution.QUICK_FIX else ":orange-badge[Follow-up]"
    done = ":green-badge" if rs.is_reviewed(state, f.id) else ":gray-badge"
    st.markdown(f"{kind} {done}[{DECISION_LABELS[status]}] &nbsp; **{esc(f.id)}** · {esc(f.rule.rule_id)}")
    _header_body(state, f)


def show_finding(state: rs.SessionState, f: NormalizedFinding) -> None:
    """The read-only finding card (no decision badge, no controls); used by the validator."""
    kind = ":blue-badge[Quick fix]" if f.resolution == Resolution.QUICK_FIX else ":orange-badge[Follow-up]"
    st.markdown(f"{kind} &nbsp; **{esc(f.id)}** · {esc(f.rule.rule_id)}")
    _header_body(state, f)
    if f.resolution == Resolution.QUICK_FIX:
        _proposed_change(f)
    _cell_details(state, f)


def _header_body(state: rs.SessionState, f: NormalizedFinding) -> None:
    sheet, _, target = rs.finding_cells(state, f.id)
    if sheet is not None:
        st.button(
            format_ref(sheet, target),
            key=f"chip:{f.id}",
            icon=":material/my_location:",
            help="Show this location in the workbook",
            on_click=_jump,
            args=(f.id,),
        )
    st.markdown(f"**{esc(f.rule.title or f.rule.rule_id)}**")
    st.caption(f"Theme: {f.rule.theme or 'none'} · Case: {f.case}")
    st.text(f.message)
    if f.check_problems:
        st.warning(
            "The automatic re-check of this finding failed; review it with care:\n\n"
            + "\n".join(f"- {esc(p)}" for p in f.check_problems)
        )
    if f.fallback_reason:
        st.caption(f"Why this is not a Quick fix: {f.fallback_reason}")
    if f.duplicates or f.missing_duplicates:
        st.markdown(":violet-badge[Possible duplicate]")
        for d in f.duplicates:
            st.button(f"Open {d}", key=f"dup:{f.id}:{d}", on_click=_jump, args=(d,))
        if f.missing_duplicates:
            st.caption(f"Referenced but not in the report: {', '.join(f.missing_duplicates)}")
    if sheet is not None and target is not None:
        others = [o for o in rs.findings_at(state, sheet, target) if o != f.id]
        if others:
            st.caption("Also at this cell:")
            for o in others:
                st.button(f"Open {o}", key=f"also:{f.id}:{o}", on_click=_jump, args=(o,))


def _nav(state: rs.SessionState) -> None:
    ids = rs.visible_ids(state)
    pos = ids.index(state.current_id) + 1 if state.current_id in ids else 0
    c1, c2, c3 = st.columns(3)
    c1.button("◀ Back", on_click=_go, args=("previous",), disabled=pos <= 1, key="nav_prev", width="stretch")
    c2.button("Next ▶", on_click=_go, args=("next",), disabled=pos >= len(ids), key="nav_next", width="stretch")
    c3.button("Next open", on_click=_go, args=("next_open",), key="nav_next_open", width="stretch")
    st.caption(f"{pos} of {len(ids)} in this list")


def _details(state: rs.SessionState, f: NormalizedFinding) -> None:
    _reveal_controls(state, f)
    _cell_details(state, f)


def _reveal_controls(state: rs.SessionState, f: NormalizedFinding) -> None:
    """Review only: these change the output workbook."""
    reveals = rs.hidden_targets(state, f)
    if reveals and state.profile.writable:
        st.markdown("**Hidden content.** The output keeps the original visibility unless you choose otherwise:")
        requested = {r.key for r in state.visibility_requests}
        for i, r in enumerate(reveals):
            key = f"reveal:{f.id}:{r.what}:{r.ref}"
            st.session_state[key] = r.key in requested
            st.checkbox(
                f"Unhide {r.what} {r.ref} on {r.sheet} in the output workbook",
                key=key,
                on_change=_toggle_reveal,
                args=(f.id, i, key),
            )


def _cell_details(state: rs.SessionState, f: NormalizedFinding) -> None:
    with st.expander("Cell details"):
        if not f.locations:
            st.caption("The report gives no location for this finding.")
        reader = open_reader(state.working_bytes)
        for loc in f.locations:
            label = format_ref(loc.sheet, loc.ref) if loc.sheet else "Workbook"
            st.markdown(f"**{esc(loc.kind.capitalize())}: {esc(label)}**")
            preview.cell_details(reader, loc)


def _comment_box(fid: str, label: str) -> None:
    key = _comment_key(fid)
    if key not in st.session_state:
        st.session_state[key] = get_state().comments.get(fid, "")
    st.text_area(label, key=key, on_change=_store_comment, args=(fid,), height=90)


def _proposed_change(f: NormalizedFinding) -> None:
    """The report's before/after and the proposed action, read-only."""
    st.markdown("**Proposed change**")
    if f.change is not None:
        st.caption("Report: before")
        st.code(display(f.change.before, 2000), language=None, wrap_lines=True)
        st.caption("Report: after")
        st.code(display(f.change.after, 2000), language=None, wrap_lines=True)
    if f.automatic:
        st.markdown(f"**Action:** {esc(describe_proposed(f.operation))}")
    else:
        st.info(f.operation.reason)


def _quick_fix(state: rs.SessionState, f: NormalizedFinding) -> None:
    applied = rs.applied_change(state, f.id)
    _proposed_change(f)
    check = None
    if f.automatic:
        if applied:
            st.success(f"Applied to {target_label(f.operation)} in the working copy.")
        else:
            check = rs.check_fix(state, f.id)
            if check.blocked:
                st.warning(check.blocked)
            elif check.stale:
                st.warning(STALE_MESSAGE + "\n\n" + "\n".join(f"- {esc(d)}" for d in check.stale_details))

    _comment_box(f.id, "Reviewer comment" + (" (required for Mark as manually resolved)" if not applied else ""))
    decided = state.decisions.get(f.id)
    b1, b2 = st.columns(2)
    b3, b4 = st.columns(2)
    if applied:
        b1.button("Undo fix", key=f"undo:{f.id}", on_click=_undo, args=(f.id, action_id(state, "undo", f.id)))
        return
    can_apply = f.automatic and check is not None and not check.blocked and state.profile.writable
    b1.button(
        "Apply fix",
        type="primary",
        key=f"apply:{f.id}",
        disabled=not can_apply,
        on_click=_apply,
        args=(f.id, action_id(state, "apply", f.id)),
    )
    b2.button(
        "Reject", key=f"reject:{f.id}", on_click=_decide, args=(f.id, D.REJECTED, action_id(state, "reject", f.id))
    )
    b3.button(
        "Mark as manually resolved",
        key=f"manual:{f.id}",
        on_click=_decide,
        args=(f.id, D.MANUALLY_RESOLVED, action_id(state, "manual", f.id)),
    )
    if decided:
        b4.button("Reopen", key=f"reopen:{f.id}", on_click=_reopen, args=(f.id, action_id(state, "reopen", f.id)))
    else:
        b4.button(
            "Keep open", key=f"keep:{f.id}", on_click=_decide, args=(f.id, D.KEPT_OPEN, action_id(state, "keep", f.id))
        )


def _follow_up(state: rs.SessionState, f: NormalizedFinding) -> None:
    st.markdown("**Your decision**")
    st.caption("Follow-ups need your judgement. The workbook is never changed for a Follow-up.")
    key = f"fu_choice:{f.id}"
    decided = state.decisions.get(f.id)
    if key not in st.session_state:
        st.session_state[key] = decided.value if decided else D.KEPT_OPEN
    st.radio("Decision", FOLLOW_UP_CHOICES, format_func=DECISION_LABELS.get, key=key, horizontal=True)
    required = comment_required(st.session_state[key])
    _comment_box(f.id, "Reviewer comment" + (" (required: at least 10 characters)" if required else " (optional)"))
    c1, c2 = st.columns(2)
    c1.button(
        "Save decision",
        type="primary",
        key=f"save:{f.id}",
        on_click=_decide_follow_up,
        args=(f.id, action_id(state, "save", f.id)),
    )
    if decided:
        c2.button("Reopen", key=f"reopen:{f.id}", on_click=_reopen, args=(f.id, action_id(state, "reopen", f.id)))
        st.caption(f"Saved {decided.decided_at}: {DECISION_LABELS[decided.value]}")
