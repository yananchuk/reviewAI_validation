"""Streamlit UI tests with AppTest. File uploads are not supported by AppTest, so a session is injected directly
(the same SessionState the upload page creates)."""

import json

import pytest
from conftest import APP, SYN_NAME
from streamlit.testing.v1 import AppTest

from review_app import review_session as rs
from review_app.models import DecisionValue as D
from review_app.ui.upload import REVIEW_MODE_AVAILABLE

APP_FILE = str(APP / "app.py")


def make_state(report, data, name=SYN_NAME, start=True):
    state = rs.start_session(name, data, "Synthetic_Leadsheet.review.json", json.dumps(report).encode())
    if start:
        rs.start_review(state)
    return state


def run_app(state=None):
    at = AppTest.from_file(APP_FILE, default_timeout=30)
    if state is not None:
        at.session_state["rs"] = state
    return at.run()


def texts(at):
    return " ".join(
        [m.value for m in at.markdown]
        + [m.value for m in at.text]
        + [m.value for m in at.caption]
        + [m.value for m in at.info]
        + [m.value for m in at.warning]
        + [m.value for m in at.success]
        + [m.value for m in at.error]
    )


def test_upload_page_renders_without_session():
    at = run_app()
    assert not at.exception
    assert at.title[0].value == "Review AI"
    assert "Upload both files" in texts(at)


def test_name_mismatch_gates_start(syn_report, syn_bytes):
    state = make_state(syn_report, syn_bytes, name="Other.xlsx", start=False)
    # The upload page shows the validation of the session that matches the current uploads; render it directly.
    at = AppTest.from_function(_render_validation, default_timeout=30)
    at.session_state["rs"] = state
    at.run()
    assert at.button(key="start_review").disabled
    at.checkbox(key="confirm_mismatch").check().run()
    assert state.name_mismatch_confirmed and not at.button(key="start_review").disabled
    at.button(key="start_review").click().run()
    assert state.page == "review"


def _render_validation():
    from review_app.ui.state import get_state
    from review_app.ui.upload import render_validation

    render_validation(get_state())


@pytest.fixture
def at(syn_report, syn_bytes):
    return run_app(make_state(syn_report, syn_bytes))


def test_review_workspace_shows_the_first_quick_fix(at):
    assert not at.exception
    page = texts(at)
    assert "C002" in page and "QLT\\-FMT\\-01" in page
    assert at.button(key="apply:C002") and not at.button(key="apply:C002").disabled
    assert at.radio(key="nav_tab").value == rs.Resolution.QUICK_FIX and at.selectbox(key="nav_select").value == "C002"
    assert not at.sidebar.button and not at.sidebar.radio  # no sidebar on the review page


def test_apply_then_undo(at):
    state = at.session_state["rs"]
    at.button(key="apply:C002").click().run()
    assert not at.exception
    assert state.decisions["C002"].value == D.ACCEPTED and len(state.applied) == 1
    # The view moved on; go back to C002 and undo.
    rs.jump(state, "C002")
    at.run()
    at.button(key="undo:C002").click().run()
    assert state.applied == [] and "C002" not in state.decisions


def test_applied_fix_cannot_be_applied_again(at):
    """After Apply, the card offers Undo instead of Apply; repeated actions are ignored by the session."""
    state = at.session_state["rs"]
    at.button(key="apply:C002").click().run()
    rs.jump(state, "C002")
    at.run()
    assert not [b for b in at.button if b.key == "apply:C002"] and at.button(key="undo:C002")
    assert len(state.applied) == 1


def test_manual_resolution_requires_comment(at):
    state = at.session_state["rs"]
    rs.jump(state, "C005")
    at.run()
    assert at.button(key="apply:C005").disabled  # 'widen column H or wrap text enabled' is ambiguous
    at.button(key="manual:C005").click().run()
    assert "C005" not in state.decisions and "Placeholder" in texts(at)
    at.text_area(key="comment:C005").input("Widened column H in Excel.").run()
    at.button(key="manual:C005").click().run()
    assert state.decisions["C005"].value == D.MANUALLY_RESOLVED


