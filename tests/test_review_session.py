import io
import json
import zipfile

import pytest
from conftest import SYN_NAME, build_workbook

from review_app import review_session as rs
from review_app.models import DecisionValue as D
from review_app.models import Resolution
from review_app.workbook import WorkbookReader, sha256


def session(report, data, name=SYN_NAME):
    state = rs.start_session(name, data, "Synthetic_Leadsheet.review.json", json.dumps(report).encode())
    assert state.validation_errors == []
    rs.start_review(state)
    return state


@pytest.fixture
def state(syn_report, syn_bytes):
    return session(syn_report, syn_bytes)


def test_start_and_progress(state):
    assert state.page == "review" and state.started_at
    assert rs.progress(state) == {
        "total": 16,
        "reviewed": 0,
        "remaining": 16,
        "quick_fix": 10,
        "follow_up": 6,
        "applied": 0,
    }
    assert state.current_id == "C002"  # first open Quick Fix


def test_blocking_errors_keep_the_upload_page(syn_bytes):
    state = rs.start_session(SYN_NAME, syn_bytes, "r.json", b"{broken")
    assert state.validation_errors and not state.can_start
    rs.start_review(state)
    assert state.page == "upload"
    bad = rs.start_session("WP.xls", b"x", "r.json", b"{}")
    assert "cannot be read" in bad.validation_errors[0].message


def test_name_mismatch_needs_confirmation(syn_report, syn_bytes):
    state = rs.start_session("Other name.xlsx", syn_bytes, "r.json", json.dumps(syn_report).encode())
    assert state.name_mismatch and not state.can_start
    state.name_mismatch_confirmed = True
    assert state.can_start


def test_missing_sheet_downgrades_fix_and_warns(syn_report, syn_bytes):
    syn_report["rules"][1]["findings"][0]["change"]["sheet"] = "Gone"
    syn_report["rules"][1]["findings"][0]["locations"][0]["sheet"] = "Gone"
    state = session(syn_report, syn_bytes)
    assert not state.findings["C002"].automatic
    assert any("sheet 'Gone' is not in the workbook" in w.message for w in state.validation_warnings)


def test_filters_keep_a_valid_current_finding(state):
    rs.set_filters(state, rules=["QLT-MTH-01"])
    assert rs.visible_ids(state) == ["C009"] and state.current_id == "C009"
    rs.set_filters(state, rules=["NOPE"])
    assert state.current_id is None
    rs.set_filters(state, rules=[], sheets=["Kunde's Daten"])
    assert state.current_id == "C010"
    rs.set_filters(state, sheets=[], themes=["B. Work performed"])
    assert rs.visible_ids(state) == []
    rs.set_tab(state, Resolution.FOLLOW_UP)
    assert rs.visible_ids(state) == ["C013", "C014", "C015", "C016"]


def test_navigation(state):
    rs.go(state, "next")
    assert state.current_id == "C003"
    rs.go(state, "previous")
    rs.go(state, "previous")
    assert state.current_id == "C002"
    rs.decide(state, "C003", D.REJECTED, "", "a1")
    rs.go(state, "next_open")
    assert state.current_id == "C004"  # C003 is reviewed and skipped
    rs.jump(state, "C016")
    assert state.tab == Resolution.FOLLOW_UP and state.current_id == "C016"


def test_status_filter(state):
    rs.decide(state, "C002", D.REJECTED, "", "a1")
    rs.set_filters(state, status="reviewed")
    assert rs.visible_ids(state) == ["C002"]
    rs.set_filters(state, status="open")
    assert "C002" not in rs.visible_ids(state)


def test_apply_is_idempotent_and_undo_replays(state, syn_bytes):
    out = rs.apply_fix(state, "C009", "apply:C009:1")
    assert out.status == "applied" and state.decisions["C009"].value == D.ACCEPTED
    assert rs.apply_fix(state, "C009", "apply:C009:1").status == "duplicate"  # rerun with the same action
    assert rs.apply_fix(state, "C009", "apply:C009:2").status == "duplicate"  # second click
    assert len(state.applied) == 1
    assert WorkbookReader(state.working_bytes).snapshot("Lead", "B70").formula == "=SUM(B67:B69)"
    assert rs.decide(state, "C009", D.REJECTED, "", "x").status == "invalid"  # undo first
    rs.apply_fix(state, "C011", "apply:C011")
    rs.undo(state, "C009", "undo:C009")
    assert [c.finding_id for c in state.applied] == ["C011"] and "C009" not in state.decisions
    w = WorkbookReader(state.working_bytes)
    assert w.snapshot("Lead", "B70").formula == "=SUM(B67:B68)" and w.snapshot("Lead", "B63").value.endswith(
        "were checked."
    )
    rs.undo(state, "C011", "undo:C011")
    assert state.working_bytes == syn_bytes and state.original_sha256 == sha256(syn_bytes)


def test_all_automatic_example_fixes_apply(state):
    for fid in ("C002", "C003", "C004", "C006", "C007", "C009", "C010", "C011"):
        assert rs.apply_fix(state, fid, f"apply:{fid}").status == "applied", fid
    for fid in ("C005", "C008"):
        assert rs.apply_fix(state, fid, f"apply:{fid}").status == "blocked"


