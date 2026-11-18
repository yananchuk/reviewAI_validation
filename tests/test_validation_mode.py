"""Skill-validator mode: labels, misses, counting and metrics (pure logic)."""

import json
from collections import Counter

import pytest
from conftest import SYN_NAME, build_workbook

from review_app import review_session as rs
from review_app import skill_validation as val
from review_app.skill_validation import Label, Metrics, Miss, Outcomes, ValidationState


@pytest.fixture
def session(syn_report, syn_bytes):
    return rs.start_session(SYN_NAME, syn_bytes, "r.review.json", json.dumps(syn_report).encode())


def test_outcomes_count_labels_and_misses(session):
    v, order = ValidationState(), session.order
    for fid in order[:3]:
        val.set_label(v, fid, Label.REAL)
    val.set_label(v, order[3], Label.FALSE_ALARM)
    val.set_label(v, order[4], Label.DUPLICATE)
    val.add_miss(v, "Lead", "C85", "R1", "")
    val.add_miss(v, "Lead", "C86", "OTHER", "")
    assert val.outcomes(order, v) == Outcomes(tp=3, fp=1, fn=2, duplicates=1, unlabelled=len(order) - 5)
    val.set_label(v, order[0], None)
    assert order[0] in val.unlabelled(order, v)


def test_metrics_bermet_numbers():
    m = val.metrics(Outcomes(36, 3, 14, 0, 0))
    p, r = 36 / 39, 36 / 50
    assert val.fmt(m.recall) == "0.72" and val.fmt(m.precision) == "0.92"
    assert val.fmt(m.f2) == val.fmt(5 * p * r / (4 * p + r))
    assert m.f1 is not None


def test_metrics_none_when_denominator_zero():
    assert val.metrics(Outcomes(0, 0, 0, 0, 0)) == Metrics(None, None, None, None)
    assert val.fmt(None) == "n/a"


def test_duplicate_miss_is_refused():
    v = ValidationState()
    assert isinstance(val.add_miss(v, "Lead", "C85", "R1", "x"), Miss)
    assert isinstance(val.add_miss(v, "Lead", "C85", "R1", "x"), str)
    assert isinstance(val.add_miss(v, "Lead", "c85", "R1", "x"), str)
    assert len(v.misses) == 1


def test_suggestions_count_only_when_confirmed(session):
    v = ValidationState()
    v.suggested = [
        Miss(id="M001", sheet="Lead", ref="A1", rule_id="R1", source="answer_key"),
        Miss(id="M002", sheet="Lead", ref="A2", rule_id="R1", source="answer_key"),
    ]
    assert val.outcomes(session.order, v).fn == 0
    val.accept_suggestion(v, "M001")
    assert val.outcomes(session.order, v).fn == 1
    assert [m.id for m in v.misses] == ["M001"] and [m.id for m in v.suggested] == ["M002"]
    val.dismiss_suggestion(v, "M002")
    assert "M002" in v.dismissed and not v.suggested and val.outcomes(session.order, v).fn == 1
    assert val.add_miss(v, "Lead", "Z9", "R1", "").id == "M003"  # ids are never reused


def test_per_rule_rows(session):
    by_rule = Counter(f.rule.rule_id for f in session.findings.values())
    x = next(r for r, n in by_rule.items() if n >= 2)
    ids = [fid for fid in session.order if session.findings[fid].rule.rule_id == x]
    v = ValidationState()
    val.set_label(v, ids[0], Label.REAL)
    val.set_label(v, ids[1], Label.FALSE_ALARM)
    val.add_miss(v, "Lead", "A1", "ZZ9", "")
    val.add_miss(v, "Lead", "A2", "OTHER", "")
    rows = val.per_rule(session.findings, session.order, v)
    by = {r["rule_id"]: r for r in rows}
    assert (by[x]["tp"], by[x]["fp"], by[x]["fn"]) == (1, 1, 0)
    assert by["ZZ9"]["fn"] == 1
    assert rows[-1]["rule_id"] == "OTHER" and [r["rule_id"] for r in rows[:-1]] == sorted(r["rule_id"] for r in rows[:-1])


def test_accept_suggestion_refuses_a_second_miss_on_the_same_cell():
    v = ValidationState()
    val.add_miss(v, "Lead", "a5", "R1", "")
    v.suggested = [Miss(id="M002", sheet="Lead", ref="A5", rule_id="R1", source="answer_key")]
    assert isinstance(val.accept_suggestion(v, "M002"), str)
    assert [m.id for m in v.misses] == ["M001"] and not v.suggested and "M002" in v.dismissed
    v.suggested = [Miss(id="M003", sheet="Lead", ref="A6", rule_id="R1", source="answer_key")]
    assert val.accept_suggestion(v, "M003") is None and len(v.misses) == 2