def test_follow_up_decision_and_navigation(at):
    state = at.session_state["rs"]
    at.radio(key="nav_tab").set_value(rs.Resolution.FOLLOW_UP).run()
    assert state.current_id == "C001"
    at.radio(key="fu_choice:C001").set_value(D.RESOLVED).run()
    at.text_area(key="comment:C001").input("TBD").run()
    at.button(key="save:C001").click().run()
    assert "C001" not in state.decisions and "Placeholder" in texts(at)
    at.text_area(key="comment:C001").input("Conclusion added in row 41 of Lead.").run()
    at.button(key="save:C001").click().run()
    assert state.decisions["C001"].value == D.RESOLVED
    at.button(key="dup:C001:C016").click().run()
    assert state.current_id == "C016"
    at.button(key="nav_prev").click().run()
    assert state.current_id == "C015"


def test_filters_keep_current_valid(at):
    state = at.session_state["rs"]
    at.multiselect(key="f_rules").set_value(["QLT-MTH-02"]).run()
    assert state.current_id == "C010" and at.button(key="apply:C010")


def test_stale_dialog_and_apply_anyway(syn_report):
    from conftest import build_workbook

    data = build_workbook(mutate=lambda wb: wb["Kunde's Daten"].__setitem__("C75", 999))
    state = make_state(syn_report, data)
    rs.jump(state, "C010")
    at = run_app(state)
    assert "has changed since this report was created" in texts(at)
    at.button(key="apply:C010").click().run()
    assert not state.applied
    at.button(key="stale_apply:C010").click().run()
    assert len(state.applied) == 1 and state.applied[0].stale_override


def test_completion_and_downloads(at):
    state = at.session_state["rs"]
    at.button(key="apply:C002").click().run()
    at.button(key="to_complete").click().run()
    assert state.page == "complete" and not at.exception
    at.button(key="generate").click().run()
    assert not state.output_dirty and "Synthetic_Leadsheet_reviewed.xlsx" in state.outputs
    assert "still open" in texts(at)


def test_hidden_location_reveal_toggle(at):
    state = at.session_state["rs"]
    rs.jump(state, "C015")
    at.run()
    key = "reveal:C015:sheet:Hidden Calc"
    at.checkbox(key=key).check().run()
    assert [r.ref for r in state.visibility_requests] == ["Hidden Calc"]
    at.checkbox(key=key).uncheck().run()
    assert state.visibility_requests == []


def test_review_page_has_workbook_and_panel(at):
    assert not at.exception
    assert any(type(e).__name__ == "UnknownElement" for e in at.main)  # the workbook component
    assert at.button(key="apply:C002") and at.button(key="to_complete")


def test_also_here_links(syn_report, syn_bytes):
    state = make_state(syn_report, syn_bytes)
    rs.jump(state, "C001")
    at = run_app(state)
    at.button(key="also:C001:C016").click().run()
    assert state.current_id == "C016"


def test_cell_chip_shows_the_finding_sheet(syn_report, syn_bytes):
    state = make_state(syn_report, syn_bytes)
    rs.jump(state, "C010")
    state.active_sheet = "Lead"  # the user browsed another sheet
    at = run_app(state)
    at.button(key="chip:C010").click().run()
    assert state.active_sheet == "Kunde's Daten"


def test_back_to_review_from_completion(at):
    state = at.session_state["rs"]
    at.button(key="to_complete").click().run()
    at.button(key="to_review").click().run()
    assert state.page == "review"


VALIDATE_OPTION = "Validate the skill (label findings and score it)"
REVIEW_OPTION = "Review the workpaper"
needs_review_mode = pytest.mark.skipif(not REVIEW_MODE_AVAILABLE, reason="review mode is switched off (to-do)")


def _validation_at(state):
    at = AppTest.from_function(_render_validation, default_timeout=30)
    at.session_state["rs"] = state
    return at.run()


def test_validate_mode_routes_to_label(syn_report, syn_bytes):
    state = make_state(syn_report, syn_bytes, start=False)
    at = _validation_at(state)
    at.radio(key="mode").set_value(VALIDATE_OPTION).run()
    assert "Read only: nothing is changed" in texts(at)
    at.button(key="start_review").click().run()
    assert state.mode == "validate" and state.page == "v_label"
    assert state.validation.started_at


@needs_review_mode
def test_review_mode_default_unchanged(syn_report, syn_bytes):
    state = make_state(syn_report, syn_bytes, start=False)
    at = _validation_at(state)
    at.button(key="start_review").click().run()
    assert state.page == "review" and state.mode == "review"


