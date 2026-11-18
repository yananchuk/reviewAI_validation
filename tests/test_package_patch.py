import hashlib
import io
import zipfile

import pytest
from conftest import build_workbook

from review_app.models import FontSpec, SetColumnWidth, SetFont, SetFormula, SetValue, VisibilityRequest
from review_app.package_patch import PatchError, build, column_width
from review_app.workbook import WorkbookReader


def parts(data):
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        return {n: zf.read(n) for n in zf.namelist()}


def test_value_formula_text_and_number(syn_bytes):
    res = build(
        syn_bytes,
        [
            SetValue(sheet="Lead", cell="B63", before="The invoices was checked.", after="The invoices were checked."),
            SetFormula(sheet="Lead", cell="B70", before="=SUM(B67:B68)", after="=SUM(B67:B69)"),
            SetFormula(sheet="Kunde's Daten", cell="C75", before=120, after="=A75*B75"),
            SetValue(sheet="Lead", cell="H13", before=120000, after=130000.5),
        ],
    )
    r = WorkbookReader(res.data)
    assert r.snapshot("Lead", "B63").value == "The invoices were checked."
    assert r.snapshot("Lead", "B70").formula == "=SUM(B67:B69)"
    assert r.snapshot("Kunde's Daten", "C75").formula == "=A75*B75"
    assert r.snapshot("Lead", "H13").value == 130000.5  # number stays a number
    assert res.formula_cells == [("Lead", "B70"), ("Kunde's Daten", "C75")]
    wb = parts(res.data)["xl/workbook.xml"].decode()
    assert 'fullCalcOnLoad="1"' in wb


def test_text_starting_with_equals_stays_text(syn_bytes):
    res = build(syn_bytes, [SetValue(sheet="Lead", cell="B63", before="x", after="=not a formula")])
    s = WorkbookReader(res.data).snapshot("Lead", "B63")
    assert s.formula is None and s.value == "=not a formula"


def test_unicode_and_escaping_preserved(syn_bytes):
    text = '  Prüfung <ok> & "quotes" – €  '
    res = build(syn_bytes, [SetValue(sheet="Lead", cell="B63", before="x", after=text)])
    assert WorkbookReader(res.data).snapshot("Lead", "B63").value == text
    with pytest.raises(PatchError, match="cannot be stored"):
        build(syn_bytes, [SetValue(sheet="Lead", cell="B63", before="x", after="bad\x01char")])


def test_only_edited_parts_change_and_unknown_parts_survive(syn_bytes):
    res = build(syn_bytes, [SetValue(sheet="Lead", cell="B63", before="x", after="y")])
    before, after = parts(syn_bytes), parts(res.data)
    assert res.edited_parts == {"xl/worksheets/sheet1.xml"}
    assert after["docMetadata/LabelInfo.xml"] == before["docMetadata/LabelInfo.xml"]
    for name in set(before) - res.edited_parts:
        assert after[name] == before[name], name


def test_edited_sheet_changes_only_the_cell(syn_bytes):
    res = build(syn_bytes, [SetValue(sheet="Lead", cell="B63", before="x", after="y")])
    old = parts(syn_bytes)["xl/worksheets/sheet1.xml"].decode()
    new = parts(res.data)["xl/worksheets/sheet1.xml"].decode()
    i = old.index('<c r="B63"')
    assert new[:i] == old[:i]
    assert new[-200:] == old[-200:]  # conditional formatting, data validation etc. untouched


def test_calc_chain_is_removed_with_its_references():
    data = build_workbook(
        extras={
            "xl/calcChain.xml": (
                b'<calcChain xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
                b'<c r="B70" i="1"/></calcChain>'
            )
        }
    )
    zf = parts(data)
    zf_rels = zf["xl/_rels/workbook.xml.rels"].replace(
        b"</Relationships>",
        b'<Relationship Id="rId99" Target="calcChain.xml" '
        b'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/calcChain"/></Relationships>',
    )
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for n, d in zf.items():
            z.writestr(n, zf_rels if n == "xl/_rels/workbook.xml.rels" else d)
    res = build(buf.getvalue(), [SetFormula(sheet="Lead", cell="B70", before="=SUM(B67:B68)", after="=SUM(B67:B69)")])
    out = parts(res.data)
    assert "xl/calcChain.xml" not in out and res.removed_parts == {"xl/calcChain.xml"}
    assert b"calcChain" not in out["xl/_rels/workbook.xml.rels"]
    WorkbookReader(res.data)  # still opens


