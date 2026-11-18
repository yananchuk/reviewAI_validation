import io
import zipfile

from conftest import SYN_NAME, build_workbook

from review_app.integrity import download_allowed, run_checks
from review_app.models import FontSpec, SetColumnWidth, SetFont, SetFormula, SetValue, VisibilityRequest
from review_app.package_patch import BuildResult, build
from review_app.workbook import sha256

CHANGES = [
    SetValue(sheet="Lead", cell="B63", before="x", after="The invoices were checked."),
    SetFormula(sheet="Lead", cell="B70", before="=SUM(B67:B68)", after="=SUM(B67:B69)"),
    SetFont(
        sheet="Lead", target="A32:B32", before=FontSpec(name="Carlito", size=11), after=FontSpec(name="Arial", size=10)
    ),
    SetColumnWidth(sheet="Lead", column="H", before=2.0, after=15.375),
]


OUT_NAME = SYN_NAME.replace(".xlsx", "_reviewed.xlsx")


def check(original, result, changes=CHANGES, reveals=(), out_name=OUT_NAME):
    return run_checks(
        original=original,
        original_sha256=sha256(original),
        input_name=SYN_NAME,
        output_name=out_name,
        result=result,
        changes=changes,
        reveals=reveals,
    )


def failed(results):
    return {r.check for r in results if r.status == "fail"}


def test_clean_build_passes(syn_bytes):
    results = check(syn_bytes, build(syn_bytes, CHANGES))
    assert failed(results) == set() and download_allowed(results)
    assert any(r.status == "warn" and r.check == "Formula results" for r in results)


def test_unexpected_value_change_blocks(syn_bytes):
    """Simulate a writer bug: a change outside the approved targets."""
    sneaky = build(syn_bytes, CHANGES + [SetValue(sheet="Lead", cell="H14", before=8500, after=9999)])
    results = check(syn_bytes, sneaky)
    assert "Formulas and values outside approved targets unchanged" in failed(results)
    assert not download_allowed(results)


def test_target_mismatch_blocks(syn_bytes):
    other = build(syn_bytes, [SetValue(sheet="Lead", cell="B63", before="x", after="something else")])
    assert "Approved targets hold the approved change" in failed(check(syn_bytes, other, CHANGES[:1]))


def test_tampered_part_blocks(syn_bytes):
    res = build(syn_bytes, CHANGES)
    buf = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(res.data)) as zin, zipfile.ZipFile(buf, "w") as zout:
        for info in zin.infolist():
            data = zin.read(info.filename)
            if info.filename == "docMetadata/LabelInfo.xml":
                data = b"<changed/>"
            zout.writestr(info, data)
        zout.writestr("xl/extra.xml", b"<x/>")
    tampered = BuildResult(data=buf.getvalue(), edited_parts=res.edited_parts, removed_parts=res.removed_parts)
    assert "All other package parts byte-identical" in failed(check(syn_bytes, tampered))


def test_visibility_change_without_request_blocks(syn_bytes):
    reveal = [
        VisibilityRequest(finding_id="X", sheet="Hidden Calc", what="sheet", ref="Hidden Calc", original_state="hidden")
    ]
    res = build(syn_bytes, [], reveal)
    assert "Visibility preserved" in failed(check(syn_bytes, res, [], reveals=()))
    assert failed(check(syn_bytes, res, [], reveals=reveal)) == set()


def test_unreadable_output_blocks(syn_bytes):
    results = check(syn_bytes, BuildResult(data=b"not a zip"))
    assert "Output workbook reopens" in failed(results) and not download_allowed(results)


def test_source_change_and_extension_block(syn_bytes):
    res = build(syn_bytes, CHANGES)
    results = run_checks(
        original=syn_bytes,
        original_sha256="0" * 64,
        input_name=SYN_NAME,
        output_name="x.xlsm",
        result=res,
        changes=CHANGES,
    )
    assert {"Source workbook unchanged", "Extension kept"} <= failed(results)


def test_merges_and_defined_names_preserved(syn_bytes):
    results = check(syn_bytes, build(syn_bytes, CHANGES))
    status = {r.check: r.status for r in results}
    assert status["Merged ranges preserved"] == status["Defined names preserved"] == "pass"


def test_xlsm_checks_vba():
    data = build_workbook(xlsm=True)
    res = build(data, CHANGES)
    results = run_checks(
        original=data,
        original_sha256=sha256(data),
        input_name="WP.xlsm",
        output_name="WP_reviewed.xlsm",
        result=res,
        changes=CHANGES,
    )
    assert any(r.check == "VBA project preserved" and r.status == "pass" for r in results)
    assert download_allowed(results)