def test_step_bar_navigates(syn_report, syn_bytes):
    state = make_state(syn_report, syn_bytes, start=False)
    rs.start_validation(state)
    at = run_app(state)
    assert not at.exception
    assert "1 Label" in texts(at)
    at.button(key="step:v_check").click().run()
    assert not at.exception
    assert state.page == "v_check"


@needs_review_mode
def test_mode_switch_with_work_needs_confirmation(syn_report, syn_bytes):
    from review_app.skill_validation import Label

    state = make_state(syn_report, syn_bytes, start=False)
    rs.start_validation(state)
    fid = state.order[0]
    state.validation.labels[fid] = Label.REAL
    state.page = "upload"
    at = _validation_at(state)
    at.radio(key="mode").set_value(REVIEW_OPTION).run()
    at.button(key="start_review").click().run()
    assert at.warning and state.validation.labels == {fid: Label.REAL} and state.mode == "validate"
    at.button(key="discard_review").click().run()
    at.button(key="start_review").click().run()
    assert state.validation.labels == {} and state.mode == "review" and state.page == "review"


def _soft_reset_then_validation():
    import streamlit as st

    from review_app.ui.state import get_state, reset, set_state
    from review_app.ui.upload import MODE_VALIDATE, mode_choice, render_validation

    if "reset_done" not in st.session_state:
        # What render() does on a first upload: the radio is drawn, then the session is reset.
        st.session_state["mode"] = MODE_VALIDATE
        mode_choice()
        loaded = get_state()
        reset(clear_uploads=False)
        set_state(loaded)  # render() then loads the session for the new files
        st.session_state["reset_done"] = True
        st.session_state["kept_mode"] = st.session_state.get("mode")
    render_validation(get_state(), with_mode_choice=False)


def test_mode_choice_survives_soft_reset(syn_report, syn_bytes):
    state = make_state(syn_report, syn_bytes, start=False)
    at = AppTest.from_function(_soft_reset_then_validation, default_timeout=30)
    at.session_state["rs"] = state
    at.run()
    assert not at.exception
    assert at.session_state["kept_mode"] == VALIDATE_OPTION
    assert "Read only: nothing is changed" in texts(at)
    assert at.button(key="start_review").label == "Start validation"
    at.button(key="start_review").click().run()
    assert at.session_state["rs"].mode == "validate" and at.session_state["rs"].page == "v_label"


def test_discard_work_review_branch(syn_report, syn_bytes):
    state = make_state(syn_report, syn_bytes)
    assert rs.apply_fix(state, "C002", "apply:C002:1").status == "applied"
    assert state.applied and state.decisions
    assert state.working_bytes != state.original_bytes
    rs.discard_work(state)
    assert state.decisions == {} and state.applied == []
    assert state.working_bytes == state.original_bytes


def validator_app(syn_report, syn_bytes):
    state = make_state(syn_report, syn_bytes, start=False)
    rs.start_validation(state)
    return state, run_app(state)


def test_label_finding_and_next(syn_report, syn_bytes):
    state, at = validator_app(syn_report, syn_bytes)
    assert not at.exception and state.page == "v_label"
    at.selectbox(key="v_select").set_value("C002").run()
    at.button(key="label:real:C002").click().run()
    assert state.validation.labels["C002"] == "real"
    at.button(key="v_next_unlabelled").click().run()
    after = state.order[state.order.index("C002") + 1 :]
    expected = next(f for f in after if f not in state.validation.labels)
    assert state.current_id == expected != "C002"
    assert at.get("progress")[0].proto.text == f"1 of {len(state.order)} labelled"


def test_label_comment_saved(syn_report, syn_bytes):
    state, at = validator_app(syn_report, syn_bytes)
    at.selectbox(key="v_select").set_value("C002").run()
    at.text_area(key="v_comment:C002").set_value("ok").run()
    assert state.validation.comments["C002"] == "ok"


def test_validate_mode_has_no_fix_buttons(syn_report, syn_bytes):
    state, at = validator_app(syn_report, syn_bytes)
    keys = [b.key or "" for b in at.button]
    assert not any(k.startswith(("apply:", "decide:")) for k in keys)
    assert state.working_bytes == state.original_bytes
    # C015 points into a hidden sheet: review offers an "Unhide" checkbox, the validator must not
    at.selectbox(key="v_select").set_value("C015").run()
    assert not at.exception and state.current_id == "C015"
    assert not [c.key for c in at.checkbox if (c.key or "").startswith("reveal:")]
    assert "Cell details" in [e.label for e in at.expander]
    assert state.visibility_requests == []


