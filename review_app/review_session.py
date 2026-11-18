"""The review session: the single source of truth for decisions and workbook state.

All functions here are plain Python (no Streamlit import) and are the only code that changes decisions, applied
changes or outputs. They mutate the given SessionState in place and bump `version`; UI callbacks call them.

Idempotency: every mutating call takes an `action_id`. An id seen before is ignored, so a Streamlit rerun or a
double click can never apply a change twice. The workbook is never kept as a live object: the working copy is
always rebuilt from the original bytes plus the applied changes, which also makes undo of any change exact."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Literal

from openpyxl.utils import get_column_letter
from pydantic import BaseModel, ConfigDict

from review_app import exporter, intake, limits
from review_app.addresses import col_letter, parse_cell, parse_range
from review_app.comments import validate_comment
from review_app.integrity import download_allowed, run_checks
from review_app.models import (
    REVIEWED_DECISIONS,
    AppliedChange,
    Decision,
    DecisionValue,
    IntegrityResult,
    Manual,
    NormalizedFinding,
    ReportMeta,
    Resolution,
    ValidationIssue,
    VisibilityRequest,
    WorkbookProfile,
    utc_now,
)
from review_app.normalizer import normalize
from review_app.package_patch import PatchError, build
from review_app.quick_fixes import STALE_MESSAGE, precheck
from review_app.sheet_view import anchor_of
from review_app.skill_validation import ValidationState, load_answer_key
from review_app.validation import check_workbook_name, validate_report
from review_app.workbook import open_reader, sha256

log = logging.getLogger(__name__)

StatusFilter = Literal["all", "open", "reviewed"]

FOLLOW_UP_DECISIONS = (DecisionValue.RESOLVED, DecisionValue.NOT_APPLICABLE, DecisionValue.KEPT_OPEN)
QUICK_FIX_DECISIONS = (DecisionValue.REJECTED, DecisionValue.MANUALLY_RESOLVED, DecisionValue.KEPT_OPEN)


class Filters(BaseModel):
    status: StatusFilter = "all"
    rules: list[str] = []
    sheets: list[str] = []
    themes: list[str] = []


class SessionState(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True, validate_assignment=False)

    version: int = 0
    upload_token: str = ""
    page: Literal["upload", "review", "complete", "v_label", "v_misses", "v_check", "v_results"] = "upload"
    mode: Literal["review", "validate"] = "review"
    validation: ValidationState = ValidationState()
    rule_ids: list[str] = []  # every rule id in the report, including rules without findings
    rule_titles: dict[str, str] = {}  # rule id -> title, for every rule in the report

    original_name: str = ""
    original_bytes: bytes = b""
    original_sha256: str = ""
    working_bytes: bytes = b""
    report_name: str = ""
    report_sha256: str = ""

    report_meta: ReportMeta | None = None
    findings: dict[str, NormalizedFinding] = {}
    order: list[str] = []
    profile: WorkbookProfile | None = None
    validation_errors: list[ValidationIssue] = []
    validation_warnings: list[ValidationIssue] = []
    name_mismatch: bool = False
    name_mismatch_confirmed: bool = False

    tab: Resolution = Resolution.QUICK_FIX
    filters: Filters = Filters()
    current_id: str | None = None
    active_sheet: str | None = None  # sheet shown in the workbook pane

    decisions: dict[str, Decision] = {}
    comments: dict[str, str] = {}
    applied: list[AppliedChange] = []
    next_seq: int = 1
    visibility_requests: list[VisibilityRequest] = []
    processed_actions: set[str] = set()

    started_at: str | None = None
    completed_at: str | None = None
    integrity: list[IntegrityResult] = []
    outputs: dict[str, bytes] = {}
    output_meta: dict[str, dict] = {}
    output_dirty: bool = True
    errors: list[str] = []

    @property
    def can_start(self) -> bool:
        return (
            not self.validation_errors
            and bool(self.order or self.report_meta)
            and (not self.name_mismatch or self.name_mismatch_confirmed)
        )


@dataclass
class Outcome:
    status: Literal["applied", "stale", "blocked", "duplicate", "saved", "invalid", "undone"]
    message: str = ""
    details: list[str] | None = None


# ---------------------------------------------------------------- session start


def upload_token(workbook: bytes, report: bytes) -> str:
    return sha256(workbook) + sha256(report)


def start_session(workbook_name: str, workbook_bytes: bytes, report_name: str, report_bytes: bytes) -> SessionState:
    """Check and load both uploads. Blocking problems end up in validation_errors; the state stays on 'upload'."""
    state = SessionState(
        upload_token=upload_token(workbook_bytes, report_bytes),
        original_name=workbook_name,
        original_bytes=workbook_bytes,
        original_sha256=sha256(workbook_bytes),
        working_bytes=workbook_bytes,
        report_name=report_name,
        report_sha256=sha256(report_bytes),
    )
    err = [
        ValidationIssue(level="error", message=m) for m in intake.check_workbook(workbook_name, workbook_bytes).errors
    ]
    rep = intake.check_report(report_name, report_bytes)
    err += [ValidationIssue(level="error", message=m) for m in rep.errors]
    if rep.data is not None:
        issues = validate_report(rep.data)
        err += [i for i in issues if i.level == "error"]
        state.validation_warnings += [i for i in issues if i.level == "warning"]
    if not err:
        try:
            reader = open_reader(workbook_bytes)
            state.profile = reader.profile(workbook_name)
        except Exception as exc:  # noqa: BLE001 - any reader failure means the workbook cannot be used
            log.info("Workbook could not be opened: %s", type(exc).__name__)
            err.append(ValidationIssue(level="error", message="The workbook could not be opened."))
    if err:
        state.validation_errors = err
        return state

    meta, findings, warnings = normalize(rep.data)
    state.report_meta = meta
    state.rule_ids = [str(r["rule_id"]) for r in rep.data["rules"]]
    state.rule_titles = {str(r["rule_id"]): str(r.get("title") or "") for r in rep.data["rules"]}
    state.validation_warnings += warnings
    mismatch = check_workbook_name(meta.workbook, workbook_name)
    if mismatch:
        state.name_mismatch = True
        state.validation_warnings.append(mismatch)
    if not state.profile.writable:
        state.validation_warnings.append(
            ValidationIssue(
                level="warning",
                message="Review only: changes cannot be written because "
                + "; ".join(state.profile.not_writable_reasons)
                + ".",
            )
        )
    state.validation_warnings += _location_checks(findings, state.profile)
    state.findings = {f.id: f for f in findings}
    state.order = [f.id for f in findings]
    log.info("Session loaded: %d findings, schema %s", len(findings), meta.schema_version)
    return state


def _location_checks(findings: list[NormalizedFinding], profile: WorkbookProfile) -> list[ValidationIssue]:
    """Warn about locations that do not exist; downgrade automatic fixes whose sheet is missing."""
    out = []
    names = set(profile.sheet_names)
    for f in findings:
        for loc in f.locations:
            if loc.sheet is None:
                continue
            if loc.sheet not in names:
                out.append(
                    ValidationIssue(level="warning", message=f"{f.id}: sheet '{loc.sheet}' is not in the workbook.")
                )
                continue
            if loc.cell and parse_cell(loc.cell) is None:
                out.append(
                    ValidationIssue(level="warning", message=f"{f.id}: '{loc.cell}' is not a valid cell address.")
                )
            if loc.range and parse_range(loc.range) is None:
                out.append(
                    ValidationIssue(level="warning", message=f"{f.id}: '{loc.range}' is not a valid range address.")
                )
        sheet = getattr(f.operation, "sheet", None)
        if sheet is not None and sheet not in names:
            f.operation = Manual(reason=f"Sheet '{sheet}' is not in the workbook, so the change cannot be applied.")
    return out


def start_review(state: SessionState) -> None:
    if not state.can_start:
        return
    state.mode = "review"
    state.page = "review"
    state.started_at = state.started_at or utc_now()
    if not visible_ids(state) and state.tab == Resolution.QUICK_FIX:
        state.tab = Resolution.FOLLOW_UP
    ensure_current(state)
    _default_sheet(state)
    state.version += 1


def _default_sheet(state: SessionState) -> None:
    if state.active_sheet is None and state.profile is not None:
        visible = [s.name for s in state.profile.sheets if s.state == "visible"]
        state.active_sheet = visible[0] if visible else (state.profile.sheet_names or [None])[0]


def start_validation(state: SessionState) -> None:
    """Enter validator mode: read-only labelling of the findings. The workbook is never changed."""
    if not state.can_start:
        return
    state.mode = "validate"
    state.page = "v_label"
    state.validation.started_at = state.validation.started_at or utc_now()
    ensure_current(state)
    _default_sheet(state)
    load_answer_key(
        state.validation,
        state.working_bytes,
        state.profile.sheet_names,
        state.findings,
        state.order,
        set(state.rule_ids),
    )
    state.version += 1


def has_work(state: SessionState) -> bool:
    """Work that switching mode would throw away."""
    if state.mode == "validate":
        return bool(state.validation.labels or state.validation.misses)
    return bool(state.decisions or state.applied)


def discard_work(state: SessionState) -> None:
    """Drop the work of the current mode (decisions and applied changes, or labels and misses)."""
    if state.mode == "validate":
        state.validation = ValidationState()
    else:
        state.decisions, state.comments, state.applied = {}, {}, []
        state.visibility_requests, state.processed_actions = [], set()
        state.working_bytes, state.outputs, state.output_meta = state.original_bytes, {}, {}
        state.output_dirty = True
        state.integrity = []
    state.version += 1


# ---------------------------------------------------------------- status, filters, navigation


def is_reviewed(state: SessionState, fid: str) -> bool:
    if state.mode == "validate":
        return fid in state.validation.labels
    d = state.decisions.get(fid)
    return d is not None and d.value in REVIEWED_DECISIONS


def status_of(state: SessionState, fid: str) -> DecisionValue:
    d = state.decisions.get(fid)
    return d.value if d else DecisionValue.OPEN


def filters_on(state: SessionState) -> bool:
    fl = state.filters
    return fl.status != "all" or bool(fl.rules or fl.sheets or fl.themes)


def matches_filters(state: SessionState, fid: str) -> bool:
    """Whether a finding passes the filters (status, rule, worksheet, theme), on either tab."""
    fl, f = state.filters, state.findings[fid]
    if fl.status == "open" and is_reviewed(state, fid):
        return False
    if fl.status == "reviewed" and not is_reviewed(state, fid):
        return False
    if fl.rules and f.rule.rule_id not in fl.rules:
        return False
    if fl.themes and f.rule.theme not in fl.themes:
        return False
    return not (fl.sheets and not set(f.sheets) & set(fl.sheets))


def visible_ids(state: SessionState) -> list[str]:
    return [fid for fid in state.order if state.findings[fid].resolution == state.tab and matches_filters(state, fid)]


def ensure_current(state: SessionState) -> None:
    """Keep current_id inside the filtered list: else the first open one, else the first, else None."""
    ids = visible_ids(state)
    if state.current_id in ids:
        return
    state.current_id = next((i for i in ids if not is_reviewed(state, i)), ids[0] if ids else None)
    _follow_sheet(state)


def set_tab(state: SessionState, tab: Resolution) -> None:
    state.tab = tab
    ensure_current(state)


def set_filters(state: SessionState, **values) -> None:
    state.filters = state.filters.model_copy(update=values)
    ensure_current(state)


def go(state: SessionState, where: Literal["previous", "next", "next_open"]) -> None:
    ids = visible_ids(state)
    if not ids:
        state.current_id = None
        return
    i = ids.index(state.current_id) if state.current_id in ids else -1
    if where == "previous":
        state.current_id = ids[max(i - 1, 0)]
    elif where == "next":
        state.current_id = ids[min(i + 1, len(ids) - 1)]
    else:
        rotated = ids[i + 1 :] + ids[: i + 1]
        state.current_id = next(
            (x for x in rotated if not is_reviewed(state, x) and x != state.current_id), state.current_id
        )
    _follow_sheet(state)


def jump(state: SessionState, fid: str) -> None:
    """Open a finding (e.g. a possible duplicate), widening tab and filters if needed."""
    if fid not in state.findings:
        return
    state.tab = state.findings[fid].resolution
    if fid not in visible_ids(state):
        state.filters = Filters()
    state.current_id = fid
    _follow_sheet(state)


# ---------------------------------------------------------------- workbook view


def _follow_sheet(state: SessionState) -> None:
    """Show the current finding's sheet; findings without a sheet in the workbook keep the active sheet."""
    if state.current_id is not None:
        sheet, _, _ = finding_cells(state, state.current_id)
        if sheet is not None:
            state.active_sheet = sheet


