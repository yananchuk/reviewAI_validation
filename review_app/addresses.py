"""A1-style cell and range addresses. No sheet-name assumptions: names may be in any language and contain spaces
or apostrophes; they are only quoted when rendered as a reference."""

from __future__ import annotations

import re
from dataclasses import dataclass

MAX_ROW = 1_048_576
MAX_COL = 16_384

_CELL = re.compile(r"^\$?([A-Za-z]{1,3})\$?(\d{1,7})$")
_INVALID_SHEET_CHARS = set("[]:*?/\\")


def col_index(letters: str) -> int:
    """'A' -> 1, 'XFD' -> 16384."""
    n = 0
    for ch in letters.upper():
        n = n * 26 + (ord(ch) - 64)
    return n


def col_letter(index: int) -> str:
    """1 -> 'A'."""
    out = ""
    while index > 0:
        index, rem = divmod(index - 1, 26)
        out = chr(65 + rem) + out
    return out


@dataclass(frozen=True)
class Bounds:
    """Inclusive cell rectangle, 1-based."""

    min_col: int
    min_row: int
    max_col: int
    max_row: int

    @property
    def is_single(self) -> bool:
        return self.min_col == self.max_col and self.min_row == self.max_row

    @property
    def size(self) -> int:
        return (self.max_col - self.min_col + 1) * (self.max_row - self.min_row + 1)

    def contains(self, col: int, row: int) -> bool:
        return self.min_col <= col <= self.max_col and self.min_row <= row <= self.max_row

    def cells(self) -> list[str]:
        return [
            f"{col_letter(c)}{r}"
            for r in range(self.min_row, self.max_row + 1)
            for c in range(self.min_col, self.max_col + 1)
        ]

    @property
    def ref(self) -> str:
        first = f"{col_letter(self.min_col)}{self.min_row}"
        if self.is_single:
            return first
        return f"{first}:{col_letter(self.max_col)}{self.max_row}"


def parse_cell(text: str | None) -> tuple[int, int] | None:
    """'B70' or '$B$70' -> (col, row); None if not a valid single cell."""
    if not isinstance(text, str):
        return None
    m = _CELL.match(text.strip())
    if not m:
        return None
    col, row = col_index(m.group(1)), int(m.group(2))
    if not (1 <= col <= MAX_COL and 1 <= row <= MAX_ROW):
        return None
    return col, row


def parse_range(text: str | None) -> Bounds | None:
    """'A32:B32', 'B70' (single cell) -> Bounds; None if invalid. Whole-column/row refs are not accepted."""
    if not isinstance(text, str):
        return None
    parts = text.strip().split(":")
    if len(parts) == 1:
        cell = parse_cell(parts[0])
        return Bounds(cell[0], cell[1], cell[0], cell[1]) if cell else None
    if len(parts) != 2:
        return None
    a, b = parse_cell(parts[0]), parse_cell(parts[1])
    if not a or not b:
        return None
    return Bounds(min(a[0], b[0]), min(a[1], b[1]), max(a[0], b[0]), max(a[1], b[1]))


def normalize_cell(text: str) -> str | None:
    """'$b$70' -> 'B70'."""
    cell = parse_cell(text)
    return f"{col_letter(cell[0])}{cell[1]}" if cell else None


def is_valid_sheet_name(name: object) -> bool:
    return (
        isinstance(name, str)
        and 0 < len(name) <= 31
        and not any(ch in _INVALID_SHEET_CHARS for ch in name)
        and not name.startswith("'")
        and not name.endswith("'")
    )


def quote_sheet(name: str) -> str:
    """Quote a sheet name for display in a reference: Kunde's Daten -> 'Kunde''s Daten'."""
    if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.]*", name):
        return name
    return "'" + name.replace("'", "''") + "'"


def format_ref(sheet: str | None, ref: str | None) -> str:
    if not sheet:
        return ref or ""
    return f"{quote_sheet(sheet)}!{ref}" if ref else quote_sheet(sheet)
