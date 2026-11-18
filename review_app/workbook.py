"""Read-only access to the workbook: profile, cell snapshots and preview grids.

openpyxl is used only to read. Loaded workbooks are cached per content (bytes) and never mutated: cells are peeked
without creating them, so a reader can be shared between reruns."""

from __future__ import annotations

import hashlib
import io
import warnings
import zipfile
from dataclasses import dataclass
from functools import lru_cache
from pathlib import PurePath
from typing import Any

from openpyxl import load_workbook
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.worksheet import Worksheet

from review_app import limits
from review_app.addresses import Bounds, col_index, parse_cell
from review_app.models import CellSnapshot, SheetProfile, WorkbookProfile
from review_app.package_patch import WORKSHEET_TYPE, defined_names, structure_protected, workbook_sheets


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _peek(ws: Worksheet, row: int, col: int):
    """The cell if it exists, without creating it (ws.cell() and ws[...] would add empty cells)."""
    return ws._cells.get((row, col))  # noqa: SLF001 - documented reason above


def _formula_text(value: Any) -> str | None:
    if isinstance(value, str) and value.startswith("="):
        return value
    text = getattr(value, "text", None)  # ArrayFormula
    if isinstance(text, str):
        return text if text.startswith("=") else "=" + text
    return None


def _jsonable(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    return str(value)


def display(value: Any, limit: int = limits.MAX_TEXT_CHARS) -> str:
    """Short, safe text for a preview cell."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    text = str(value)
    return text if len(text) <= limit else text[: limit - 1] + "…"


@dataclass
class PreviewGrid:
    sheet: str
    bounds: Bounds
    rows: list[list[str]]
    row_labels: list[int]
    col_labels: list[str]
    total_cells: int
    truncated: bool
    target: str | None = None


class WorkbookReader:
    def __init__(self, data: bytes):
        self.data = data
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")  # openpyxl warns about extensions it does not read; reading is unaffected
            self.wb = load_workbook(io.BytesIO(data), data_only=False, keep_links=True)
            self.values = load_workbook(io.BytesIO(data), data_only=True, keep_links=True)
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            self.entries = workbook_sheets(zf)
            self.names = defined_names(zf)
            self.structure_protected = structure_protected(zf)
            self.parts = sorted(zf.namelist())
        self._hidden: dict[str, tuple[set[int], set[int]]] = {}

    # ------------------------------------------------------------ sheets

    def worksheet(self, name: str | None) -> Worksheet | None:
        if name is None or name not in self.wb.sheetnames:
            return None
        ws = self.wb[name]
        return ws if isinstance(ws, Worksheet) else None

    def hidden_rows_cols(self, name: str) -> tuple[set[int], set[int]]:
        if name not in self._hidden:
            ws = self.worksheet(name)
            rows: set[int] = set()
            cols: set[int] = set()
            if ws is not None:
                rows = {idx for idx, dim in ws.row_dimensions.items() if dim.hidden}
                for dim in ws.column_dimensions.values():
                    if dim.hidden and dim.min and dim.max:
                        cols.update(range(dim.min, dim.max + 1))
            self._hidden[name] = (rows, cols)
        return self._hidden[name]

    def merged_range(self, ws: Worksheet, col: int, row: int) -> str | None:
        for mr in ws.merged_cells.ranges:
            if mr.min_col <= col <= mr.max_col and mr.min_row <= row <= mr.max_row:
                return mr.coord
        return None

    def profile(self, file_name: str) -> WorkbookProfile:
        sheets = []
        for entry in self.entries:
            ws = self.worksheet(entry.name)
            rows, cols = self.hidden_rows_cols(entry.name)
            sheets.append(
                SheetProfile(
                    name=entry.name,
                    state=entry.state,
                    part=entry.part,
                    hidden_rows=sorted(rows),
                    hidden_columns=[get_column_letter(c) for c in sorted(cols)],
                    merged_ranges=sorted(mr.coord for mr in ws.merged_cells.ranges) if ws is not None else [],
                    protected=bool(ws is not None and ws.protection.sheet),
                )
            )
        reasons = []
        if any(p.startswith("_xmlsignatures/") for p in self.parts):
            reasons.append("the workbook is digitally signed, and any change would invalidate the signature")
        ext = PurePath(file_name).suffix.lower()
        if ext not in limits.WRITABLE_EXTENSIONS:
            reasons.append(f"{ext} files cannot be written")
        return WorkbookProfile(
            file_name=PurePath(file_name).name,
            extension=ext,
            sha256=sha256(self.data),
            sheets=sheets,
            defined_names=self.names,
            structure_protected=self.structure_protected,
            has_vba="xl/vbaProject.bin" in self.parts,
            has_signature=any(p.startswith("_xmlsignatures/") for p in self.parts),
            parts=self.parts,
            writable=not reasons,
            not_writable_reasons=reasons,
        )

    def is_worksheet(self, name: str) -> bool:
        return any(e.name == name and e.rel_type == WORKSHEET_TYPE for e in self.entries)

    # ------------------------------------------------------------ cells

    def snapshot(self, sheet: str, cell: str) -> CellSnapshot:
        ws = self.worksheet(sheet)
        pos = parse_cell(cell)
        if ws is None or pos is None:
            return CellSnapshot(sheet=sheet, cell=cell, exists=False)
        col, row = pos
        state = next((e.state for e in self.entries if e.name == sheet), "visible")
        rows, cols = self.hidden_rows_cols(sheet)
        snap = CellSnapshot(
            sheet=sheet,
            cell=cell,
            sheet_state=state,
            row_hidden=row in rows,
            column_hidden=col in cols,
            merged_range=self.merged_range(ws, col, row),
        )
        c = _peek(ws, row, col)
        if c is None:
            return snap
        # Only a formula cell is a formula: text that merely starts with '=' stays text.
        formula = _formula_text(c.value) if c.data_type == "f" else None
        vc = _peek(self.values[sheet], row, col)
        snap.value = _jsonable(c.value) if formula is None else formula
        snap.formula = formula
        snap.cached = _jsonable(vc.value) if vc is not None else None
        snap.data_type = c.data_type
        snap.number_format = c.number_format
        snap.font_name = c.font.name
        snap.font_size = float(c.font.sz) if c.font.sz is not None else None
        snap.bold = bool(c.font.b)
        snap.italic = bool(c.font.i)
        snap.wrap = bool(c.alignment.wrap_text)
        snap.horizontal = c.alignment.horizontal
        snap.vertical = c.alignment.vertical
        if c.fill is not None and c.fill.fill_type == "solid":
            color = c.fill.fgColor
            snap.fill = color.rgb if color.type == "rgb" else f"{color.type} {color.value}"
        return snap

    def shown_value(self, sheet: str, col: int, row: int) -> Any:
        """What Excel would show: the saved result for formulas, otherwise the value."""
        ws = self.worksheet(sheet)
        c = _peek(ws, row, col) if ws is not None else None
        if c is None:
            return None
        if c.data_type == "f":
            vc = _peek(self.values[sheet], row, col)
            return vc.value if vc is not None and vc.value is not None else _formula_text(c.value)
        return c.value

    def grid(self, sheet: str, bounds: Bounds, limit: int, target: str | None = None) -> PreviewGrid:
        """Values of a rectangle, row by row, stopping after `limit` cells."""
        rows: list[list[str]] = []
        row_labels: list[int] = []
        width = bounds.max_col - bounds.min_col + 1
        max_rows = max(1, limit // width) if width <= limit else 1
        max_cols = min(width, limit)
        for r in range(bounds.min_row, min(bounds.max_row, bounds.min_row + max_rows - 1) + 1):
            rows.append(
                [display(self.shown_value(sheet, c, r)) for c in range(bounds.min_col, bounds.min_col + max_cols)]
            )
            row_labels.append(r)
        return PreviewGrid(
            sheet=sheet,
            bounds=bounds,
            rows=rows,
            row_labels=row_labels,
            col_labels=[get_column_letter(c) for c in range(bounds.min_col, bounds.min_col + max_cols)],
            total_cells=bounds.size,
            truncated=len(rows) * max_cols < bounds.size,
            target=target,
        )

    def context(self, sheet: str, cell: str, radius: int = limits.CONTEXT_RADIUS) -> PreviewGrid | None:
        pos = parse_cell(cell)
        if pos is None or self.worksheet(sheet) is None:
            return None
        col, row = pos
        bounds = Bounds(max(1, col - radius), max(1, row - radius), col + radius, row + radius)
        return self.grid(sheet, bounds, limits.PREVIEW_CELLS, target=cell)

    def column_hidden(self, sheet: str, letter: str) -> bool:
        return col_index(letter) in self.hidden_rows_cols(sheet)[1]

    # ------------------------------------------------------------ integrity dumps

    def dump(self, sheet: str) -> dict[str, Any]:
        """All non-empty stored values and formulas of a sheet: {'B70': '=SUM(B67:B68)', ...}."""
        ws = self.worksheet(sheet)
        if ws is None:
            return {}
        out = {}
        for (row, col), c in ws._cells.items():  # noqa: SLF001 - read without creating cells
            if c.value is not None:
                value = _formula_text(c.value) if c.data_type == "f" else _jsonable(c.value)
                out[f"{get_column_letter(col)}{row}"] = (c.data_type == "f", value)
        return out


@lru_cache(maxsize=4)
def open_reader(data: bytes) -> WorkbookReader:
    """Cached by content; the reader is read-only and safe to share between Streamlit reruns."""
    return WorkbookReader(data)
