"""Flatten rules → findings and decide, per Quick Fix, whether its change is deterministic.

The classification only looks at the report. Whether the workbook still matches (stale check), the sheet is
protected or the target is merged is decided later, against the actual workbook, before anything is written."""

from __future__ import annotations

from typing import Any

from review_app import limits
from review_app.addresses import Bounds, col_index, is_valid_sheet_name, normalize_cell, parse_cell, parse_range
from review_app.format_spec import has_layout_words, parse_column_width, parse_font_spec
from review_app.models import (
    Location,
    Manual,
    NormalizedFinding,
    ProposedChange,
    ReportMeta,
    Resolution,
    RuleMeta,
    SetColumnWidth,
    SetFont,
    SetFormula,
    SetValue,
    ValidationIssue,
)

MANUAL_NOTE = "This suggested change requires manual review."
# Cases whose 'after' is a formula although 'before' is a typed value (rule QLT-MTH-02).
FORMULA_CASES = frozenset({"typed_value_replacing_formula"})


def _manual(reason: str) -> Manual:
    return Manual(reason=f"{MANUAL_NOTE} {reason}")


def same_value(a: Any, b: Any) -> bool:
    """Equality that keeps types apart: 120 != '120', True != 1."""
    if isinstance(a, bool) or isinstance(b, bool):
        return type(a) is type(b) and a == b
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return float(a) == float(b)
    return type(a) is type(b) and a == b


def _primary(change: ProposedChange, locations: list[Location]) -> Location | None:
    return next(
        (loc for loc in locations if loc.kind in {"cell", "range"} and loc.sheet == change.sheet and loc.ref), None
    )


def classify(resolution: Resolution, case: str, change: ProposedChange | None, locations: list[Location]):
    """Return the Operation for a finding: an automatic one only when target and operation are deterministic."""
    if resolution == Resolution.FOLLOW_UP:
        return Manual(reason="Follow-up findings need human judgement and are never changed automatically.")
    if change is None:
        return _manual("The report proposes no change for this finding.")
    if not change.sheet or not change.cell:
        return _manual("The proposed change has no worksheet or cell.")
    if not is_valid_sheet_name(change.sheet):
        return _manual("The proposed worksheet name is not valid in Excel.")
    target = parse_range(change.cell)
    if target is None:
        return _manual(f"The proposed target '{change.cell}' is not a valid cell or range.")
    primary = _primary(change, locations)
    if primary is None:
        return _manual("The change does not match a reported location on this worksheet.")
    before, after = change.before, change.after

    # Column width: 'column H width 2.0' -> 'column H width 15.375'.
    bw, aw = parse_column_width(before), parse_column_width(after)
    if bw and aw:
        if bw[0] != aw[0]:
            return _manual("The before and after widths refer to different columns.")
        if target.min_col != target.max_col or target.min_col != col_index(bw[0]):
            return _manual("The change target is not in the column whose width is changed.")
        return SetColumnWidth(sheet=change.sheet, column=bw[0], before=bw[1], after=aw[1])
    if bw or aw:
        return _manual("The column-width change could not be read unambiguously.")

    # Font and wrap: both sides must parse under the strict grammar.
    bf, af = parse_font_spec(before), parse_font_spec(after)
    if bf and af:
        if target.size > limits.MAX_FONT_TARGET_CELLS:
            return _manual(f"The target has more than {limits.MAX_FONT_TARGET_CELLS} cells.")
        if primary.range:
            prim = parse_range(primary.range)
            if prim is None or prim != target:
                return _manual("The change target is not the same range as the reported location.")
        else:
            cell = parse_cell(primary.cell)
            if cell is None or not target.contains(*cell):
                return _manual("The change target does not include the reported cell.")
        return SetFont(sheet=change.sheet, target=target.ref, before=bf, after=af)
    if bf or af:
        return _manual("The formatting change could not be read unambiguously.")
    if has_layout_words(before) or has_layout_words(after):
        return _manual("The change describes a layout adjustment, which is not automated.")

    # Value and formula changes: exactly one reported cell, whose quoted value is the 'before' value.
    if not target.is_single:
        return _manual("A value or formula can only be written to a single cell.")
    if primary.kind != "cell" or normalize_cell(primary.cell or "") != target.ref:
        return _manual("The change target is not the reported cell.")
    if before is None or not primary.has_value or not same_value(primary.value, before):
        return _manual(
            "The reported value does not match the proposed 'before' value, so the change cannot be verified."
        )
    if isinstance(after, str) and after.startswith("="):
        is_formula = (isinstance(before, str) and before.startswith("=")) or case in FORMULA_CASES
        if not is_formula:
            return _manual("The proposed text starts with '=' but the report does not identify it as a formula.")
        if len(after.strip()) < 2:
            return _manual("The proposed formula is empty.")
        return SetFormula(sheet=change.sheet, cell=target.ref, before=before, after=after)
    if isinstance(after, bool) or isinstance(after, (int, float)) or (isinstance(after, str) and after != ""):
        return SetValue(sheet=change.sheet, cell=target.ref, before=before, after=after)
    return _manual("The proposed change type is not supported.")


