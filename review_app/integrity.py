"""Checks run on the generated workbook before it may be downloaded. Any failed blocking check withholds the
modified workbook; the review log stays available."""

from __future__ import annotations

import hashlib
import io
import zipfile
from collections.abc import Sequence
from pathlib import PurePath

from openpyxl.utils import get_column_letter

from review_app.addresses import parse_range
from review_app.models import (
    IntegrityResult,
    SetColumnWidth,
    SetFont,
    SetFormula,
    SetValue,
    VisibilityRequest,
)
from review_app.package_patch import BuildResult, column_width
from review_app.workbook import WorkbookReader, sha256


def _norm_formula(text: str | None) -> str:
    return "".join((text or "").split()).lstrip("=").upper()


def _pass(check: str, detail: str = "") -> IntegrityResult:
    return IntegrityResult(check=check, status="pass", detail=detail)


def _fail(check: str, detail: str) -> IntegrityResult:
    return IntegrityResult(check=check, status="fail", detail=detail, blocks_download=True)


def _parts(data: bytes) -> dict[str, str]:
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        return {name: hashlib.sha256(zf.read(name)).hexdigest() for name in zf.namelist()}


def run_checks(
    *,
    original: bytes,
    original_sha256: str,
    input_name: str,
    output_name: str,
    result: BuildResult,
    changes: Sequence[SetValue | SetFormula | SetFont | SetColumnWidth],
    reveals: Sequence[VisibilityRequest] = (),
) -> list[IntegrityResult]:
    out: list[IntegrityResult] = []

    out.append(
        _pass("Source workbook unchanged")
        if sha256(original) == original_sha256
        else _fail("Source workbook unchanged", "The uploaded workbook bytes changed during the session.")
    )
    try:
        before = WorkbookReader(original)
        after = WorkbookReader(result.data)
        out.append(_pass("Output workbook reopens"))
    except Exception as exc:  # noqa: BLE001 - any failure to reopen blocks the download
        out.append(_fail("Output workbook reopens", f"The output could not be reopened: {type(exc).__name__}."))
        return out

    in_ext, out_ext = PurePath(input_name).suffix.lower(), PurePath(output_name).suffix.lower()
    out.append(
        _pass("Extension kept", out_ext)
        if in_ext == out_ext
        else _fail("Extension kept", f"Input is {in_ext}, output is {out_ext}.")
    )

    names_before = [e.name for e in before.entries]
    names_after = [e.name for e in after.entries]
    out.append(
        _pass("Worksheet names and order", f"{len(names_after)} sheets")
        if names_before == names_after
        else _fail("Worksheet names and order", f"Before: {names_before}; after: {names_after}.")
    )

    revealed = {(r.sheet, r.what, r.ref) for r in reveals}
    state_diffs = []
    for e_before, e_after in zip(before.entries, after.entries, strict=False):
        expected = "visible" if (e_before.name, "sheet", e_before.name) in revealed else e_before.state
        if e_after.state != expected:
            state_diffs.append(f"{e_before.name}: {e_before.state} -> {e_after.state}")
        rows_b, cols_b = before.hidden_rows_cols(e_before.name)
        rows_a, cols_a = after.hidden_rows_cols(e_after.name)
        exp_rows = {r for r in rows_b if (e_before.name, "row", str(r)) not in revealed}
        exp_cols = {c for c in cols_b if (e_before.name, "column", get_column_letter(c)) not in revealed}
        if rows_a != exp_rows or cols_a != exp_cols:
            state_diffs.append(f"{e_before.name}: hidden rows or columns changed")
    out.append(
        _pass("Visibility preserved", f"{len(reveals)} requested reveal(s)")
        if not state_diffs
        else _fail("Visibility preserved", "; ".join(state_diffs))
    )

    targets = {(c.sheet, c.cell) for c in changes if isinstance(c, (SetValue, SetFormula))}
    content_diffs = []
    for name in names_before:
        dump_b, dump_a = before.dump(name), after.dump(name)
        for ref in set(dump_b) | set(dump_a):
            if (name, ref) in targets:
                continue
            if dump_b.get(ref) != dump_a.get(ref):
                content_diffs.append(f"{name}!{ref}")
    out.append(
        _pass("Formulas and values outside approved targets unchanged")
        if not content_diffs
        else _fail(
            "Formulas and values outside approved targets unchanged",
            f"{len(content_diffs)} unexpected change(s), for example {', '.join(sorted(content_diffs)[:5])}.",
        )
    )

    target_problems = []
    final: dict[tuple, SetValue | SetFormula | SetFont | SetColumnWidth] = {}
    for c in changes:
        key = (c.sheet, getattr(c, "cell", None) or getattr(c, "target", None) or c.column, c.op)
        final[key] = c
    for c in final.values():
        if isinstance(c, SetValue):
            snap = after.snapshot(c.sheet, c.cell)
            if snap.formula is not None or snap.value != c.after or type(snap.value) is not type(c.after):
                target_problems.append(f"{c.sheet}!{c.cell} does not hold the approved value")
        elif isinstance(c, SetFormula):
            snap = after.snapshot(c.sheet, c.cell)
            if _norm_formula(snap.formula) != _norm_formula(c.after):
                target_problems.append(f"{c.sheet}!{c.cell} does not hold the approved formula")
        elif isinstance(c, SetFont):
            for ref in parse_range(c.target).cells():
                s = after.snapshot(c.sheet, ref)
                spec = c.after
                if (
                    (spec.name is not None and (s.font_name or "").casefold() != spec.name.casefold())
                    or (spec.size is not None and s.font_size != spec.size)
                    or (spec.bold is not None and s.bold != spec.bold)
                    or (spec.wrap is not None and s.wrap != spec.wrap)
                ):
                    target_problems.append(f"{c.sheet}!{ref} does not have the approved formatting")
        elif isinstance(c, SetColumnWidth):
            width, _ = column_width(result.data, c.sheet, c.column)
            if width is None or abs(width - c.after) > 1e-6:
                target_problems.append(f"Column {c.column} on {c.sheet} does not have the approved width")
    out.append(
        _pass("Approved targets hold the approved change", f"{len(final)} change(s)")
        if not target_problems
        else _fail("Approved targets hold the approved change", "; ".join(target_problems[:5]))
    )

    merge_diffs = [
        n
        for n in names_before
        if before.worksheet(n) is not None
        and sorted(m.coord for m in before.worksheet(n).merged_cells.ranges)
        != sorted(m.coord for m in after.worksheet(n).merged_cells.ranges)
    ]
    out.append(
        _pass("Merged ranges preserved")
        if not merge_diffs
        else _fail("Merged ranges preserved", f"Changed on: {', '.join(merge_diffs)}.")
    )
    out.append(
        _pass("Defined names preserved", f"{len(before.names)} name(s)")
        if before.names == after.names
        else _fail("Defined names preserved", "The defined names differ.")
    )

    parts_b, parts_a = _parts(original), _parts(result.data)
    expected_parts = set(parts_b) - result.removed_parts
    unexpected = []
    if set(parts_a) != expected_parts:
        unexpected.append(
            f"added {sorted(set(parts_a) - expected_parts)}, missing {sorted(expected_parts - set(parts_a))}"
        )
    changed = [p for p in expected_parts & set(parts_a) if p not in result.edited_parts and parts_a[p] != parts_b[p]]
    if changed:
        unexpected.append(f"changed {sorted(changed)}")
    out.append(
        _pass(
            "All other package parts byte-identical", f"{len(expected_parts) - len(result.edited_parts)} part(s) copied"
        )
        if not unexpected
        else _fail("All other package parts byte-identical", "; ".join(unexpected))
    )
    if "xl/vbaProject.bin" in parts_b:
        out.append(
            _pass("VBA project preserved")
            if parts_a.get("xl/vbaProject.bin") == parts_b["xl/vbaProject.bin"]
            else _fail("VBA project preserved", "The VBA project differs from the original.")
        )

    if result.removed_parts:
        out.append(_pass("Calculation chain removed", "Excel rebuilds it when the workbook is opened."))
    if "xl/styles.xml" in result.edited_parts:
        out.append(_pass("Cell formats added", "New fonts or cell formats were appended; existing ones are unchanged."))
    if result.formula_cells:
        out.append(
            IntegrityResult(
                check="Formula results",
                status="warn",
                detail="Edited formulas have no saved result. Excel recalculates them when the workbook is opened.",
            )
        )
    return out


def download_allowed(results: Sequence[IntegrityResult]) -> bool:
    return bool(results) and not any(r.blocks_download for r in results)
