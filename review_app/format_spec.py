"""Strict grammars for the free-text formatting changes in review.json 0.3.

Originally ported from the dropped Excel add-in and extended
with a column-width grammar. Anything that does not match exactly is treated as a manual change: these parsers
never guess."""

from __future__ import annotations

import re

from review_app.models import FontSpec

_LAYOUT_WORD = re.compile(r"\b(column|row|width|height|widen|narrow|wrap|default)\b", re.IGNORECASE)
_FORMAT_PART = re.compile(
    r"^(font\s+)?(?:([A-Za-z][A-Za-z ]*?)\s+)?(\d{1,3}(?:\.\d{1,2})?)(?:\s+(bold))?$", re.IGNORECASE
)
_COLUMN_WIDTH = re.compile(r"^column\s+([A-Za-z]{1,3})\s+width\s+(\d{1,3}(?:\.\d{1,4})?)$", re.IGNORECASE)


def parse_font_spec(text: object) -> FontSpec | None:
    """'Carlito 11', 'font Arial 10', 'Calibri 16 bold', 'Arial 10, wrap text enabled', 'Carlito 11, no wrap'.

    Returns None for anything ambiguous: column widths, 'or' alternatives, row heights, leftover tokens."""
    if not isinstance(text, str):
        return None
    name: str | None = None
    size: float | None = None
    bold: bool | None = None
    wrap: bool | None = None
    for raw in text.split(","):
        part = raw.strip()
        lower = part.lower()
        if lower in {"no wrap", "wrap text enabled"}:
            if wrap is not None:
                return None
            wrap = lower == "wrap text enabled"
            continue
        m = _FORMAT_PART.match(part)
        if not m:
            return None
        prefix, part_name, part_size, part_bold = m.groups()
        if part_name is None and prefix is None and part_bold is None:
            return None  # a bare number is a value, not a format
        if part_name is not None and _LAYOUT_WORD.search(part_name):
            return None
        if size is not None or (part_name is not None and name is not None):
            return None
        if part_bold is not None and bold is not None:
            return None
        size = float(part_size)
        if part_name is not None:
            name = part_name.strip()
        if part_bold is not None:
            bold = True
    spec = FontSpec(name=name, size=size, bold=bold, wrap=wrap)
    return spec if spec != FontSpec() else None


def parse_column_width(text: object) -> tuple[str, float] | None:
    """'column H width 2.0' -> ('H', 2.0)."""
    if not isinstance(text, str):
        return None
    m = _COLUMN_WIDTH.match(text.strip())
    if not m:
        return None
    width = float(m.group(2))
    if not 0 < width <= 255:
        return None
    return m.group(1).upper(), width


def has_layout_words(text: object) -> bool:
    return isinstance(text, str) and bool(_LAYOUT_WORD.search(text))