def set_active_sheet(state: SessionState, sheet: str) -> None:
    if state.profile is not None and sheet in state.profile.sheet_names:
        state.active_sheet = sheet


def finding_cells(state: SessionState, fid: str) -> tuple[str | None, list[str], str | None]:
    """(sheet, anchor cells to highlight, target) for a finding: the cells of its locations on the first located
    sheet, mapped to merge anchors. Large ranges contribute their two corners only."""
    f = state.findings.get(fid)
    if f is None or state.profile is None:
        return None, [], None
    names = set(state.profile.sheet_names)
    sheet = next((loc.sheet for loc in f.locations if loc.sheet in names), None)
    if sheet is None:
        return None, [], None
    refs: list[str] = []
    for loc in f.locations:
        if loc.sheet != sheet:
            continue
        bounds = parse_range(loc.cell or loc.range or loc.anchor)
        if bounds is None:
            continue
        if bounds.size <= limits.RANGE_HIGHLIGHT_MAX:
            refs.extend(bounds.cells())
        else:  # never enumerate a huge range: a report may name A1:XFD1048576
            refs.extend(
                [f"{col_letter(bounds.min_col)}{bounds.min_row}", f"{col_letter(bounds.max_col)}{bounds.max_row}"]
            )
    anchors: list[str] = []
    for ref in refs:
        anchor = anchor_of(state.working_bytes, sheet, ref)
        if anchor not in anchors:
            anchors.append(anchor)
    return sheet, anchors, anchors[0] if anchors else None


