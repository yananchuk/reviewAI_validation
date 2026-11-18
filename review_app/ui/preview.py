"""Text summary of reported locations (value, formula, formatting, visibility) for the "Cell details" section."""

from __future__ import annotations

import streamlit as st

from review_app.addresses import format_ref, parse_cell, parse_range
from review_app.models import Location
from review_app.ui.state import esc
from review_app.workbook import WorkbookReader, display


def _visibility(state: str, row_hidden: bool = False, col_hidden: bool = False) -> str:
    parts = []
    if state == "hidden":
        parts.append(":orange-badge[Hidden sheet]")
    elif state == "veryHidden":
        parts.append(":red-badge[Very hidden sheet]")
    if row_hidden:
        parts.append(":orange-badge[Hidden row]")
    if col_hidden:
        parts.append(":orange-badge[Hidden column]")
    return " ".join(parts) or ":gray-badge[Visible]"


def _cell_summary(reader: WorkbookReader, sheet: str, cell: str) -> None:
    s = reader.snapshot(sheet, cell)
    if not s.exists:
        st.warning(f"{format_ref(sheet, cell)} is not a valid cell in this workbook.")
        return
    st.markdown(_visibility(s.sheet_state, s.row_hidden, s.column_hidden))
    lines = []
    if s.formula:
        lines.append(f"**Formula:** `{esc(display(s.formula, 300))}`")
        lines.append(f"**Saved result:** {esc(display(s.cached)) or '(none: Excel calculates it when the file opens)'}")
    else:
        lines.append(f"**Value:** {esc(display(s.value, 300)) or '(empty)'}")
    if s.value is not None or s.formula:
        lines.append(f"**Type:** {esc(type(s.cached if s.formula else s.value).__name__)}")
    fmt = [f"{s.font_name or '?'} {s.font_size:g}" if s.font_size else (s.font_name or "")]
    fmt += [x for x, on in (("bold", s.bold), ("italic", s.italic), ("wrap text", s.wrap)) if on]
    if s.fill:
        fmt.append(f"fill {s.fill}")
    if s.horizontal:
        fmt.append(f"aligned {s.horizontal}")
    if s.number_format and s.number_format != "General":
        fmt.append(f"number format {s.number_format}")
    lines.append(f"**Formatting:** {esc(', '.join(x for x in fmt if x)) or 'default'}")
    if s.merged_range:
        lines.append(f"**Merged range:** {esc(s.merged_range)}")
    st.markdown("  \n".join(lines))


def cell_details(reader: WorkbookReader, loc: Location) -> None:
    """Text summary of one reported location; the workbook pane shows it in context."""
    if loc.kind == "workbook" or not loc.sheet:
        st.caption("This finding has no specific workbook location.")
        return
    if reader.worksheet(loc.sheet) is None:
        st.warning(f"Sheet '{loc.sheet}' is not a worksheet in the uploaded workbook.")
        return
    state = next((e.state for e in reader.entries if e.name == loc.sheet), "visible")
    if loc.kind in {"cell", "comment"}:
        if parse_cell(loc.cell) is None:
            st.warning(f"'{loc.cell}' is not a valid cell address.")
            return
        _cell_summary(reader, loc.sheet, loc.cell)
        if loc.kind == "comment":
            st.markdown(f"**Comment by {esc(loc.author or 'unknown')}:** {esc(display(loc.value, 300))}")
    elif loc.kind == "range":
        bounds = parse_range(loc.range)
        if bounds is None:
            st.warning(f"'{loc.range}' is not a valid range address.")
            return
        st.markdown(_visibility(state))
        st.caption(f"Range of {bounds.size} cells, highlighted in the workbook.")
    elif loc.kind == "absence":
        st.markdown(
            f"Expected content was not found next to the label {esc(display(loc.anchor_value))} "
            f"at {esc(format_ref(loc.sheet, loc.anchor))}."
        )
        if parse_cell(loc.anchor):
            _cell_summary(reader, loc.sheet, loc.anchor)
    elif loc.kind == "drawing":
        st.markdown(
            f"Text box {esc(loc.drawing_name or '')} anchored at {esc(loc.anchor or '?')}: "
            f"{esc(display(loc.value, 300))}"
        )
        st.caption("Text boxes and other drawings are not shown in the workbook view.")
    else:
        st.caption(f"Location kind '{loc.kind}' is not previewed.")