def test_miss_ids_are_not_reused_after_dismissal():
    v = ValidationState(dismissed={"M007"})
    v.suggested = [Miss(id="M003", sheet="Lead", ref="A1", rule_id="R1", source="answer_key")]
    assert val.add_miss(v, "Lead", "B1", "R1", "").id == "M008"


def test_metrics_both_zero_gives_zero_f_scores():
    m = val.metrics(Outcomes(0, 2, 3, 0, 0))
    assert (m.recall, m.precision, m.f2, m.f1) == (0.0, 0.0, 0.0, 0.0)


def _tagged(wb):
    ws = wb["Lead"]
    ws["A70"] = "QLT-MTH-01"
    ws["A63"] = "QLT-RLF-01, QLT-SPL-01"
    ws["A64"] = "not a tag"


def test_find_tags_reads_column_a_only_known_rules():
    data = build_workbook(mutate=_tagged)
    tags = val.find_tags(data, ["Lead"], {"QLT-MTH-01", "QLT-SPL-01"})
    assert set(tags) == {("QLT-MTH-01", "Lead", 70), ("QLT-SPL-01", "Lead", 63)} and len(tags) == 2
    assert val.Tag("QLT-MTH-01", "Lead", 70) in tags


def test_answer_key_matches_and_suggests(syn_report):
    data = build_workbook(mutate=_tagged)
    s = rs.start_session(SYN_NAME, data, "r.review.json", json.dumps(syn_report).encode())
    known = {"QLT-MTH-01", "QLT-SPL-01", "QLT-RLF-01"}
    v = ValidationState()
    val.load_answer_key(v, data, ["Lead"], s.findings, s.order, known)
    assert v.answer_key == val.AnswerKeyInfo(tags=3, matched=2)
    # MTH-01 on row 70 and SPL-01 on row 63 have findings; RLF-01 on row 63 has none
    assert {(m.rule_id, m.ref) for m in v.suggested} == {("QLT-RLF-01", "A63")}
    assert all(m.source == "answer_key" and m.sheet == "Lead" for m in v.suggested)
    assert len(v.suggested) == v.answer_key.tags - v.answer_key.matched
    assert len({m.id for m in v.suggested}) == len(v.suggested)


def test_no_tags_no_answer_key(session, syn_bytes):
    v = ValidationState()
    val.load_answer_key(v, syn_bytes, ["Lead"], session.findings, session.order, {"QLT-MTH-01"})
    assert v.answer_key is None and v.suggested == []


def test_unlocated_finding_counts_and_is_never_matched(syn_report):
    rule = None
    s0 = rs.start_session(SYN_NAME, build_workbook(), "r.review.json", json.dumps(syn_report).encode())
    rule = s0.findings["C012"].rule.rule_id
    data = build_workbook(mutate=lambda wb: wb["Lead"].__setitem__("A90", rule))
    session = rs.start_session(SYN_NAME, data, "r.review.json", json.dumps(syn_report).encode())
    assert val.finding_rows(session.findings["C012"]) == set()
    v = ValidationState()
    val.load_answer_key(v, data, ["Lead"], session.findings, session.order, {rule})
    assert v.answer_key == val.AnswerKeyInfo(tags=1, matched=0) and [m.ref for m in v.suggested] == ["A90"]
    val.set_label(v, "C012", Label.REAL)
    assert val.outcomes(session.order, v).tp == 1


def test_two_rules_on_one_row_can_both_be_accepted():
    v = ValidationState()
    v.suggested = [
        Miss(id="M001", sheet="Lead", ref="A63", rule_id="QLT-RLF-01", source="answer_key"),
        Miss(id="M002", sheet="Lead", ref="A63", rule_id="QLT-SPL-01", source="answer_key"),
    ]
    assert val.accept_suggestion(v, "M001") is None and val.accept_suggestion(v, "M002") is None
    assert len(v.misses) == 2 and val.outcomes([], v).fn == 2
    assert isinstance(val.add_miss(v, "Lead", "a63", "QLT-SPL-01", ""), str)
    assert isinstance(val.add_miss(v, "Lead", "A63", "QLT-MTH-01", ""), Miss)


def test_load_answer_key_is_idempotent(syn_report):
    data = build_workbook(mutate=_tagged)
    s = rs.start_session(SYN_NAME, data, "r.review.json", json.dumps(syn_report).encode())
    known = {"QLT-MTH-01", "QLT-SPL-01", "QLT-RLF-01"}
    v = ValidationState()
    val.load_answer_key(v, data, ["Lead"], s.findings, s.order, known)
    first = v.model_copy(deep=True)
    val.load_answer_key(v, data, ["Lead"], s.findings, s.order, known)
    assert v.answer_key == first.answer_key and v.suggested == first.suggested


