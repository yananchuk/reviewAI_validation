"""Data for the Excel-like workbook view: colours, number formats and per-sheet payloads.

Pure Python, no Streamlit. Values are the saved results in the workbook; nothing is recalculated."""

from __future__ import annotations

import colorsys
import io
import re
import zipfile
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal
from functools import lru_cache
from typing import Any
from xml.etree import ElementTree as ET  # noqa: S405 - parts of an already size-checked package

from openpyxl.styles.colors import COLOR_INDEX
from openpyxl.utils import get_column_letter

from review_app import limits
from review_app.addresses import normalize_cell, parse_cell
from review_app.workbook import _formula_text, _jsonable, open_reader

_NS_A = {"a": "http://schemas.openxmlformats.org/drawingml/2006/main"}
_HEX = re.compile(r"^[0-9A-F]{6}$")


# ---------------------------------------------------------------- colours
# Ported from src/workpaper-quality/scripts/wp_reader.py (_theme_colors, _apply_tint, _color) so the web app does
# not depend on the skill.


def _apply_tint(rgb_hex: str, tint: float) -> str:
    r, g, b = (int(rgb_hex[i : i + 2], 16) / 255 for i in (0, 2, 4))
    h, lum, s = colorsys.rgb_to_hls(r, g, b)
    lum = lum * (1 + tint) if tint < 0 else lum * (1 - tint) + tint
    r, g, b = colorsys.hls_to_rgb(h, max(0.0, min(1.0, lum)), s)
    return "".join(f"{round(c * 255):02X}" for c in (r, g, b))


def theme_colors(data: bytes) -> list[str]:
    """Theme colours in Excel index order (lt1, dk1, lt2, dk2, accent1..6, hlink, folHlink); [] if unavailable."""
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            root = ET.fromstring(zf.read("xl/theme/theme1.xml"))  # noqa: S314
    except (zipfile.BadZipFile, KeyError, ET.ParseError):
        return []
    scheme = root.find(".//a:clrScheme", _NS_A)
    colors: list[str] = []
    for child in scheme if scheme is not None else []:
        srgb, sys_clr = child.find("a:srgbClr", _NS_A), child.find("a:sysClr", _NS_A)
        value = srgb.get("val") if srgb is not None else sys_clr.get("lastClr") if sys_clr is not None else None
        colors.append((value or "000000").upper())
    if len(colors) >= 4:  # the scheme lists dk1, lt1, dk2, lt2
        colors = [colors[1], colors[0], colors[3], colors[2], *colors[4:]]
    return [c if _HEX.match(c) else "000000" for c in colors]


def resolve_color(color: Any, theme: list[str]) -> str | None:
    """openpyxl Color -> 'RRGGBB', or None when it cannot be resolved."""
    if color is None:
        return None
    ctype = getattr(color, "type", None)
    tint = float(getattr(color, "tint", 0) or 0)
    rgb: str | None = None
    if ctype == "rgb" and isinstance(color.rgb, str):
        rgb = color.rgb[-6:].upper()
    elif ctype == "theme" and color.theme is not None and 0 <= color.theme < len(theme):
        rgb = theme[color.theme]
    elif ctype == "indexed" and color.indexed is not None and 0 <= color.indexed < len(COLOR_INDEX):
        rgb = COLOR_INDEX[color.indexed][-6:].upper()
    if rgb is None or not _HEX.match(rgb):
        return None
    return _apply_tint(rgb, tint) if tint else rgb


# ---------------------------------------------------------------- number formats

_DATE_FORMATS = {"dd.mm.yyyy": "%d.%m.%Y", "yyyy-mm-dd": "%Y-%m-%d"}
_NUMBER = re.compile(r"^(#,##0|0)(\.0+)?$")
_PERCENT = re.compile(r"^0(\.0+)?%$")


def _general(value: float | int) -> str:
    if isinstance(value, int) or float(value).is_integer():
        return str(int(value))
    return f"{value:.10g}"


def _number(value: float, pattern: str) -> str | None:
    m = _NUMBER.match(pattern)
    if not m:
        return None
    decimals = len(m.group(2)) - 1 if m.group(2) else 0
    # Excel rounds half away from zero; Python's format() rounds half to even.
    rounded = Decimal(repr(float(value))).quantize(Decimal(1).scaleb(-decimals), rounding=ROUND_HALF_UP)
    return f"{rounded:{',' if m.group(1) == '#,##0' else ''}.{decimals}f}"


