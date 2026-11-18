import json
import re
from datetime import datetime

from conftest import build_workbook
from openpyxl.styles.colors import Color

from review_app import limits
from review_app.sheet_view import (
    anchor_of,
    format_value,
    resolve_color,
    sheet_payload,
    sheet_tabs,
    theme_colors,
    visible_cell,
)

THEME = [
    "FFFFFF", "000000", "E7E6E6", "44546A", "4472C4", "ED7D31",
    "A5A5A5", "FFC000", "5B9BD5", "70AD47", "0563C1", "954F72",
]  # fmt: skip


def test_resolve_color():
    assert resolve_color(Color(rgb="FF171C8F"), THEME) == "171C8F"
    assert resolve_color(Color(theme=4), THEME) == "4472C4"
    lighter = resolve_color(Color(theme=4, tint=0.5), THEME)
    assert lighter != "4472C4" and int(lighter[:2], 16) > 0x44  # lighter
    assert resolve_color(Color(indexed=10), THEME) == "FF0000"
    assert resolve_color(None, THEME) is None


def test_theme_missing_or_out_of_range():
    assert resolve_color(Color(theme=4), []) is None
    assert resolve_color(Color(theme=99), THEME) is None
    assert theme_colors(b"not a zip") == []


def test_theme_colors_from_workbook(syn_bytes):
    colors = theme_colors(syn_bytes)
    assert len(colors) == 12 and all(len(c) == 6 for c in colors)
    assert colors[1] == "000000" and colors[0] == "FFFFFF"  # dk1/lt1 swapped into Excel index order


def test_format_value():
    assert format_value(12000, "#,##0.00") == "12,000.00"
    assert format_value(-5, "#,##0;(#,##0)") == "(5)"
    assert format_value(-5.5, "#,##0.00;[Red]-#,##0.00") == "-5.50"
    assert format_value(0.249, "0.0%") == "24.9%"
    assert format_value(1234.5, "0") == "1235"
    assert format_value(1.274889, "General") == "1.274889"
    assert format_value(18256814.925, None) == "18256814.93"
    assert format_value(100, "General") == "100"
    assert format_value(datetime(2025, 12, 31), "dd.mm.yyyy") == "31.12.2025"
    assert format_value(datetime(2025, 12, 31), "d/m/yyyy") == "31/12/2025"
    assert format_value(datetime(2025, 12, 31), "yyyy-mm-dd") == "2025-12-31"
    assert format_value("text", "@") == "text"
    assert format_value(True, None) == "TRUE"
    assert format_value(None, "0.00") == ""
    assert format_value(1.5, "[$€-407] #,##0.00") == "1.5"  # unknown format: raw value


# ---------------------------------------------------------------- payload


def style_of(payload, ref):
    return payload["styles"][str(payload["cells"][ref]["s"])]


def test_payload_cells_styles_and_sizes(syn_bytes):
    p = sheet_payload(syn_bytes, "Lead")
    assert p["sheet"] == "Lead" and p["window"] is None
    assert p["cells"]["B70"]["f"] == "=SUM(B67:B68)" and p["cells"]["B67"]["t"] == "100"
    assert p["cells"]["B70"]["t"] == "=SUM(B67:B68)" and p["cells"]["B70"]["g"] == 1  # no saved result
    assert p["cells"]["B67"].get("n") == 1 and "n" not in p["cells"]["A1"]  # numbers align right
    st = style_of(p, "A1")
    assert st["font-family"] == "Carlito" and st["font-size"] == "11pt"
    assert style_of(p, "C10")["background-color"] == "#FFFF00" and style_of(p, "C10")["text-align"] == "center"
    assert any(c["c"] == 8 and c["letter"] == "H" and c["w"] == 2.0 for c in p["cols"])
    assert {"r1": 2, "c1": 1, "r2": 2, "c2": 4} in p["merges"]
    assert p["rows"][0]["r"] == 1 and p["rows"][-1]["r"] >= 70
    json.dumps(p)


def test_hidden_rows_and_columns_are_skipped(syn_bytes):
    p = sheet_payload(syn_bytes, "Lead")
    assert p["hidden_rows"] == [5] and p["hidden_cols"] == [6]


def test_text_is_data_not_markup():
    text = '<script>alert(1)</script></style>"javascript:'
    data = build_workbook(mutate=lambda wb: wb["Lead"].__setitem__("A3", text))
    assert sheet_payload(data, "Lead")["cells"]["A3"]["t"] == text


