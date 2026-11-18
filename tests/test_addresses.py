import pytest

from review_app.addresses import (
    Bounds,
    col_index,
    col_letter,
    format_ref,
    is_valid_sheet_name,
    normalize_cell,
    parse_cell,
    parse_range,
)


@pytest.mark.parametrize("text,expected", [("A1", (1, 1)), ("$B$70", (2, 70)), ("xfd1048576", (16384, 1048576))])
def test_valid_cells(text, expected):
    assert parse_cell(text) == expected


@pytest.mark.parametrize("text", ["", "A0", "XFE1", "A1048577", "1A", "A1:B2", None, 12, "Lead!A1"])
def test_invalid_cells(text):
    assert parse_cell(text) is None


def test_ranges():
    assert parse_range("A32:B32") == Bounds(1, 32, 2, 32)
    assert parse_range("B2:A1") == Bounds(1, 1, 2, 2)
    assert parse_range("H12") == Bounds(8, 12, 8, 12)
    assert parse_range("H12:H14").cells() == ["H12", "H13", "H14"]
    assert parse_range("A:A") is None
    assert parse_range("A1:B2:C3") is None


def test_columns_round_trip():
    for i in (1, 26, 27, 702, 703, 16384):
        assert col_index(col_letter(i)) == i


def test_sheet_names_and_quoting():
    assert is_valid_sheet_name("Prüfung – Übersicht")
    assert is_valid_sheet_name("Kunde's Daten")
    assert not is_valid_sheet_name("a/b")
    assert not is_valid_sheet_name("x" * 32)
    assert format_ref("Lead", "B70") == "Lead!B70"
    assert format_ref("Kunde's Daten", "C75") == "'Kunde''s Daten'!C75"
    assert format_ref("Formatting Examples", "F9") == "'Formatting Examples'!F9"
    assert normalize_cell("$b$7") == "B7"