def test_stale_value_detected_and_override_logged(syn_report):
    data = build_workbook(mutate=lambda wb: wb["Kunde's Daten"].__setitem__("C75", 999))
    state = session(syn_report, data)
    out = rs.apply_fix(state, "C010", "apply:C010")
    assert out.status == "stale" and "999" in out.details[0] and not state.applied
    assert rs.apply_fix(state, "C010", "apply:C010", override_stale=True).status == "applied"
    assert state.applied[0].stale_override and state.decisions["C010"].stale_override


def test_stale_font_detected(syn_report):
    from openpyxl.styles import Font

    data = build_workbook(mutate=lambda wb: setattr(wb["Lead"]["A1"], "font", Font(name="Arial", size=10)))
    out = rs.apply_fix(session(syn_report, data), "C002", "apply:C002")
    assert out.status == "stale" and "font is Arial" in out.details[0]


def test_protected_and_merged_targets_are_blocked(syn_report):
    def mutate(wb):
        wb["Lead"].protection.sheet = True

    out = rs.apply_fix(session(syn_report, build_workbook(mutate=mutate)), "C011", "a")
    assert out.status == "blocked" and "protected" in out.message

    def merge(wb):
        wb["Lead"].merge_cells("A62:B63")

    out = rs.apply_fix(session(syn_report, build_workbook(mutate=merge)), "C011", "b")
    assert out.status == "blocked" and "merged range A62:B63" in out.message


def test_follow_up_decisions_validate_comments(state):
    assert rs.decide(state, "C013", D.RESOLVED, "TBD", "a").message.startswith("Enter an explanation")
    assert rs.decide(state, "C013", D.RESOLVED, "Explained in WP C.07, ref 12", "a").status == "saved"
    assert state.decisions["C013"].decided_at
    assert rs.decide(state, "C014", D.KEPT_OPEN, "", "b").status == "saved"
    assert not rs.is_reviewed(state, "C014")
    assert rs.decide(state, "C014", D.REJECTED, "", "c").status == "invalid"  # not a Follow-up decision
    assert rs.decide(state, "C002", D.RESOLVED, "long enough text", "d").status == "invalid"  # not a QF decision
    assert rs.decide(state, "C005", D.MANUALLY_RESOLVED, "", "e").status == "invalid"
    assert rs.decide(state, "C005", D.MANUALLY_RESOLVED, "Widened column H in Excel.", "e").status == "saved"


def test_reveals_and_hidden_targets(state):
    targets = rs.hidden_targets(state, state.findings["C015"])
    assert [(t.what, t.ref) for t in targets] == [("sheet", "Hidden Calc")]
    rs.toggle_reveal(state, targets[0], True, "r1")
    assert state.visibility_requests and state.output_dirty
    rs.toggle_reveal(state, targets[0], False, "r2")
    assert state.visibility_requests == []


def test_build_outputs(state):
    rs.apply_fix(state, "C009", "a1")
    rs.decide(state, "C013", D.NOT_APPLICABLE, "Scoped out in planning memo P.3", "a2")
    rs.build_outputs(state)
    assert set(state.outputs) == {
        "Synthetic_Leadsheet_reviewed.xlsx",
        "Synthetic_Leadsheet_review_log.json",
        "Synthetic_Leadsheet_review_package.zip",
    }
    assert not state.output_dirty and all(r.status != "fail" for r in state.integrity)
    logd = json.loads(state.outputs["Synthetic_Leadsheet_review_log.json"])
    assert logd["session"]["open_findings_remain"] is True
    with zipfile.ZipFile(io.BytesIO(state.outputs["Synthetic_Leadsheet_review_package.zip"])) as z:
        assert sorted(z.namelist()) == ["Synthetic_Leadsheet_review_log.json", "Synthetic_Leadsheet_reviewed.xlsx"]
    rs.undo(state, "C009", "u")
    assert state.output_dirty  # downloads are stale until rebuilt
    rs.build_outputs(state)
    assert "Synthetic_Leadsheet_reviewed.xlsx" not in state.outputs  # nothing to write any more


def test_review_only_workbook_exports_log_only(syn_report):
    data = build_workbook(extras={"_xmlsignatures/sig1.xml": b"<x/>"})
    state = session(syn_report, data)
    assert rs.apply_fix(state, "C009", "a").status == "blocked"
    rs.build_outputs(state)
    assert list(state.outputs) == ["Synthetic_Leadsheet_review_log.json", "Synthetic_Leadsheet_review_package.zip"]


def test_new_upload_gives_a_new_token(syn_report, syn_bytes):
    a = rs.upload_token(syn_bytes, json.dumps(syn_report).encode())
    syn_report["run_id"] = "other"
    assert a != rs.upload_token(syn_bytes, json.dumps(syn_report).encode())


