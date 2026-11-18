"""Skill-validator mode: labels, missed problems, counting and metrics. Pure logic, no Streamlit.

TP = findings labelled real, FP = labelled false alarm, FN = accepted misses. Duplicate and unlabelled findings
are not counted. A zero denominator gives None (shown as "n/a")."""

from __future__ import annotations

import re
from enum import StrEnum
from typing import TYPE_CHECKING, Literal, NamedTuple

from pydantic import BaseModel

from review_app.addresses import format_ref, parse_range
from review_app.models import NormalizedFinding, utc_now
from review_app.workbook import open_reader

if TYPE_CHECKING:
    from review_app.review_session import SessionState

OTHER = "OTHER"


class Label(StrEnum):
    REAL = "real"
    FALSE_ALARM = "false_alarm"
    DUPLICATE = "duplicate"


LABEL_TEXT: dict[Label, str] = {
    Label.REAL: "Real problem",
    Label.FALSE_ALARM: "False alarm",
    Label.DUPLICATE: "Duplicate",
}


class Miss(BaseModel):
    id: str
    sheet: str
    ref: str
    rule_id: str
    note: str = ""
    source: Literal["manual", "answer_key"] = "manual"


class AnswerKeyInfo(BaseModel):
    tags: int
    matched: int


class ValidationState(BaseModel):
    labels: dict[str, Label] = {}
    comments: dict[str, str] = {}
    misses: list[Miss] = []
    suggested: list[Miss] = []
    dismissed: set[str] = set()
    validator: str = ""
    started_at: str | None = None
    selection: tuple[str, str] | None = None
    answer_key: AnswerKeyInfo | None = None
    last_miss_no: int = 0  # highest miss number ever issued; removed ids are never reused


def set_label(v: ValidationState, fid: str, label: Label | None) -> None:
    if label is None:
        v.labels.pop(fid, None)
    else:
        v.labels[fid] = label


def set_comment(v: ValidationState, fid: str, text: str) -> None:
    if text:
        v.comments[fid] = text
    else:
        v.comments.pop(fid, None)


def _next_id(v: ValidationState) -> str:
    ids = [*(m.id for m in (*v.misses, *v.suggested)), *v.dismissed]
    nums = [int(i[1:]) for i in ids if i[1:].isdigit()]
    v.last_miss_no = max(v.last_miss_no, *nums, 0) + 1
    return f"M{v.last_miss_no:03d}"


def _key(m: Miss) -> tuple[str, str, str]:
    return (m.sheet, m.ref.upper(), m.rule_id)


def _rows(ref: str) -> range:
    b = parse_range(ref)
    return range(b.min_row, b.max_row + 1) if b else range(0)


def _row_cover(misses: list[Miss], sheet: str, ref: str, rule_id: str) -> Miss | None:
    """A miss of the same rule on the same sheet that shares a row with ref. One rule tag on one row is one
    trigger, so a second miss there would count it twice."""
    rows = set(_rows(ref))
    return next((m for m in misses if m.sheet == sheet and m.rule_id == rule_id and rows & set(_rows(m.ref))), None)


def add_miss(
    v: ValidationState, sheet: str, ref: str, rule_id: str, note: str, source: Literal["manual", "answer_key"] = "manual"
) -> Miss | str:
    if any(_key(m) == (sheet, ref.upper(), rule_id) for m in v.misses):
        return f"{sheet}!{ref.upper()} is already listed as a missed problem for {rule_id}."
    keyed = _row_cover([m for m in v.misses if m.source == "answer_key"], sheet, ref, rule_id)
    if keyed is not None:
        return (
            f"{sheet}!{ref.upper()} is on a row already listed as a missed problem for {rule_id} "
            f"(answer key, {keyed.id})."
        )
    miss = Miss(id=_next_id(v), sheet=sheet, ref=ref, rule_id=rule_id, note=note, source=source)
    v.misses.append(miss)
    return miss


def remove_miss(v: ValidationState, miss_id: str) -> None:
    v.misses = [m for m in v.misses if m.id != miss_id]


def accept_suggestion(v: ValidationState, miss_id: str) -> str | None:
    """Confirm a suggestion as a miss. A row already listed as a miss of the same rule is not counted twice: the
    suggestion is dropped (dismissed) and an error text is returned."""
    for m in v.suggested:
        if m.id == miss_id:
            v.suggested = [s for s in v.suggested if s.id != miss_id]
            if any(_key(x) == _key(m) for x in v.misses):
                v.dismissed.add(miss_id)
                return f"{m.sheet}!{m.ref.upper()} is already listed as a missed problem for {m.rule_id}."
            cover = _row_cover(v.misses, m.sheet, m.ref, m.rule_id)
            if cover is not None:
                v.dismissed.add(miss_id)
                return (
                    f"{m.sheet}!{m.ref.upper()} is on a row already listed as a missed problem for {m.rule_id} "
                    f"({cover.id}, {format_ref(cover.sheet, cover.ref)})."
                )
            v.misses.append(m)
            return None
    return None