def test_font_change_appends_formats_and_keeps_other_cells(syn_bytes):
    res = build(
        syn_bytes,
        [
            SetFont(
                sheet="Lead",
                target="A32:B32",
                before=FontSpec(name="Carlito", size=11),
                after=FontSpec(name="Arial", size=10),
            ),
            SetFont(
                sheet="Prüfung – Übersicht",
                target="B2",
                before=FontSpec(name="Calibri", size=16, bold=True),
                after=FontSpec(name="Arial", size=11, bold=True),
            ),
            SetFont(
                sheet="Lead",
                target="D67",
                before=FontSpec(name="Carlito", size=11, wrap=False),
                after=FontSpec(name="Arial", size=10, wrap=True),
            ),
        ],
    )
    r = WorkbookReader(res.data)
    for ref in ("A32", "B32"):
        s = r.snapshot("Lead", ref)
        assert (s.font_name, s.font_size, s.bold) == ("Arial", 10.0, False)
    p = r.snapshot("Prüfung – Übersicht", "B2")
    assert (p.font_name, p.font_size, p.bold) == ("Arial", 11.0, True)
    d = r.snapshot("Lead", "D67")
    assert d.wrap and d.font_name == "Arial"
    a1 = r.snapshot("Lead", "A1")  # same original style as A32, must be untouched
    assert (a1.font_name, a1.font_size) == ("Carlito", 11.0)
    c10 = r.snapshot("Lead", "C10")
    assert c10.fill == "00FFFF00" and c10.horizontal == "center"
    assert res.edited_parts >= {"xl/styles.xml"}


def test_font_change_is_deduplicated(syn_bytes):
    spec = dict(before=FontSpec(name="Carlito", size=11), after=FontSpec(name="Arial", size=10))
    one = build(syn_bytes, [SetFont(sheet="Lead", target="A32", **spec)])
    two = build(syn_bytes, [SetFont(sheet="Lead", target="A32", **spec), SetFont(sheet="Lead", target="B32", **spec)])
    assert parts(one.data)["xl/styles.xml"] == parts(two.data)["xl/styles.xml"]


def test_column_width(syn_bytes):
    res = build(syn_bytes, [SetColumnWidth(sheet="Lead", column="H", before=2.0, after=15.375)])
    assert column_width(res.data, "Lead", "H") == (15.375, False)
    assert column_width(syn_bytes, "Lead", "H") == (2.0, False)
    new = build(syn_bytes, [SetColumnWidth(sheet="Lead", column="Q", before=0, after=20)])
    assert column_width(new.data, "Lead", "Q") == (20.0, False)


def test_shared_column_spec_is_refused():
    data = build_workbook(mutate=lambda wb: wb["Lead"].column_dimensions.group("J", "L", hidden=False))
    width, shared = column_width(data, "Lead", "K")
    assert shared
    with pytest.raises(PatchError, match="shares"):
        build(data, [SetColumnWidth(sheet="Lead", column="K", before=width or 0, after=20)])


def test_missing_cell_sheet_and_chartless_targets(syn_bytes):
    with pytest.raises(PatchError, match="no content"):
        build(syn_bytes, [SetValue(sheet="Lead", cell="Z999", before="x", after="y")])
    with pytest.raises(PatchError, match="not in the workbook"):
        build(syn_bytes, [SetValue(sheet="Nope", cell="A1", before="x", after="y")])


def test_xlsm_vba_preserved():
    data = build_workbook(xlsm=True)
    res = build(data, [SetValue(sheet="Lead", cell="B63", before="x", after="y")])
    sha = lambda d: hashlib.sha256(parts(d)["xl/vbaProject.bin"]).hexdigest()  # noqa: E731
    assert sha(res.data) == sha(data)


def test_reveals(syn_bytes):
    reveals = [
        VisibilityRequest(
            finding_id="C015", sheet="Hidden Calc", what="sheet", ref="Hidden Calc", original_state="hidden"
        ),
        VisibilityRequest(finding_id="X", sheet="Lead", what="row", ref="5", original_state="hidden"),
        VisibilityRequest(finding_id="X", sheet="Lead", what="column", ref="F", original_state="hidden"),
    ]
    p = WorkbookReader(build(syn_bytes, [], reveals).data).profile("x.xlsx")
    assert p.sheet("Hidden Calc").state == "visible"
    assert p.sheet("Lead").hidden_rows == [] and p.sheet("Lead").hidden_columns == []


def test_no_changes_copies_every_part(syn_bytes):
    res = build(syn_bytes, [])
    assert parts(res.data) == parts(syn_bytes) and not res.edited_parts


def test_real_workbook_quick_fixes(real_workbook):
    """C017, C027, C028, C029, C031 of the Trigger report on the real workbook."""
    _, data = real_workbook
    res = build(
        data,
        [
            SetFont(
                sheet="Lead",
                target="A1",
                before=FontSpec(name="Carlito", size=11),
                after=FontSpec(name="Arial", size=10),
            ),
            SetColumnWidth(sheet="Formatting Examples", column="H", before=2.0, after=15.375),
            SetFormula(sheet="Lead", cell="B70", before="=SUM(B67:B68)", after="=SUM(B67:B69)"),
            SetFormula(sheet="Lead", cell="C75", before=120, after="=A75*B75"),
            SetValue(sheet="Lead", cell="B63", before="The invoices was checked.", after="The invoices were checked."),
        ],
    )
    before, after = parts(data), parts(res.data)
    assert set(after) == set(before) - {"xl/calcChain.xml"}
    assert after["docMetadata/LabelInfo.xml"] == before["docMetadata/LabelInfo.xml"]
    r = WorkbookReader(res.data)
    assert r.snapshot("Lead", "B70").formula == "=SUM(B67:B69)"
    assert r.snapshot("Lead", "A1").font_name == "Arial"
