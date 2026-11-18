import io
import json
import zipfile

from conftest import SYN_NAME

from review_app import review_session as rs
from review_app.exporter import output_names, package, review_log, safe_stem
from review_app.models import DecisionValue as D


def test_output_names():
    assert output_names("Leadsheet.xlsx") == {
        "workbook": "Leadsheet_reviewed.xlsx",
        "log": "Leadsheet_review_log.json",
        "package": "Leadsheet_review_package.zip",
    }
    assert output_names("Cash Beispiel.XLSM")["workbook"] == "Cash Beispiel_reviewed.xlsm"
    assert output_names("Prüfung – Übersicht.xlsx")["log"] == "Prüfung – Übersicht_review_log.json"


def test_unsafe_names_are_sanitized():
    assert safe_stem("../../etc/passwd.xlsx") == "passwd"
    assert safe_stem("..\\..\\evil.xlsx") == "evil"
    assert safe_stem('a<b>:"c|?*.xlsx') == "a_b___c___"
    assert safe_stem("...") == "workbook"
    with zipfile.ZipFile(io.BytesIO(package({"../x/../../evil.json": b"{}"}))) as z:
        assert z.namelist() == ["evil.json"]


def test_review_log_content(syn_report, syn_bytes):
    state = rs.start_session("Renamed.xlsx", syn_bytes, "r.review.json", json.dumps(syn_report).encode())
    state.name_mismatch_confirmed = True
    rs.start_review(state)
    rs.apply_fix(state, "C011", "a")
    rs.decide(state, "C003", D.REJECTED, "Header style is intentional", "b")
    rs.decide(state, "C005", D.MANUALLY_RESOLVED, "Widened column H in Excel.", "c")
    rs.decide(state, "C001", D.RESOLVED, "Conclusion added in row 41.", "d")
    rs.build_outputs(state)
    log = review_log(state)
    assert log["source_report"]["run_id"] == syn_report["run_id"]
    assert log["source_report"]["schema_version"] == "0.3" and log["source_report"]["skill_version"] == "0.5.0"
    assert log["workbook"]["uploaded_name"] == "Renamed.xlsx" and log["workbook"]["name_in_report"] == SYN_NAME
    assert log["workbook"]["name_mismatch"] and log["workbook"]["name_mismatch_confirmed"]
    assert log["session"]["started_at"] and log["session"]["completed_at"]
    by_id = {f["id"]: f for f in log["findings"]}
    assert len(by_id) == 16 and by_id["C011"]["decision"] == "accepted" and by_id["C016"]["decision"] == "open"
    assert by_id["C001"]["comment"] == "Conclusion added in row 41."
    assert by_id["C005"]["manual_reason"].startswith("This suggested change requires manual review.")
    assert log["rejected_changes"] == ["C003"] and log["manual_resolutions"] == ["C005"]
    assert log["applied_changes"][0]["operation"]["op"] == "set_value"
    assert log["applied_changes"][0]["before_snapshot"]["B63"]["value"] == "The invoices was checked."
    assert log["visibility"]["output_preserves_original"] is True
    assert {s["sheet"]: s["state"] for s in log["visibility"]["original"]}["Hidden Calc"] == "hidden"
    assert any("Renamed.xlsx" in w for w in log["validation_warnings"])
    assert log["integrity"] and log["workbook"]["output"]["name"] == "Renamed_reviewed.xlsx"
    json.dumps(log)  # serializable
