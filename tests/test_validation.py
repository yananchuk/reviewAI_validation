import copy

from conftest import REPO, finding

from review_app import limits
from review_app.validation import check_workbook_name, validate_report


def errors(issues):
    return [i.message for i in issues if i.level == "error"]


def test_packaged_schema_matches_shared_schema():
    from importlib import resources

    packaged = resources.files("review_app").joinpath("schema/review-report.schema.json").read_bytes()
    assert packaged == (REPO / "shared" / "review-report.schema.json").read_bytes()


def test_shared_example_is_valid(example_report):
    assert errors(validate_report(example_report)) == []


def test_real_report_is_valid(real_report):
    assert errors(validate_report(real_report)) == []


def test_missing_schema_version_is_unsupported(example_report):
    del example_report["schema_version"]
    assert "0.2 or older" in errors(validate_report(example_report))[0]


def test_unsupported_schema_version(example_report):
    example_report["schema_version"] = "0.4"
    assert errors(validate_report(example_report)) == ["Report schema version 0.4 is not supported. Supported: 0.3."]


def test_skill_version_does_not_decide_compatibility(example_report):
    example_report["skill_version"] = "99.0.0"
    assert errors(validate_report(example_report)) == []


def test_missing_required_fields(example_report):
    del example_report["run_id"]
    del finding(example_report, "C002")["message"]
    msgs = errors(validate_report(example_report))
    assert any("'run_id' is a required property" in m for m in msgs)
    assert any("'message' is a required property" in m and "findings" in m for m in msgs)


def test_unsupported_resolution_is_rejected(example_report):
    finding(example_report, "C002")["resolution"] = "auto"
    assert any("resolution" in m for m in errors(validate_report(example_report)))


def test_malformed_rule(example_report):
    example_report["rules"][0] = {"rule_id": "X"}
    assert errors(validate_report(example_report))


def test_duplicate_ids(example_report):
    finding(example_report, "C003")["id"] = "C002"
    assert errors(validate_report(example_report)) == ["Finding ID C002 appears more than once."]


def test_too_many_findings(example_report, monkeypatch):
    monkeypatch.setattr(limits, "MAX_FINDINGS", 3)
    assert "more than 3 findings" in errors(validate_report(example_report))[0]


def test_schema_error_cap(example_report, monkeypatch):
    monkeypatch.setattr(limits, "MAX_SCHEMA_ERRORS", 2)
    for rule in example_report["rules"]:
        rule["status"] = "BAD"
    msgs = errors(validate_report(example_report))
    assert len(msgs) == 3 and msgs[-1].endswith("more schema errors are not shown.")


def test_changed_workbook_and_empty_report_are_warnings(example_report):
    report = copy.deepcopy(example_report)
    report["workbook_unchanged"] = False
    issues = validate_report(report)
    assert not errors(issues) and any("changed while the review" in i.message for i in issues)


def test_workbook_name_check():
    assert check_workbook_name("Leadsheet.xlsx", "Leadsheet.xlsx") is None
    issue = check_workbook_name("Leadsheet.xlsx", "Leadsheet (2).xlsx")
    assert issue.level == "warning" and "Leadsheet (2).xlsx" in issue.message