def format_value(value: Any, number_format: str | None) -> str:
    """What Excel would display for common formats; any other format shows the raw value."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    fmt = (number_format or "General").strip()
    if isinstance(value, (datetime, date)):
        if fmt in _DATE_FORMATS:
            return value.strftime(_DATE_FORMATS[fmt])
        if fmt == "d/m/yyyy":
            return f"{value.day}/{value.month}/{value.year}"
        return value.isoformat()
    if not isinstance(value, (int, float)):
        return str(value)
    if fmt == "General":
        return _general(value)
    sections = fmt.split(";")
    positive = sections[0]
    if value < 0 and len(sections) > 1:
        negative = sections[1]
        bracketed = negative.startswith("(") and negative.endswith(")")
        core = negative.strip("()").removeprefix("[Red]").removeprefix("-")
        text = _number(abs(value), core)
        if text is not None:
            return f"({text})" if bracketed else f"-{text}"
        return _general(value)
    pct = _PERCENT.match(positive)
    if pct:
        return _number(value * 100, "0" + (pct.group(1) or "")) + "%"
    text = _number(value, positive)
    return text if text is not None else _general(value)


# ---------------------------------------------------------------- sheet payload

_FONT_NAME = re.compile(r"^[A-Za-z0-9 \-]{1,40}$")
_BORDERS = {
    "thin": "1px solid", "hair": "1px dotted", "dotted": "1px dotted", "dashed": "1px dashed",
    "dashDot": "1px dashed", "dashDotDot": "1px dashed", "medium": "2px solid", "mediumDashed": "2px dashed",
    "mediumDashDot": "2px dashed", "mediumDashDotDot": "2px dashed", "slantDashDot": "2px dashed",
    "thick": "3px solid", "double": "3px double",
}  # fmt: skip
_H_ALIGN = {"left": "left", "center": "center", "centerContinuous": "center", "right": "right",
            "justify": "justify", "distributed": "center", "fill": "left"}  # fmt: skip
_V_ALIGN = {"top": "top", "center": "middle", "bottom": "bottom", "justify": "middle", "distributed": "middle"}
DEFAULT_COL_WIDTH = 8.43
DEFAULT_ROW_HEIGHT = 15.0


def _css(cell, theme: list[str]) -> dict[str, str]:
    """CSS for one cell style, from validated values only (colours as #RRGGBB, numbers, allow-listed font names)."""
    css: dict[str, str] = {}
    font = cell.font
    if font is not None:
        if isinstance(font.name, str) and _FONT_NAME.match(font.name):
            css["font-family"] = font.name
        if isinstance(font.sz, (int, float)) and 1 <= float(font.sz) <= 409:
            css["font-size"] = f"{float(font.sz):g}pt"
        if font.b:
            css["font-weight"] = "bold"
        if font.i:
            css["font-style"] = "italic"
        if font.u:
            css["text-decoration"] = "underline"
        color = resolve_color(font.color, theme)
        if color:
            css["color"] = f"#{color}"
    fill = cell.fill
    if fill is not None and fill.fill_type == "solid":
        color = resolve_color(fill.fgColor, theme)
        if color:
            css["background-color"] = f"#{color}"
    border = cell.border
    if border is not None:
        for side in ("top", "right", "bottom", "left"):
            s = getattr(border, side, None)
            if s is not None and s.style in _BORDERS:
                color = resolve_color(s.color, theme) or "000000"
                css[f"border-{side}"] = f"{_BORDERS[s.style]} #{color}"
    al = cell.alignment
    if al is not None:
        if al.horizontal in _H_ALIGN:
            css["text-align"] = _H_ALIGN[al.horizontal]
        if al.vertical in _V_ALIGN:
            css["vertical-align"] = _V_ALIGN[al.vertical]
        if al.wrap_text:
            css["white-space"] = "pre-wrap"
    return css


def _format_text(cell, theme: list[str]) -> str:
    """One line describing a cell's formatting for the formula bar, e.g. 'Arial 10, bold · fill #FFFF00 · #,##0.00'."""
    parts = []
    font = cell.font
    if font is not None:
        name = font.name if isinstance(font.name, str) and _FONT_NAME.match(font.name) else "default font"
        size = f" {float(font.sz):g}" if isinstance(font.sz, (int, float)) and 1 <= float(font.sz) <= 409 else ""
        styles = [w for w, on in (("bold", font.b), ("italic", font.i), ("underline", font.u)) if on]
        text = name + size + ("" if not styles else ", " + ", ".join(styles))
        color = resolve_color(font.color, theme)
        if color and color != "000000":
            text += f", colour #{color}"
        parts.append(text)
    fill = cell.fill
    if fill is not None and fill.fill_type == "solid":
        color = resolve_color(fill.fgColor, theme)
        if color:
            parts.append(f"fill #{color}")
    if cell.number_format and cell.number_format != "General":
        parts.append(f"format {cell.number_format}")
    al = cell.alignment
    if al is not None:
        if al.horizontal in _H_ALIGN:
            parts.append(f"align {al.horizontal}")
        if al.wrap_text:
            parts.append("wrap text")
    return " · ".join(parts)


def _col_widths(ws, last_col: int) -> dict[int, float]:
    default = float(ws.sheet_format.defaultColWidth or DEFAULT_COL_WIDTH)
    widths = dict.fromkeys(range(1, last_col + 1), default)
    for dim in ws.column_dimensions.values():
        if dim.width and dim.min and dim.max:
            for c in range(dim.min, min(dim.max, last_col) + 1):
                widths[c] = float(dim.width)
    return widths


def _used_range(ws) -> tuple[int, int]:
    rows, cols = ws.max_row or 1, ws.max_column or 1
    for mr in ws.merged_cells.ranges:
        rows, cols = max(rows, mr.max_row), max(cols, mr.max_col)
    return rows, cols


def _cell_entry(cell, saved_value: Any) -> dict[str, Any]:
    """Display data of one cell: text (saved result for formulas), formula, style id, numeric flag."""
    entry: dict[str, Any] = {"t": "", "f": None, "s": cell.style_id}
    if cell.data_type == "f":
        entry["f"] = _formula_text(cell.value)
        if saved_value is None:
            entry["t"], entry["g"] = entry["f"] or "", 1
        else:
            shown = saved_value if isinstance(saved_value, (datetime, date)) else _jsonable(saved_value)
            entry["t"] = format_value(shown, cell.number_format)
    else:
        entry["t"] = format_value(cell.value, cell.number_format)
    shown = saved_value if cell.data_type == "f" else cell.value
    if isinstance(shown, (int, float)) and not isinstance(shown, bool):
        entry["n"] = 1
    return entry


def _span(total: int, size: int, focus: int | None) -> tuple[int, int]:
    """A window of `size` positions out of 1..total, centred on `focus` where possible."""
    if total <= size:
        return 1, total
    first = max(1, min((focus or 1) - size // 2, total - size + 1))
    return first, first + size - 1


@lru_cache(maxsize=16)
def sheet_payload(
    data: bytes, sheet: str, focus_row: int | None = None, focus_col: int | None = None
) -> dict[str, Any]:
    """Everything the workbook pane needs to draw one sheet (JSON-serializable; texts are data, never markup).

    Sheets above SHEET_RENDER_MAX_CELLS are cut to a window around the focus cell: LARGE_SHEET_WINDOW_ROWS rows and
    as many columns as the cell budget allows, so a stray far-right or far-down cell cannot blow up the grid."""
    reader = open_reader(data)
    ws = reader.worksheet(sheet)
    if ws is None:
        return {"sheet": sheet, "rows": [], "cols": [], "hidden_rows": [], "hidden_cols": [], "merges": [],
                "cells": {}, "styles": {}, "formats": {}, "window": None}  # fmt: skip
    values = reader.values[sheet]
    theme = theme_colors(data)
    total_rows, total_cols = _used_range(ws)
    first_row, last_row, first_col, last_col, window = 1, total_rows, 1, total_cols, None
    if total_rows * total_cols > limits.SHEET_RENDER_MAX_CELLS:
        first_row, last_row = _span(total_rows, limits.LARGE_SHEET_WINDOW_ROWS, focus_row)
        budget = max(1, limits.SHEET_RENDER_MAX_CELLS // (last_row - first_row + 1))
        first_col, last_col = _span(total_cols, budget, focus_col)
        window = {"first_row": first_row, "last_row": last_row, "total_rows": total_rows,
                  "first_col": first_col, "last_col": last_col, "total_cols": total_cols}  # fmt: skip

    hidden_rows, hidden_cols = reader.hidden_rows_cols(sheet)
    default_height = float(ws.sheet_format.defaultRowHeight or DEFAULT_ROW_HEIGHT)
    rows = []
    for r in range(first_row, last_row + 1):
        dim = ws.row_dimensions.get(r)
        rows.append({"r": r, "h": float(dim.ht) if dim is not None and dim.ht else default_height})
    widths = _col_widths(ws, last_col)
    cols = [{"c": c, "letter": get_column_letter(c), "w": widths[c]} for c in range(first_col, last_col + 1)]

    styles: dict[str, dict[str, str]] = {}
    formats: dict[str, str] = {}
    cells: dict[str, dict[str, Any]] = {}

    def add(r: int, c: int, cell, at: str | None = None) -> None:
        if str(cell.style_id) not in styles:
            styles[str(cell.style_id)] = _css(cell, theme)
            formats[str(cell.style_id)] = _format_text(cell, theme)
        saved = values._cells.get((r, c)) if cell.data_type == "f" else None  # noqa: SLF001
        cells[at or f"{get_column_letter(c)}{r}"] = _cell_entry(cell, saved.value if saved is not None else None)

    for (r, c), cell in ws._cells.items():  # noqa: SLF001 - read without creating cells
        if first_row <= r <= last_row and first_col <= c <= last_col and (cell.value is not None or cell.style_id):
            add(r, c, cell)

    # Merges, clipped to the window, start at their first visible cell; that cell shows the merge's content
    # (Excel shows a merged value even when the merge's top-left row or column is hidden).
    merges = []
    for mr in ws.merged_cells.ranges:
        vis_rows = [r for r in range(max(mr.min_row, first_row), min(mr.max_row, last_row) + 1) if r not in hidden_rows]
        vis_cols = [c for c in range(max(mr.min_col, first_col), min(mr.max_col, last_col) + 1) if c not in hidden_cols]
        if not vis_rows or not vis_cols:
            continue
        r1, c1 = vis_rows[0], vis_cols[0]
        merges.append({"r1": r1, "c1": c1, "r2": vis_rows[-1], "c2": vis_cols[-1]})
        if (r1, c1) != (mr.min_row, mr.min_col):
            top_left = ws._cells.get((mr.min_row, mr.min_col))  # noqa: SLF001
            if top_left is not None:
                add(mr.min_row, mr.min_col, top_left, at=f"{get_column_letter(c1)}{r1}")
    return {
        "sheet": sheet,
        "rows": rows,
        "cols": cols,
        "hidden_rows": sorted(r for r in hidden_rows if first_row <= r <= last_row),
        "hidden_cols": sorted(c for c in hidden_cols if first_col <= c <= last_col),
        "merges": merges,
        "cells": cells,
        "styles": styles,
        "formats": formats,
        "window": window,
    }


def visible_cell(payload: dict[str, Any], ref: str) -> str:
    """The cell where `ref` is shown: itself if visible, else the next visible row/column (or the previous one at
    the edge), so findings in hidden rows, columns or merge interiors still get a flag and a target."""
    pos = parse_cell(ref)
    if pos is None or not payload.get("rows") or not payload.get("cols"):
        return ref
    col, row = pos
    hidden_r, hidden_c = set(payload["hidden_rows"]), set(payload["hidden_cols"])
    rows = [r["r"] for r in payload["rows"] if r["r"] not in hidden_r]
    cols = [c["c"] for c in payload["cols"] if c["c"] not in hidden_c]
    if not rows or not cols:
        return ref
    row = next((r for r in rows if r >= row), rows[-1])
    col = next((c for c in cols if c >= col), cols[-1])
    for m in payload["merges"]:
        if m["r1"] <= row <= m["r2"] and m["c1"] <= col <= m["c2"]:
            row, col = m["r1"], m["c1"]
    return f"{get_column_letter(col)}{row}"


def sheet_tabs(data: bytes) -> list[dict[str, Any]]:
    """Sheets in workbook order: name, state and tab colour (#RRGGBB or None)."""

    reader = open_reader(data)
    theme = theme_colors(data)
    tabs = []
    for entry in reader.entries:
        ws = reader.worksheet(entry.name)
        color = resolve_color(ws.sheet_properties.tabColor, theme) if ws is not None else None
        tabs.append({"name": entry.name, "state": entry.state, "color": f"#{color}" if color else None})
    return tabs


def anchor_of(data: bytes, sheet: str, cell: str) -> str:
    """The top-left cell of the merged range that contains `cell`, otherwise `cell` itself."""

    ref = normalize_cell(cell) or cell
    pos = parse_cell(ref)
    ws = open_reader(data).worksheet(sheet)
    if ws is None or pos is None:
        return ref
    col, row = pos
    for mr in ws.merged_cells.ranges:
        if mr.min_col <= col <= mr.max_col and mr.min_row <= row <= mr.max_row:
            return f"{get_column_letter(mr.min_col)}{mr.min_row}"
    return ref