def sheet_flags(state: SessionState, sheet: str) -> dict[str, list[dict]]:
    """Findings per anchor cell of a sheet: {'B70': [{'id': 'C009', 'type': 'quick_fix', 'reviewed': False}]}.
    Only findings that pass the filters are flagged."""
    flags: dict[str, list[dict]] = {}
    for fid in state.order:
        if not matches_filters(state, fid):
            continue
        located, cells, _ = finding_cells(state, fid)
        if located != sheet:
            continue
        entry = {"id": fid, "type": state.findings[fid].resolution.value, "reviewed": is_reviewed(state, fid)}
        for cell in cells:
            flags.setdefault(cell, []).append(entry)
    return flags


def findings_at(state: SessionState, sheet: str, cell: str) -> list[str]:
    anchor = anchor_of(state.working_bytes, sheet, cell)
    return [f["id"] for f in sheet_flags(state, sheet).get(anchor, [])]


def open_cell(state: SessionState, sheet: str, cell: str) -> str | None:
    """Open the finding at a clicked cell: an open one on the current tab, else any open one, else the first."""
    return open_one_of(state, findings_at(state, sheet, cell))


def open_one_of(state: SessionState, ids: list[str]) -> str | None:
    """Open one of several findings at the same place: an open one on the current tab, else any open, else the first."""
    if not ids:
        return None
    chosen = next(
        (i for i in ids if not is_reviewed(state, i) and state.findings[i].resolution == state.tab),
        next((i for i in ids if not is_reviewed(state, i)), ids[0]),
    )
    jump(state, chosen)
    return chosen