def _location(raw: dict[str, Any]) -> Location:
    fields = {k: raw.get(k) for k in Location.model_fields if k in raw and k != "has_value"}
    return Location(**fields, has_value="value" in raw)


def _change(raw: Any) -> ProposedChange | None:
    if not isinstance(raw, dict):
        return None
    return ProposedChange(**{k: raw.get(k) for k in ("sheet", "cell", "before", "after")})


def normalize(report: dict[str, Any]) -> tuple[ReportMeta, list[NormalizedFinding], list[ValidationIssue]]:
    """Flatten a schema-valid 0.3 report into findings ordered as in the report."""
    completion = report.get("completion") or {}
    meta = ReportMeta(
        schema_version=report["schema_version"],
        skill_version=report["skill_version"],
        run_id=report["run_id"],
        workbook=report["workbook"],
        reviewed_at=report["reviewed_at"],
        workbook_unchanged=report["workbook_unchanged"],
        completion_status=completion.get("status"),
        rules_not_completed=list(completion.get("rules_not_completed") or []),
    )
    findings: list[NormalizedFinding] = []
    for rule in report["rules"]:
        rule_meta = RuleMeta(
            rule_id=rule["rule_id"],
            status=rule["status"],
            title=rule.get("title", ""),
            theme=rule.get("theme", ""),
            what_is_checked=rule.get("what_is_checked", ""),
            warnings=list(rule.get("warnings") or []),
            not_tested_parts=list(rule.get("not_tested_parts") or []),
        )
        for raw in rule.get("findings") or []:
            locations = [_location(loc) for loc in raw.get("locations") or [] if isinstance(loc, dict)]
            change = _change(raw.get("change"))
            resolution = Resolution(raw["resolution"])
            findings.append(
                NormalizedFinding(
                    id=raw["id"],
                    fingerprint=raw.get("fingerprint", ""),
                    rule=rule_meta,
                    resolution=resolution,
                    case=raw.get("case", "default"),
                    message=raw.get("message", ""),
                    locations=locations,
                    hidden=bool(raw.get("hidden", False)),
                    change=change,
                    fallback_reason=raw.get("fallback_reason"),
                    operation=classify(resolution, raw.get("case", "default"), change, locations),
                    duplicates=list(raw.get("possible_duplicate_of") or []),
                    check_problems=list(raw.get("check_problems") or []),
                    raw=raw,
                )
            )
    ids = {f.id for f in findings}
    warnings: list[ValidationIssue] = []
    for f in findings:
        missing = [d for d in f.duplicates if d not in ids]
        if missing:
            f.duplicates = [d for d in f.duplicates if d in ids]
            f.missing_duplicates = missing
            for d in missing:
                warnings.append(
                    ValidationIssue(
                        level="warning",
                        message=f"{d} referenced by {f.id} as a possible duplicate is not in the report.",
                    )
                )
    return meta, findings, warnings


def target_bounds(op) -> Bounds | None:
    """Cells an automatic operation writes to (None for column widths and manual changes)."""
    if isinstance(op, (SetValue, SetFormula)):
        return parse_range(op.cell)
    if isinstance(op, SetFont):
        return parse_range(op.target)
    return None
