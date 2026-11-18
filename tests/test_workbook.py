from conftest import SYN_NAME

from review_app.addresses import parse_range
from review_app.workbook import WorkbookReader, open_reader


def test_profile(syn_bytes):
    p = WorkbookReader(syn_bytes).profile(SYN_NAME)
    assert p.sheet_names == ["Lead", "Kunde's Daten", "Prüfung – Übersicht", "Hidden Calc", "Locked"]
    assert p.sheet("Hidden Calc").state == "hidden"
    assert p.sheet("Locked").protected and not p.sheet("Lead").protected
    assert p.sheet("Lead").hidden_rows == [5] and p.sheet("Lead").hidden_columns == ["F"]
    assert p.sheet("Lead").merged_ranges == ["A2:D2"]
    assert p.defined_names == ["Materiality=Lead!$B$67"]
    assert p.writable and not p.has_vba and p.extension == ".xlsx"
    assert "docMetadata/LabelInfo.xml" in p.parts


def test_signed_workbook_is_review_only():
    from conftest import build_workbook

    data = build_workbook(extras={"_xmlsignatures/sig1.xml": b"<x/>"})
    p = WorkbookReader(data).profile("Signed.xlsx")
    assert not p.writable and "digitally signed" in p.not_writable_reasons[0]


def test_cell_snapshots(syn_bytes):
    r = WorkbookReader(syn_bytes)
    s = r.snapshot("Lead", "B70")
    assert s.formula == "=SUM(B67:B68)" and s.value == "=SUM(B67:B68)"
    a1 = r.snapshot("Lead", "A1")
    assert (a1.font_name, a1.font_size, a1.bold, a1.value) == ("Carlito", 11.0, False, "ABC Ltd")
    pr = r.snapshot("Prüfung – Übersicht", "B2")
    assert pr.bold and pr.font_size == 16
    assert r.snapshot("Kunde's Daten", "C75").value == 120
    c10 = r.snapshot("Lead", "C10")
    assert c10.fill == "00FFFF00" and c10.horizontal == "center"
    assert r.snapshot("Lead", "A5").row_hidden and r.snapshot("Lead", "F1").column_hidden
    assert r.snapshot("Lead", "B2").merged_range == "A2:D2"
    assert r.snapshot("Hidden Calc", "D14").sheet_state == "hidden"


def test_missing_sheet_and_cell(syn_bytes):
    r = WorkbookReader(syn_bytes)
    assert not r.snapshot("Nope", "A1").exists
    assert not r.snapshot("Lead", "ZZZZ1").exists
    empty = r.snapshot("Lead", "Z99")
    assert empty.exists and empty.value is None
    assert "Z99" not in r.dump("Lead")  # peeking never creates cells


def test_context_and_range_limits(syn_bytes):
    r = WorkbookReader(syn_bytes)
    ctx = r.context("Lead", "B70")
    assert ctx.row_labels == [68, 69, 70, 71, 72] and ctx.col_labels == ["A", "B", "C", "D"]
    assert ctx.rows[0][1] == "200" and ctx.rows[2][1] == "=SUM(B67:B68)"  # no cached value in a file saved by openpyxl
    corner = r.context("Lead", "A1")
    assert corner.row_labels[0] == 1 and corner.col_labels[0] == "A"
    big = r.grid("Lead", parse_range("A1:J50"), 100)
    assert big.truncated and big.total_cells == 500 and sum(len(row) for row in big.rows) == 100
    wide = r.grid("Lead", parse_range("A1:ZZ1"), 100)
    assert len(wide.rows[0]) == 100 and wide.truncated


def test_long_values_are_truncated():
    from review_app.workbook import display

    assert display("x" * 500).endswith("…") and len(display("x" * 500)) == 120
    assert display(True) == "TRUE" and display(None) == ""


def test_reader_is_cached_by_content(syn_bytes):
    assert open_reader(syn_bytes) is open_reader(bytes(syn_bytes))


def test_real_workbook_profile(real_workbook):
    name, data = real_workbook
    p = WorkbookReader(data).profile(name)
    assert p.sheet_names == ["Lead", "Testing", "Reconcilation", "Formatting Examples"]
    assert all(s.state == "visible" for s in p.sheets) and p.writable
    assert "docMetadata/LabelInfo.xml" in p.parts and "xl/calcChain.xml" in p.parts