def progress(state: SessionState, filtered: bool = False) -> dict[str, int]:
    """Counts over all findings, or with filtered=True over the findings that pass the filters."""
    ids = [f for f in state.order if matches_filters(state, f)] if filtered else state.order
    total = len(ids)
    reviewed = sum(is_reviewed(state, f) for f in ids)
    qf = sum(state.findings[f].resolution == Resolution.QUICK_FIX for f in ids)
    return {
        "total": total,
        "reviewed": reviewed,
        "remaining": total - reviewed,
        "quick_fix": qf,
        "follow_up": total - qf,
        "applied": len(state.applied),
    }


# ---------------------------------------------------------------- decisions and changes


def _seen(state: SessionState, action_id: str) -> bool:
    if action_id in state.processed_actions:
        return True
    state.processed_actions.add(action_id)
    return False


def _changed(state: SessionState) -> None:
    state.version += 1
    state.output_dirty = True


def applied_change(state: SessionState, fid: str) -> AppliedChange | None:
    return next((c for c in state.applied if c.finding_id == fid), None)


def decide(state: SessionState, fid: str, value: DecisionValue, comment: str, action_id: str) -> Outcome:
    if _seen(state, action_id):
        return Outcome("duplicate")
    f = state.findings[fid]
    allowed = FOLLOW_UP_DECISIONS if f.resolution == Resolution.FOLLOW_UP else QUICK_FIX_DECISIONS
    if value not in allowed:
        state.processed_actions.discard(action_id)
        return Outcome("invalid", f"'{value.value}' is not a decision for this finding.")
    if applied_change(state, fid):
        state.processed_actions.discard(action_id)
        return Outcome("invalid", "Undo the applied fix before recording another decision.")
    problem = validate_comment(value, comment)
    if problem:
        state.processed_actions.discard(action_id)  # the user may correct the text and try again
        return Outcome("invalid", problem)
    state.decisions[fid] = Decision(finding_id=fid, value=value, comment=(comment or "").strip())
    state.comments[fid] = comment or ""
    _changed(state)
    return Outcome("saved")