def test_real_trigger_session(real_report, real_workbook):
    name, data = real_workbook
    state = rs.start_session(name, data, "Trigger.review.json", json.dumps(real_report).encode())
    assert not state.validation_errors and not state.name_mismatch
    rs.start_review(state)
    assert rs.progress(state)["quick_fix"] == 15 and rs.progress(state)["follow_up"] == 66
    for fid in ("C017", "C019", "C023", "C026", "C027", "C028", "C029", "C031", "C032"):
        assert rs.apply_fix(state, fid, f"a:{fid}").status == "applied", fid
    rs.build_outputs(state)
    assert [r.check for r in state.integrity if r.status == "fail"] == []
    assert "Trigger_case_Bermet_efe0_reviewed.xlsx" in state.outputs


# ---------------------------------------------------------------- workbook view


def test_active_sheet_follows_current_finding(state):
    assert state.active_sheet == "Lead"  # C002 is on Lead
    rs.jump(state, "C010")
    assert state.active_sheet == "Kunde's Daten"
    rs.go(state, "next")
    assert state.active_sheet == "Lead"  # C011


def test_workbook_level_finding_keeps_active_sheet(state):
    rs.jump(state, "C010")
    rs.jump(state, "C012")  # workbook location
    assert state.active_sheet == "Kunde's Daten"


def test_finding_cells_and_flags(state):
    assert rs.finding_cells(state, "C006") == ("Lead", ["A32", "B32"], "A32")
    assert rs.finding_cells(state, "C012") == (None, [], None)
    assert rs.finding_cells(state, "C001") == ("Lead", ["A40"], "A40")  # absence: the anchor
    flags = rs.sheet_flags(state, "Lead")
    assert {"id": "C009", "type": "quick_fix", "reviewed": False} in flags["B70"]
    assert [f["id"] for f in flags["A40"]] == ["C001", "C016"]
    assert rs.findings_at(state, "Lead", "A40") == ["C001", "C016"]


def test_flags_mark_reviewed(state):
    rs.decide(state, "C001", D.RESOLVED, "Conclusion added in row 41.", "x")
    assert rs.sheet_flags(state, "Lead")["A40"][0] == {"id": "C001", "type": "follow_up", "reviewed": True}


def test_large_range_highlights_only_corners(state, monkeypatch):
    from review_app import limits

    monkeypatch.setattr(limits, "RANGE_HIGHLIGHT_MAX", 3)
    assert rs.finding_cells(state, "C013") == ("Lead", ["C47", "C50"], "C47")


def test_flags_on_merged_interior_go_to_anchor(syn_report, syn_bytes):
    syn_report["rules"][0]["findings"][0]["locations"] = [
        {"kind": "cell", "sheet": "Lead", "cell": "C2", "value": None}
    ]
    state = session(syn_report, syn_bytes)
    assert rs.finding_cells(state, "C001") == ("Lead", ["A2"], "A2")
    assert "C001" in [f["id"] for f in rs.sheet_flags(state, "Lead")["A2"]]


def test_open_cell_prefers_current_tab_and_open(state):
    rs.set_tab(state, Resolution.FOLLOW_UP)
    assert rs.open_cell(state, "Lead", "A40") == "C001"
    rs.decide(state, "C001", D.RESOLVED, "Conclusion added in row 41.", "x")
    assert rs.open_cell(state, "Lead", "A40") == "C016"
    assert rs.open_cell(state, "Lead", "Z999") is None


def test_open_cell_on_quoted_sheet_name(state):
    assert rs.open_cell(state, "Kunde's Daten", "C75") == "C010"
    assert state.active_sheet == "Kunde's Daten" and state.tab == Resolution.QUICK_FIX


def test_set_active_sheet(state):
    rs.set_active_sheet(state, "Prüfung – Übersicht")
    assert state.active_sheet == "Prüfung – Übersicht"
    rs.set_active_sheet(state, "Nope")
    assert state.active_sheet == "Prüfung – Übersicht"


def test_huge_range_is_cheap_and_uses_corners(syn_report, syn_bytes):
    import time

    syn_report["rules"][5]["findings"][0]["locations"] = [{"kind": "range", "sheet": "Lead", "range": "A1:XFD1048576"}]
    state = session(syn_report, syn_bytes)
    started = time.perf_counter()
    assert rs.finding_cells(state, "C013") == ("Lead", ["A1", "XFD1048576"], "A1")
    rs.sheet_flags(state, "Lead")
    assert time.perf_counter() - started < 0.5


def test_filters_limit_flags_and_counts(state):
    full = rs.progress(state)
    rs.set_filters(state, rules=["QLT-MTH-01"])
    flagged = {i["id"] for items in rs.sheet_flags(state, "Lead").values() for i in items}
    assert flagged <= {"C009"} and "C001" not in flagged
    p = rs.progress(state, filtered=True)
    assert p["total"] == 1 and p["quick_fix"] == 1 and p["follow_up"] == 0
    assert rs.progress(state) == full  # the unfiltered counts (completion page) do not change
    assert rs.filters_on(state)
    rs.set_filters(state, rules=[])
    assert not rs.filters_on(state) and rs.progress(state, filtered=True) == full
