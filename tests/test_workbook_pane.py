"""Workbook pane: the data sent to the browser and the click handlers (the component itself runs in the browser)."""

import json
import re

import pytest
from conftest import SYN_NAME

from review_app import review_session as rs
from review_app.skill_validation import Miss
from review_app.ui import state as ui_state
from review_app.ui import workbook_pane


@pytest.fixture
def state(syn_report, syn_bytes, monkeypatch):
    s = rs.start_session(SYN_NAME, syn_bytes, "r.review.json", json.dumps(syn_report).encode())
    rs.start_review(s)
    monkeypatch.setattr(ui_state.st, "session_state", {"rs": s})
    return s


def test_cell_click_opens_finding(state):
    workbook_pane.handle_cell({"sheet": "Kunde's Daten", "cell": "C75"})
    assert state.current_id == "C010" and state.active_sheet == "Kunde's Daten"


def test_click_on_plain_cell_changes_nothing(state):
    workbook_pane.handle_cell({"sheet": "Lead", "cell": "Z99"})
    workbook_pane.handle_cell(None)
    workbook_pane.handle_cell({"sheet": 5})
    assert state.current_id == "C002" and state.active_sheet == "Lead"


def test_sheet_click_switches_sheet(state):
    workbook_pane.handle_sheet("Hidden Calc")
    assert state.active_sheet == "Hidden Calc"
    workbook_pane.handle_sheet("<img src=x>")
    assert state.active_sheet == "Hidden Calc"


def test_pane_data(state):
    data = workbook_pane.pane_data(state, formulas=True)
    json.dumps(data)
    assert data["payload"]["sheet"] == "Lead" and data["formulas"] is True
    assert data["target"] == "A1" and data["highlight"] == ["A1"]
    assert {"id": "C009", "type": "quick_fix", "reviewed": False} in data["flags"]["B70"]
    tabs = {t["name"]: t for t in data["tabs"]}
    assert tabs["Lead"]["open"] >= 1 and tabs["Hidden Calc"]["state"] == "hidden" and tabs["Hidden Calc"]["open"] == 1


def test_pane_data_for_a_finding_on_another_sheet(state):
    state.active_sheet = "Locked"  # the user looks at another sheet
    data = workbook_pane.pane_data(state, formulas=False)
    assert data["payload"]["sheet"] == "Locked" and data["target"] is None and data["highlight"] == []


def test_component_code_never_injects_markup():
    assert "innerHTML" not in workbook_pane.JS and "outerHTML" not in workbook_pane.JS
    assert "insertAdjacentHTML" not in workbook_pane.JS and "eval(" not in workbook_pane.JS
    assert "<script" not in workbook_pane.HTML.lower() and "<script" not in workbook_pane.CSS.lower()
    assert re.search(r"textContent", workbook_pane.JS)


def test_finding_in_hidden_row_is_shown_on_the_next_visible_row(syn_report, syn_bytes, monkeypatch):
    syn_report["rules"][0]["findings"][0]["locations"] = [
        {"kind": "cell", "sheet": "Lead", "cell": "A5", "value": None}
    ]
    s = rs.start_session(SYN_NAME, syn_bytes, "r.review.json", json.dumps(syn_report).encode())
    rs.start_review(s)
    monkeypatch.setattr(ui_state.st, "session_state", {"rs": s})
    rs.jump(s, "C001")
    data = workbook_pane.pane_data(s, formulas=False)
    assert data["target"] == "A6" and data["highlight"] == ["A6"]
    assert "C001" in [f["id"] for f in data["flags"]["A6"]]
    workbook_pane.handle_cell({"sheet": "Lead", "cell": "A6"})
    assert s.current_id == "C001"


def test_pane_data_select_mode_and_misses(state):
    state.validation.misses.append(Miss(id="M001", sheet="Lead", ref="B63", rule_id="OTHER"))
    state.validation.misses.append(Miss(id="M002", sheet="Locked", ref="A1", rule_id="OTHER"))
    data = workbook_pane.pane_data(state, False, select_mode=True)
    json.dumps(data)
    assert data["select_mode"] is True and data["misses"]["B63"] == ["M001"]
    assert "A1" not in data["misses"]
    assert workbook_pane.pane_data(state, False)["select_mode"] is False


def test_pane_data_range_miss_flagged_on_top_left_visible_cell(state):
    state.validation.misses.append(Miss(id="M001", sheet="Lead", ref="A5:B7", rule_id="OTHER"))  # row 5 is hidden
    data = workbook_pane.pane_data(state, False, select_mode=True)
    assert data["misses"] == {"A6": ["M001"]}


def test_selection_handler_stores_range(state):
    workbook_pane.handle_selection({"sheet": "Lead", "range": "C85:D86"})
    assert state.validation.selection == ("Lead", "C85:D86")
    workbook_pane.handle_selection({"sheet": "Lead", "range": "B7"})
    assert state.validation.selection == ("Lead", "B7")


def test_selection_handler_rejects_bad_input(state):
    for event in ({"sheet": "Nope", "range": "A1"}, {"sheet": "Lead", "range": "A1;DROP"}, None, {"sheet": 5}):
        workbook_pane.handle_selection(event)
    assert state.validation.selection is None


def test_js_has_select_mode_and_review_path_unchanged():
    assert "select_mode" in workbook_pane.JS and '"selection"' in workbook_pane.JS
    assert 'setTriggerValue("cell"' in workbook_pane.JS


def test_selection_handler_refuses_huge_range(state):
    workbook_pane.handle_selection({"sheet": "Lead", "range": "A1:XFD1048576"})
    assert state.validation.selection is None


def test_huge_miss_range_renders_quickly_on_top_left_cell(state):
    import time

    state.validation.misses.append(Miss(id="M001", sheet="Lead", ref="A5:XFD1048576", rule_id="OTHER"))
    started = time.monotonic()
    data = workbook_pane.pane_data(state, False, select_mode=True)
    assert time.monotonic() - started < 5
    assert data["misses"] == {"A6": ["M001"]}  # row 5 is hidden