def dismiss_suggestion(v: ValidationState, miss_id: str) -> None:
    if any(m.id == miss_id for m in v.suggested):
        v.suggested = [s for s in v.suggested if s.id != miss_id]
        v.dismissed.add(miss_id)


def unlabelled(order: list[str], v: ValidationState) -> list[str]:
    return [fid for fid in order if fid not in v.labels]


class Outcomes(NamedTuple):
    tp: int
    fp: int
    fn: int
    duplicates: int
    unlabelled: int


def outcomes(order: list[str], v: ValidationState) -> Outcomes:
    labels = [v.labels.get(fid) for fid in order]
    return Outcomes(
        tp=labels.count(Label.REAL),
        fp=labels.count(Label.FALSE_ALARM),
        fn=len(v.misses),
        duplicates=labels.count(Label.DUPLICATE),
        unlabelled=labels.count(None),
    )


class Metrics(NamedTuple):
    recall: float | None
    precision: float | None
    f2: float | None
    f1: float | None


def metrics(o: Outcomes) -> Metrics:
    r = o.tp / (o.tp + o.fn) if o.tp + o.fn else None
    p = o.tp / (o.tp + o.fp) if o.tp + o.fp else None
    f2 = f1 = None
    if r is not None and p is not None:
        f2 = 5 * p * r / (4 * p + r) if 4 * p + r else 0.0
        f1 = 2 * p * r / (p + r) if p + r else 0.0
    return Metrics(r, p, f2, f1)


def per_rule(findings: dict[str, NormalizedFinding], order: list[str], v: ValidationState) -> list[dict]:
    counts: dict[str, list[int]] = {}

    def row(rule: str) -> list[int]:
        return counts.setdefault(rule, [0, 0, 0])

    for fid in order:
        label = v.labels.get(fid)
        if label is Label.REAL:
            row(findings[fid].rule.rule_id)[0] += 1
        elif label is Label.FALSE_ALARM:
            row(findings[fid].rule.rule_id)[1] += 1
    for m in v.misses:
        row(m.rule_id)[2] += 1
    rows = []
    for rule in sorted(counts, key=lambda r: (r == OTHER, r)):
        tp, fp, fn = counts[rule]
        m = metrics(Outcomes(tp, fp, fn, 0, 0))
        rows.append({"rule_id": rule, "tp": tp, "fp": fp, "fn": fn, "recall": m.recall, "precision": m.precision, "f2": m.f2})
    return rows


def fmt(x: float | None) -> str:
    return "n/a" if x is None else f"{x:.2f}"


_TAG = re.compile(r"[A-Z]{3}-[A-Z]{2,3}-\d\d|CON-\d\d")


class Tag(NamedTuple):
    rule_id: str
    sheet: str
    row: int


def find_tags(data: bytes, sheet_names: list[str], known_rules: set[str]) -> list[Tag]:
    """Rule IDs written in column A (answer-key tags); one tag per rule per row, unknown IDs skipped."""
    reader = open_reader(data)
    tags: list[Tag] = []
    for sheet in sheet_names:
        ws = reader.worksheet(sheet)
        if ws is None:
            continue
        for (cell,) in ws.iter_rows(min_col=1, max_col=1):
            if not isinstance(cell.value, str):
                continue
            seen: set[str] = set()
            for rule in _TAG.findall(cell.value):
                if rule in known_rules and rule not in seen:
                    seen.add(rule)
                    tags.append(Tag(rule, sheet, cell.row))
    return tags


def finding_rows(f: NormalizedFinding) -> set[tuple[str, int]]:
    """(sheet, row) of every location of a finding, all rows of a range. A finding without location gives none."""
    rows: set[tuple[str, int]] = set()
    for loc in f.locations:
        b = parse_range(loc.ref) if loc.sheet else None
        if loc.sheet and b:
            rows.update((loc.sheet, r) for r in range(b.min_row, b.max_row + 1))
    return rows