def test_invalid_font_and_colour_fall_back():
    from openpyxl.styles import Font

    data = build_workbook(mutate=lambda wb: setattr(wb["Lead"]["A3"], "font", Font(name="Arial;}body{display:none")))
    wb_payload = sheet_payload(data, "Lead")
    assert "font-family" not in style_of(wb_payload, "A3")
    for style in wb_payload["styles"].values():
        for name, value in style.items():
            assert re.fullmatch(r"[a-z-]+", name) and not re.search(r"[;{}<>]", value)


def test_flags_inside_merge_go_to_anchor(syn_bytes):
    assert anchor_of(syn_bytes, "Lead", "C2") == "A2"
    assert anchor_of(syn_bytes, "Lead", "B70") == "B70"
    assert anchor_of(syn_bytes, "Nope", "B70") == "B70"


def test_large_sheet_renders_a_window(monkeypatch, syn_bytes):
    monkeypatch.setattr(limits, "SHEET_RENDER_MAX_CELLS", 10)
    monkeypatch.setattr(limits, "LARGE_SHEET_WINDOW_ROWS", 6)
    sheet_payload.cache_clear()
    try:
        p = sheet_payload(syn_bytes, "Lead", focus_row=67)
        assert (p["window"]["first_row"], p["window"]["last_row"]) == (64, 69)
        assert all(64 <= int(re.sub(r"[A-Z]", "", a)) <= 69 for a in p["cells"])
        assert [r["r"] for r in p["rows"]] == list(range(64, 70))
    finally:
        sheet_payload.cache_clear()


def test_tabs(syn_bytes):
    tabs = sheet_tabs(syn_bytes)
    assert [t["name"] for t in tabs][:3] == ["Lead", "Kunde's Daten", "Prüfung – Übersicht"]
    assert next(t for t in tabs if t["name"] == "Hidden Calc")["state"] == "hidden"
    assert all(t["color"] is None or re.fullmatch(r"#[0-9A-F]{6}", t["color"]) for t in tabs)


def test_real_workbook_payload(real_workbook):
    _, data = real_workbook
    p = sheet_payload(data, "Lead")
    assert p["cells"]["B63"]["t"] == "The invoices was checked."
    assert len(p["cells"]) > 200 and p["window"] is None


# ---------------------------------------------------------------- final review fixes


def test_merge_with_hidden_top_left_starts_at_first_visible_cell():
    def mutate(wb):
        ws = wb["Lead"]
        ws["F3"] = "merged text"
        ws.merge_cells("F3:G3")  # column F is hidden in the synthetic workbook
        ws["H4"] = "row-merged"
        ws.merge_cells("H5:H6")  # row 5 is hidden
        ws["H5"] = "top hidden"

    p = sheet_payload(build_workbook(mutate=mutate), "Lead")
    assert {"r1": 3, "c1": 7, "r2": 3, "c2": 7} in p["merges"] and p["cells"]["G3"]["t"] == "merged text"
    assert {"r1": 6, "c1": 8, "r2": 6, "c2": 8} in p["merges"] and p["cells"]["H6"]["t"] == "top hidden"


def test_stray_far_right_cell_does_not_explode_columns():
    def mutate(wb):
        wb["Lead"]["XFD300"] = "stray"

    p = sheet_payload(build_workbook(mutate=mutate), "Lead", focus_row=67)
    assert p["window"] is not None and len(p["rows"]) <= limits.LARGE_SHEET_WINDOW_ROWS
    assert len(p["rows"]) * len(p["cols"]) <= limits.SHEET_RENDER_MAX_CELLS
    assert p["cols"][0]["letter"] == "A" and p["window"]["total_cols"] == 16384


def test_visible_cell_maps_hidden_cells_to_the_next_visible_one(syn_bytes):
    p = sheet_payload(syn_bytes, "Lead")
    assert visible_cell(p, "A5") == "A6"  # row 5 hidden
    assert visible_cell(p, "F1") == "G1"  # column F hidden
    assert visible_cell(p, "B70") == "B70"


def test_payload_format_text_for_the_formula_bar(syn_bytes):
    p = sheet_payload(syn_bytes, "Lead")
    fmt = p["formats"]
    assert fmt[str(p["cells"]["A1"]["s"])].startswith("Carlito 11")
    c10 = fmt[str(p["cells"]["C10"]["s"])]
    assert "fill #FFFF00" in c10 and "align center" in c10
    assert all(isinstance(v, str) for v in fmt.values())