def clear_decision(state: SessionState, fid: str, action_id: str) -> Outcome:
    if _seen(state, action_id):
        return Outcome("duplicate")
    if applied_change(state, fid):
        return Outcome("invalid", "Undo the applied fix to reopen this finding.")
    state.decisions.pop(fid, None)
    _changed(state)
    return Outcome("saved")


def check_fix(state: SessionState, fid: str):
    """Pre-application check against the current working copy (for display and for apply_fix)."""
    f = state.findings[fid]
    if f.resolution != Resolution.QUICK_FIX or not f.automatic:
        return None
    return precheck(f.operation, state.profile, state.working_bytes, open_reader(state.working_bytes))


def apply_fix(
    state: SessionState, fid: str, action_id: str, *, override_stale: bool = False, comment: str = ""
) -> Outcome:
    if _seen(state, action_id):
        return Outcome("duplicate", "This action was already handled.")
    f = state.findings[fid]
    if applied_change(state, fid):
        return Outcome("duplicate", "This fix is already applied.")
    if f.resolution != Resolution.QUICK_FIX or not f.automatic:
        return Outcome("blocked", "This finding cannot be applied automatically.")
    check = check_fix(state, fid)
    if check.blocked:
        return Outcome("blocked", check.blocked)
    if check.stale and not override_stale:
        state.processed_actions.discard(action_id)  # the user decides in the stale dialog, then acts again
        return Outcome("stale", STALE_MESSAGE, check.stale_details)
    change = AppliedChange(
        seq=state.next_seq,
        finding_id=fid,
        operation=f.operation,
        before_snapshot=check.current,
        stale_override=check.stale and override_stale,
    )
    try:
        working = _build_working(state, [*state.applied, change])
    except PatchError as exc:
        return Outcome("blocked", f"{exc} Mark the finding as manually resolved after changing it in Excel.")
    state.applied.append(change)
    state.next_seq += 1
    state.working_bytes = working
    state.decisions[fid] = Decision(
        finding_id=fid,
        value=DecisionValue.ACCEPTED,
        comment=(comment or "").strip(),
        stale_override=change.stale_override,
    )
    _changed(state)
    log.info("Applied %s (%s)", fid, f.operation.op)
    return Outcome("applied", "Fix applied to the working copy.")


def undo(state: SessionState, fid: str, action_id: str) -> Outcome:
    if _seen(state, action_id):
        return Outcome("duplicate")
    change = applied_change(state, fid)
    if change is None:
        return Outcome("invalid", "There is no applied fix to undo.")
    remaining = [c for c in state.applied if c.finding_id != fid]
    state.working_bytes = _build_working(state, remaining)  # replay without the change: exact undo
    state.applied = remaining
    state.decisions.pop(fid, None)
    _changed(state)
    return Outcome("undone", "The fix was undone.")