def _report_session(syn_report, syn_bytes, with_key=False):
    state = rs.start_session(SYN_NAME, syn_bytes, "r.review.json", json.dumps(syn_report).encode())
    rs.start_validation(state)
    v = state.validation
    v.validator = "Ann"
    val.set_label(v, state.order[0], Label.REAL)
    val.set_label(v, state.order[1], Label.FALSE_ALARM)
    val.set_comment(v, state.order[1], "a|b\nc")
    val.add_miss(v, "Lead", "B63", "OTHER", "x|y\nz")
    v.answer_key = val.AnswerKeyInfo(tags=5, matched=3) if with_key else None
    return state


def test_report_markdown_sections(syn_report, syn_bytes):
    md = val.report_markdown(_report_session(syn_report, syn_bytes))
    assert "| Skill reported | TP 1 | FP 1 |" in md and "| Skill did not report | FN 1 |" in md
    assert "Duplicates: 0." in md and "- Validator: Ann" in md
    heads = ["# Skill validation", "## Score", "## Outcomes", "## Per rule", "## Findings", "## Missed problems", "## Notes"]
    pos = [md.index(h) for h in heads]
    assert pos == sorted(pos)
    assert "a\\|b c" in md and "x\\|y z" in md
    assert "(for reference)" in md and "not counted (not needed for F2)" in md


def test_report_real_workpaper_note(syn_report, syn_bytes):
    note = "Recall is measured against the problems the validator found, not a complete answer key."
    md = val.report_markdown(_report_session(syn_report, syn_bytes))
    assert note in md and "Answer key:" not in md
    md = val.report_markdown(_report_session(syn_report, syn_bytes, with_key=True))
    assert "Answer key: 5 tags found, 3 matched" in md and note not in md


def test_report_with_nothing_labelled(syn_report, syn_bytes):
    state = rs.start_session(SYN_NAME, syn_bytes, "r.review.json", json.dumps(syn_report).encode())
    rs.start_validation(state)
    md = val.report_markdown(state)
    assert "- F2: n/a" in md and "- Recall: n/a" in md and "- Precision: n/a" in md
    assert "| Skill reported | TP 0 | FP 0 |" in md


def test_suggestion_on_a_row_with_a_manual_miss_of_the_same_rule_is_refused():
    v = ValidationState()
    val.add_miss(v, "Lead", "C85", "QLT-RLF-01", "")
    v.suggested = [Miss(id="M002", sheet="Lead", ref="A85", rule_id="QLT-RLF-01", source="answer_key")]
    assert isinstance(val.accept_suggestion(v, "M002"), str)
    assert [m.id for m in v.misses] == ["M001"] and "M002" in v.dismissed and val.outcomes([], v).fn == 1
    # a manual range spanning the row also covers it; another rule on that row does not
    v.suggested = [
        Miss(id="M003", sheet="Lead", ref="A86", rule_id="QLT-RLF-01", source="answer_key"),
        Miss(id="M004", sheet="Lead", ref="A85", rule_id="QLT-SPL-01", source="answer_key"),
    ]
    val.add_miss(v, "Lead", "B84:D87", "QLT-MTH-01", "")
    val.add_miss(v, "Other", "C86", "QLT-RLF-01", "")
    assert val.accept_suggestion(v, "M003") is None and val.accept_suggestion(v, "M004") is None


def test_manual_miss_on_a_row_with_a_confirmed_answer_key_miss_is_refused():
    v = ValidationState()
    v.suggested = [Miss(id="M001", sheet="Lead", ref="A85", rule_id="QLT-RLF-01", source="answer_key")]
    assert val.accept_suggestion(v, "M001") is None
    assert isinstance(val.add_miss(v, "Lead", "C85", "QLT-RLF-01", ""), str)
    assert isinstance(val.add_miss(v, "Lead", "B84:C86", "QLT-RLF-01", ""), str)
    assert val.outcomes([], v).fn == 1
    assert isinstance(val.add_miss(v, "Lead", "C85", "QLT-SPL-01", ""), Miss)
    assert isinstance(val.add_miss(v, "Lead", "C86", "QLT-RLF-01", ""), Miss)
    assert isinstance(val.add_miss(v, "Other", "C85", "QLT-RLF-01", ""), Miss)


def test_miss_ids_are_not_reused_after_remove():
    v = ValidationState()
    assert val.add_miss(v, "Lead", "B1", "R1", "").id == "M001"
    val.remove_miss(v, "M001")
    assert val.add_miss(v, "Lead", "B2", "R1", "").id == "M002"


def test_rule_titles_kept_for_every_report_rule(session, syn_report):
    assert session.rule_titles == {str(r["rule_id"]): r.get("title", "") for r in syn_report["rules"]}
    assert set(session.rule_titles) == set(session.rule_ids)