def _misses_app(syn_report, syn_bytes):
    state, at = validator_app(syn_report, syn_bytes)
    state.page = "v_misses"
    return state, at.run()


def _add_miss_click(at, rule):
    at.selectbox(key="v_miss_rule").set_value(rule).run()
    at.button(key="v_add_miss").click().run()


def test_add_miss_from_selection(syn_report, syn_bytes):
    state, at = _misses_app(syn_report, syn_bytes)
    assert not at.exception
    assert at.button(key="v_add_miss").disabled
    assert "Select a cell or range in the workbook" in texts(at) and "10,000" in texts(at)
    state.validation.selection = ("Lead", "B63")
    at.run()
    rule = state.rule_ids[0]
    at.text_input(key="v_miss_note").set_value("n")
    _add_miss_click(at, rule)
    ms = state.validation.misses
    assert [(m.sheet, m.ref, m.rule_id, m.note) for m in ms] == [("Lead", "B63", rule, "n")]
    assert state.validation.selection is None
    assert at.text_input(key="v_miss_note").value == ""
    assert "rm:M001" in [b.key for b in at.button]


def test_add_same_miss_twice_warns(syn_report, syn_bytes):
    state, at = _misses_app(syn_report, syn_bytes)
    rule = state.rule_ids[0]
    state.validation.selection = ("Lead", "B63")
    at.run()
    _add_miss_click(at, rule)
    state.validation.selection = ("Lead", "B63")
    at.run()
    _add_miss_click(at, rule)
    assert len(state.validation.misses) == 1
    assert any("already listed" in w.value for w in at.warning)


def _seed_suggestions(state):
    from review_app.skill_validation import Miss

    for i, ref in ((1, "A1"), (2, "A2")):
        state.validation.suggested.append(
            Miss(id=f"M{i:03d}", sheet="Lead", ref=ref, rule_id=state.rule_ids[0], source="answer_key")
        )


def test_confirm_and_dismiss_suggestion(syn_report, syn_bytes):
    state, at = _misses_app(syn_report, syn_bytes)
    _seed_suggestions(state)
    at.run()
    assert "Suggested from the answer key (2)" in [e.label for e in at.expander]
    at.button(key="ok:M001").click().run()
    at.button(key="no:M002").click().run()
    assert [m.id for m in state.validation.misses] == ["M001"]
    assert state.validation.dismissed == {"M002"}
    assert "Suggested from the answer key (0)" in [e.label for e in at.expander]


def test_accept_duplicate_suggestion_warns(syn_report, syn_bytes):
    state, at = _misses_app(syn_report, syn_bytes)
    _seed_suggestions(state)
    state.validation.misses.append(state.validation.suggested[0].model_copy(update={"id": "M009", "source": "manual"}))
    at.run()
    at.button(key="ok:M001").click().run()
    assert any("already listed" in w.value for w in at.warning)
    assert len(state.validation.misses) == 1


def test_remove_miss(syn_report, syn_bytes):
    state, at = _misses_app(syn_report, syn_bytes)
    state.validation.selection = ("Lead", "B63")
    at.run()
    _add_miss_click(at, state.rule_ids[0])
    at.button(key="rm:M001").click().run()
    assert state.validation.misses == []


def _check_app(syn_report, syn_bytes, setup=None):
    from review_app import skill_validation as sv

    state = make_state(syn_report, syn_bytes, start=False)
    rs.start_validation(state)
    if setup:
        setup(state, sv)
    state.page = "v_check"
    return state, run_app(state)


def test_check_warns_unlabelled_and_uncommented_false_alarm(syn_report, syn_bytes):
    state, at = _check_app(
        syn_report, syn_bytes, lambda s, sv: sv.set_label(s.validation, "C002", sv.Label.FALSE_ALARM)
    )
    assert not at.exception
    t = texts(at)
    assert "not labelled" in t and "have no comment" in t


def test_check_open_link_returns_to_label(syn_report, syn_bytes):
    state, at = _check_app(syn_report, syn_bytes)
    at.button(key="open:C003").click().run()
    assert state.page == "v_label" and state.current_id == "C003"