def _build_working(state: SessionState, changes: list[AppliedChange]) -> bytes:
    if not changes:
        return state.original_bytes
    return build(state.original_bytes, [c.operation for c in changes]).data


# ---------------------------------------------------------------- visibility


def toggle_reveal(state: SessionState, request: VisibilityRequest, on: bool, action_id: str) -> Outcome:
    if _seen(state, action_id):
        return Outcome("duplicate")
    if request.what == "sheet" and state.profile and state.profile.structure_protected:
        return Outcome("blocked", "The workbook structure is protected, so sheets cannot be unhidden.")
    keep = [r for r in state.visibility_requests if r.key != request.key]
    state.visibility_requests = [*keep, request] if on else keep
    _changed(state)
    return Outcome("saved")


def hidden_targets(state: SessionState, f: NormalizedFinding) -> list[VisibilityRequest]:
    """Hidden sheets, rows and columns at this finding's locations that could be revealed in the output."""
    if state.profile is None:
        return []
    out: dict[tuple, VisibilityRequest] = {}
    for loc in f.locations:
        sheet = state.profile.sheet(loc.sheet)
        if sheet is None:
            continue
        if sheet.state != "visible":
            out[(sheet.name, "sheet", sheet.name)] = VisibilityRequest(
                finding_id=f.id, sheet=sheet.name, what="sheet", ref=sheet.name, original_state=sheet.state
            )
        bounds = parse_range(loc.cell or loc.range or loc.anchor)
        if bounds is None or bounds.size > limits.EXPANDED_PREVIEW_CELLS:
            continue
        for r in range(bounds.min_row, bounds.max_row + 1):
            if r in sheet.hidden_rows:
                out[(sheet.name, "row", str(r))] = VisibilityRequest(
                    finding_id=f.id, sheet=sheet.name, what="row", ref=str(r), original_state="hidden"
                )
        for c in range(bounds.min_col, bounds.max_col + 1):
            letter = get_column_letter(c)
            if letter in sheet.hidden_columns:
                out[(sheet.name, "column", letter)] = VisibilityRequest(
                    finding_id=f.id, sheet=sheet.name, what="column", ref=letter, original_state="hidden"
                )
    return list(out.values())


# ---------------------------------------------------------------- outputs


def build_outputs(state: SessionState) -> None:
    """Rebuild every output from the original bytes, run the integrity gate and refresh the downloads."""
    names = exporter.output_names(state.original_name)
    state.outputs = {}
    state.output_meta = {}
    state.integrity = []
    state.completed_at = utc_now()
    changes = [c.operation for c in state.applied]
    if (changes or state.visibility_requests) and state.profile and state.profile.writable:
        try:
            result = build(state.original_bytes, changes, state.visibility_requests)
            state.integrity = run_checks(
                original=state.original_bytes,
                original_sha256=state.original_sha256,
                input_name=state.original_name,
                output_name=names["workbook"],
                result=result,
                changes=changes,
                reveals=state.visibility_requests,
            )
            if download_allowed(state.integrity):
                state.outputs[names["workbook"]] = result.data
                state.output_meta["workbook"] = {"name": names["workbook"], "sha256": sha256(result.data)}
        except PatchError as exc:
            state.errors.append(f"The output workbook could not be built: {exc}")
            state.integrity.append(
                IntegrityResult(check="Output workbook built", status="fail", detail=str(exc), blocks_download=True)
            )
        except Exception as exc:  # noqa: BLE001 - a failed build must never lose the review log
            log.exception("Output build failed")
            state.errors.append(f"The output workbook could not be built ({type(exc).__name__}).")
            state.integrity.append(
                IntegrityResult(
                    check="Output workbook built", status="fail", detail=type(exc).__name__, blocks_download=True
                )
            )
    try:
        state.outputs[names["log"]] = exporter.log_bytes(state)
    except Exception as exc:  # noqa: BLE001
        log.exception("Review log export failed")
        state.errors.append(f"The review log could not be created: {type(exc).__name__}.")
    if state.outputs:
        state.outputs[names["package"]] = exporter.package(dict(state.outputs))
    state.output_dirty = False
    state.version += 1


def open_count(state: SessionState) -> int:
    return sum(not is_reviewed(state, f) for f in state.order)