def load_answer_key(
    v: ValidationState,
    data: bytes,
    sheet_names: list[str],
    findings: dict[str, NormalizedFinding],
    order: list[str],
    known_rules: set[str],
) -> None:
    """Fill v.answer_key and v.suggested from the column-A tags. Rebuilds from scratch, so calling it twice gives the same result; no tags leaves both empty."""
    old_ids = {_key(m): m.id for m in v.suggested}  # a rebuilt suggestion keeps its id
    v.answer_key = None
    v.suggested = []
    tags = find_tags(data, sheet_names, known_rules)
    if not tags:
        return
    covered = {(findings[fid].rule.rule_id, *row) for fid in order for row in finding_rows(findings[fid])}
    matched = 0
    for t in tags:
        if (t.rule_id, t.sheet, t.row) in covered:
            matched += 1
            continue
        v.suggested.append(
            Miss(
                id=old_ids.get((t.sheet, f"A{t.row}", t.rule_id)) or _next_id(v),
                sheet=t.sheet,
                ref=f"A{t.row}",
                rule_id=t.rule_id,
                note="Tagged in column A; no finding of this rule on this row",
                source="answer_key",
            )
        )
    v.answer_key = AnswerKeyInfo(tags=len(tags), matched=matched)


REAL_WORKPAPER_NOTE = "Recall is measured against the problems the validator found, not a complete answer key."


def _cell(text: str) -> str:
    """Text for a Markdown table cell: pipes escaped, line breaks flattened."""
    return " ".join(text.replace("|", "\\|").split())


def _location(state: SessionState, fid: str) -> str:
    # Imported here: review_session imports this module, so a top-level import would be circular.
    from review_app import review_session as rs

    sheet, cells, _ = rs.finding_cells(state, fid)
    if sheet is None:
        return "-"
    return format_ref(sheet, cells[0]) if cells else sheet


def report_markdown(state: SessionState) -> str:
    v, meta = state.validation, state.report_meta
    o = outcomes(state.order, v)
    m = metrics(o)
    out = [
        f"# Skill validation: {_cell(state.original_name)}",
        "",
        f"- Workbook: {_cell(state.original_name)}",
        f"- Run ID: {meta.run_id if meta else 'n/a'}",
        f"- Skill version: {meta.skill_version if meta else 'n/a'}",
        f"- Schema: {meta.schema_version if meta else 'n/a'}",
        f"- Reviewed at: {meta.reviewed_at if meta else 'n/a'}",
        f"- Validated at: {utc_now()}",
        f"- Validator: {_cell(v.validator) or 'n/a'}",
        "",
        "## Score",
        "",
        f"- F2: {fmt(m.f2)}",
        f"- Recall: {fmt(m.recall)}",
        f"- Precision: {fmt(m.precision)}",
        f"- F1 (for reference): {fmt(m.f1)}",
        "",
        "## Outcomes",
        "",
        "| | Problem present | No problem |",
        "|---|---|---|",
        f"| Skill reported | TP {o.tp} | FP {o.fp} |",
        f"| Skill did not report | FN {o.fn} | TN: not counted (not needed for F2) |",
        "",
        f"Duplicates: {o.duplicates}. Unlabelled: {o.unlabelled}.",
        "",
        "## Per rule",
        "",
        "| Rule | TP | FP | FN | Recall | Precision | F2 |",
        "|---|---|---|---|---|---|---|",
    ]
    for r in per_rule(state.findings, state.order, v):
        out.append(
            f"| {r['rule_id']} | {r['tp']} | {r['fp']} | {r['fn']} | {fmt(r['recall'])} | {fmt(r['precision'])} | {fmt(r['f2'])} |"
        )
    out += ["", "## Findings", "", "| ID | Rule | Location | Label | Comment |", "|---|---|---|---|---|"]
    for fid in state.order:
        label = LABEL_TEXT[v.labels[fid]] if fid in v.labels else "unlabelled"
        out.append(
            f"| {fid} | {state.findings[fid].rule.rule_id} | {_cell(_location(state, fid))} | {label} | {_cell(v.comments.get(fid, ''))} |"
        )
    out += ["", "## Missed problems", ""]
    if v.misses:
        out += ["| ID | Location | Rule | Note | Source |", "|---|---|---|---|---|"]
        for ms in v.misses:
            out.append(
                f"| {ms.id} | {_cell(format_ref(ms.sheet, ms.ref))} | {ms.rule_id} | {_cell(ms.note)} | {ms.source} |"
            )
    else:
        out.append("None recorded.")
    out += ["", "## Notes", "", "- Method: docs/VALIDATION_METHODOLOGY.md"]
    if v.answer_key:
        out.append(f"- Answer key: {v.answer_key.tags} tags found, {v.answer_key.matched} matched")
    else:
        out.append(f"- {REAL_WORKPAPER_NOTE}")
    return "\n".join(out) + "\n"
