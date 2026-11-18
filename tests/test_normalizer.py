from conftest import finding

from review_app.format_spec import parse_column_width, parse_font_spec
from review_app.models import FontSpec, Resolution
from review_app.normalizer import normalize


def ops(report):
    _, findings, _ = normalize(report)
    return {f.id: f.operation for f in findings}


def test_flattening_keeps_report_order_and_rule_meta(example_report):
    meta, findings, warnings = normalize(example_report)
    assert meta.schema_version == "0.3" and meta.workbook == "Synthetic_Leadsheet.xlsx"
    assert [f.id for f in findings] == [f"C{i:03d}" for i in range(1, 17)]
    c002 = next(f for f in findings if f.id == "C002")
    assert c002.rule.rule_id == "QLT-FMT-01" and c002.rule.theme == "D. Quality"
    assert c002.raw["fingerprint"] == c002.fingerprint  # original data kept for traceability
    assert warnings == []


def test_optional_fields_are_normalized(example_report):
    _, findings, _ = normalize(example_report)
    by_id = {f.id: f for f in findings}
    assert by_id["C012"].locations[0].kind == "workbook" and by_id["C012"].locations[0].sheet is None
    assert by_id["C006"].locations[0].has_value is False  # range without 'value'
    assert by_id["C013"].locations[0].has_value is True and by_id["C013"].locations[0].value is None
    assert by_id["C009"].check_problems and by_id["C002"].check_problems == []
    assert by_id["C015"].sheets == ["Hidden Calc"] and len(by_id["C015"].locations) == 2
    assert by_id["C012"].primary_label == "Workbook"


def test_duplicates_and_missing_references(example_report):
    finding(example_report, "C016")["possible_duplicate_of"] = ["C001", "C099"]
    _, findings, warnings = normalize(example_report)
    c016 = next(f for f in findings if f.id == "C016")
    assert c016.duplicates == ["C001"] and c016.missing_duplicates == ["C099"]
    assert [w.message for w in warnings] == ["C099 referenced by C016 as a possible duplicate is not in the report."]


def test_ids_are_not_assumed_sequential(example_report):
    finding(example_report, "C002")["id"] = "C900"
    assert "C900" in ops(example_report)


def test_example_classification(example_report):
    o = ops(example_report)
    assert o["C001"].op == "manual"  # follow-up
    assert o["C002"].op == "set_font" and o["C002"].after == FontSpec(name="Arial", size=10)
    assert o["C003"].op == "set_font" and o["C003"].after.bold is True
    assert o["C004"].op == "set_font" and o["C004"].after.wrap is True and o["C004"].before.wrap is False
    assert o["C005"].op == "manual"  # 'widen column H or wrap text enabled'
    assert o["C006"].op == "set_font" and o["C006"].target == "A32:B32"
    assert o["C007"].op == "set_column_width" and (o["C007"].before, o["C007"].after) == (2.0, 15.375)
    assert o["C008"].op == "manual" and "same range" in o["C008"].reason  # A33 vs reported A33:B33
    assert o["C009"].op == "set_formula" and o["C009"].after == "=SUM(B67:B69)"
    assert o["C010"].op == "set_formula" and o["C010"].before == 120  # typed value -> formula
    assert o["C011"].op == "set_value" and o["C011"].after == "The invoices were checked."


def test_ambiguous_and_unsafe_changes_stay_manual(example_report):
    c011 = finding(example_report, "C011")
    c011["change"]["after"] = "=HYPERLINK(1)"
    assert "does not identify it as a formula" in ops(example_report)["C011"].reason
    c011["change"]["after"] = "The invoices were checked."
    c011["change"]["before"] = "something else"
    assert "does not match" in ops(example_report)["C011"].reason
    c011["change"]["before"] = "The invoices was checked."
    c011["change"]["cell"] = "B63:B64"
    assert "single cell" in ops(example_report)["C011"].reason
    c011["change"] = None
    assert "proposes no change" in ops(example_report)["C011"].reason
    c010 = finding(example_report, "C010")
    c010["change"]["before"] = "120"  # number in the cell, text in the change: types must agree
    assert ops(example_report)["C010"].op == "manual"


def test_quick_fix_with_no_location_is_manual(example_report):
    finding(example_report, "C011")["locations"] = [{"kind": "workbook", "value": None}]
    assert "reported location" in ops(example_report)["C011"].reason


def test_font_grammar():
    assert parse_font_spec("font Carlito 11") == FontSpec(name="Carlito", size=11)
    assert parse_font_spec("Times New Roman 14 bold") == FontSpec(name="Times New Roman", size=14, bold=True)
    assert parse_font_spec("Carlito 11, no wrap") == FontSpec(name="Carlito", size=11, wrap=False)
    for bad in ("column H width 2.0", "widen column H or wrap text enabled", "120", "Arial", "Arial 10 italic", 10):
        assert parse_font_spec(bad) is None
    assert parse_column_width("column H width 15.375") == ("H", 15.375)
    assert parse_column_width("column H width 0") is None


def test_real_report_classification(real_report):
    """The 15 Quick Fixes of the supplied Trigger report classify as in the plan's support matrix."""
    _, findings, _ = normalize(real_report)
    qf = {f.id: f.operation.op for f in findings if f.resolution == Resolution.QUICK_FIX}
    assert len(findings) == 81 and len(qf) == 15
    assert {k for k, v in qf.items() if v == "set_font"} == {f"C0{i}" for i in range(17, 27)}
    assert qf["C027"] == "set_column_width"
    assert qf["C028"] == qf["C029"] == "set_formula"
    assert qf["C031"] == qf["C032"] == "set_value"
