"""Before anything is written: is the target writable, and does it still hold the report's 'before' state?

The check runs against the working copy (the original plus the changes applied so far), so two fixes on the same
cell see each other's results."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from review_app.addresses import format_ref, parse_range
from review_app.models import CellSnapshot, SetColumnWidth, SetFont, SetFormula, SetValue, WorkbookProfile
from review_app.normalizer import same_value
from review_app.package_patch import PatchError, column_width
from review_app.workbook import WorkbookReader

STALE_MESSAGE = "The workbook has changed since this report was created."


@dataclass
class Precheck:
    blocked: str | None = None  # cannot be applied automatically at all
    stale: bool = False
    stale_details: list[str] = field(default_factory=list)
    current: dict[str, Any] = field(default_factory=dict)  # state before the change, kept for the log


def norm_formula(text: Any) -> str:
    return "".join(str(text or "").split()).lstrip("=").upper()


def _show(value: Any) -> str:
    return "(empty)" if value is None or value == "" else repr(value) if isinstance(value, str) else str(value)


def _font_of(s: CellSnapshot) -> dict[str, Any]:
    return {"font": s.font_name, "size": s.font_size, "bold": s.bold, "wrap": s.wrap}


def precheck(op, profile: WorkbookProfile, working: bytes, reader: WorkbookReader) -> Precheck:
    """`reader` must be a reader of `working`."""
    if not profile.writable:
        return Precheck(
            blocked="Changes cannot be written to this workbook: " + "; ".join(profile.not_writable_reasons) + "."
        )
    sheet = profile.sheet(op.sheet)
    if sheet is None or not reader.is_worksheet(op.sheet):
        return Precheck(blocked=f"Sheet '{op.sheet}' is not a worksheet in the uploaded workbook.")
    if sheet.protected:
        return Precheck(blocked="This sheet is protected. Apply the change in Excel and mark it as manually resolved.")

    if isinstance(op, SetColumnWidth):
        try:
            width, shared = column_width(working, op.sheet, op.column)
        except PatchError as exc:
            return Precheck(blocked=str(exc))
        if shared:
            return Precheck(blocked=f"Column {op.column} shares its settings with other columns; change it in Excel.")
        check = Precheck(current={"column": op.column, "width": width})
        if width is None or abs(width - op.before) > 0.01:
            check.stale = True
            check.stale_details.append(
                f"Column {op.column} width is {'the default' if width is None else f'{width:g}'}, "
                f"the report expected {op.before:g}."
            )
        return check

    refs = parse_range(op.target).cells() if isinstance(op, SetFont) else [op.cell]
    check = Precheck()
    for ref in refs:
        s = reader.snapshot(op.sheet, ref)
        if not s.exists:
            return Precheck(blocked=f"{format_ref(op.sheet, ref)} is not a valid cell.")
        if s.merged_range and not s.merged_range.startswith(ref + ":"):
            return Precheck(
                blocked=f"{format_ref(op.sheet, ref)} is inside merged range {s.merged_range}; change it in Excel."
            )
        where = format_ref(op.sheet, ref)
        if isinstance(op, SetFont):
            check.current[ref] = _font_of(s)
            b = op.before
            if b.name is not None and (s.font_name or "").casefold() != b.name.casefold():
                check.stale_details.append(f"{where} font is {s.font_name}, the report expected {b.name}.")
            if b.size is not None and s.font_size != b.size:
                check.stale_details.append(f"{where} font size is {s.font_size:g}, the report expected {b.size:g}.")
            if b.bold is not None and s.bold != b.bold:
                check.stale_details.append(
                    f"{where} is {'bold' if s.bold else 'not bold'}, the report expected otherwise."
                )
            if b.wrap is not None and s.wrap != b.wrap:
                check.stale_details.append(
                    f"{where} text wrapping is {'on' if s.wrap else 'off'}, the report expected otherwise."
                )
        else:
            check.current[ref] = {"value": s.value, "formula": s.formula, "cached": s.cached}
            if isinstance(op, SetFormula) and isinstance(op.before, str) and op.before.startswith("="):
                if norm_formula(s.formula) != norm_formula(op.before):
                    check.stale_details.append(
                        f"{where} holds {_show(s.formula or s.value)}, the report expected {op.before}."
                    )
            elif s.formula is not None or not same_value(s.value, op.before):
                check.stale_details.append(
                    f"{where} holds {_show(s.formula or s.value)}, the report expected {_show(op.before)}."
                )
    check.stale = bool(check.stale_details)
    return check


def describe_proposed(op) -> str:
    if isinstance(op, SetValue):
        return f"Set {format_ref(op.sheet, op.cell)} to {_show(op.after)}"
    if isinstance(op, SetFormula):
        return f"Set the formula of {format_ref(op.sheet, op.cell)} to {op.after}"
    if isinstance(op, SetFont):
        where = format_ref(op.sheet, op.target)
        return f"Change the formatting of {where} from {op.before.describe()} to {op.after.describe()}"
    if isinstance(op, SetColumnWidth):
        return f"Change the width of column {op.column} on {op.sheet} from {op.before:g} to {op.after:g}"
    return ""


def target_label(op) -> str:
    if isinstance(op, (SetValue, SetFormula)):
        return format_ref(op.sheet, op.cell)
    if isinstance(op, SetFont):
        return format_ref(op.sheet, op.target)
    if isinstance(op, SetColumnWidth):
        return f"{op.sheet} column {op.column}"
    return ""