def test_check_next_goes_to_results(syn_report, syn_bytes):
    state, at = _check_app(syn_report, syn_bytes)
    at.button(key="v_check_next").click().run()
    assert state.page == "v_results"


def _results_app(syn_report, syn_bytes, label=True):
    from review_app import skill_validation as sv

    state = make_state(syn_report, syn_bytes, start=False)
    rs.start_validation(state)
    if label:
        sv.set_label(state.validation, "C002", sv.Label.REAL)
    state.page = "v_results"
    return state, run_app(state)


def test_results_page_shows_score_and_download(syn_report, syn_bytes):
    state, at = _results_app(syn_report, syn_bytes)
    assert not at.exception
    assert "F2" in [m.label for m in at.metric]
    assert at.get("download_button")
    at.text_input(key="v_validator").set_value("Ann").run()
    assert state.validation.validator == "Ann"
    assert "Recall is measured against" in texts(at)


def test_results_with_nothing_labelled(syn_report, syn_bytes):
    _, at = _results_app(syn_report, syn_bytes, label=False)
    assert not at.exception
    assert any(m.value == "n/a" for m in at.metric)


def test_label_page_shows_the_proposed_change(syn_report, syn_bytes):
    state, at = validator_app(syn_report, syn_bytes)
    at.selectbox(key="v_select").set_value("C002").run()
    t = texts(at)
    assert "Proposed change" in t and "Report: before" in t and "Report: after" in t and "Action:" in t
    codes = [c.value for c in at.code]
    assert "font Carlito 11" in codes and "font Arial 10" in codes
    assert not [b.key for b in at.button if (b.key or "").startswith(("apply:", "reject:", "manual:", "keep:"))]


def test_validator_start_over_returns_to_upload(syn_report, syn_bytes):
    state, at = validator_app(syn_report, syn_bytes)
    assert "The validation lives only in this browser tab. Download the report before closing it." in texts(at)
    at.button(key="v_start_over").click().run()
    assert not at.exception
    fresh = at.session_state["rs"]
    assert fresh is not state and fresh.page == "upload" and fresh.mode == "review" and not fresh.findings


def test_results_page_shows_run_details_and_answer_key(syn_report, syn_bytes):
    from review_app.skill_validation import AnswerKeyInfo
    from review_app.ui.state import esc

    state, at = _results_app(syn_report, syn_bytes)
    t, meta = texts(at), state.report_meta
    assert esc(state.original_name) in t and esc(meta.run_id) in t and esc(meta.skill_version) in t
    assert esc(meta.reviewed_at) in t and "Answer key:" not in t
    state.validation.answer_key = AnswerKeyInfo(tags=5, matched=3)
    at.run()
    assert "Answer key: 5 tags found, 3 matched" in texts(at)


def test_misses_page_hides_suggestions_without_answer_key(syn_report, syn_bytes):
    from review_app.skill_validation import AnswerKeyInfo

    state, at = _misses_app(syn_report, syn_bytes)
    assert state.validation.answer_key is None
    assert not [e.label for e in at.expander if e.label.startswith("Suggested from the answer key")]
    state.validation.answer_key = AnswerKeyInfo(tags=1, matched=1)
    at.run()
    assert "Suggested from the answer key (0)" in [e.label for e in at.expander]


def test_misses_list_escapes_user_text(syn_report, syn_bytes):
    state, at = _misses_app(syn_report, syn_bytes)
    state.validation.selection = ("Lead", "B63")
    at.run()
    at.text_input(key="v_miss_note").set_value("*bold* [x](y)")
    _add_miss_click(at, "OTHER")
    line = next(m.value for m in at.markdown if m.value.startswith("`M001`"))
    assert "\\*bold\\*" in line and "\\[x\\]\\(y\\)" in line


def test_miss_rule_picker_uses_report_titles(syn_report, syn_bytes):
    state = make_state(syn_report, syn_bytes, start=False)
    rs.start_validation(state)
    state.page = "v_misses"
    rule = next(r for r in syn_report["rules"] if r.get("title"))
    state.rule_titles[rule["rule_id"]] = "Title from the rules list"
    at = run_app(state)
    assert f"{rule['rule_id']} - Title from the rules list" in at.selectbox(key="v_miss_rule").options
