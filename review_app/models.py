"""Pydantic models. The internal model never assumes sequential ids, a location per finding, existing locations,
English or simple sheet names, a single location, a change per Quick Fix, a safe change, present before/after
values, string values, or an actionable location on a Follow-up."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field


def utc_now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


class Resolution(StrEnum):
    QUICK_FIX = "quick_fix"
    FOLLOW_UP = "follow_up"


class DecisionValue(StrEnum):
    OPEN = "open"
    ACCEPTED = "accepted"  # Quick Fix applied automatically
    REJECTED = "rejected"  # Quick Fix declined
    RESOLVED = "resolved"
    NOT_APPLICABLE = "not_applicable"
    MANUALLY_RESOLVED = "manually_resolved"
    KEPT_OPEN = "kept_open"


REVIEWED_DECISIONS = frozenset(
    {
        DecisionValue.ACCEPTED,
        DecisionValue.REJECTED,
        DecisionValue.RESOLVED,
        DecisionValue.NOT_APPLICABLE,
        DecisionValue.MANUALLY_RESOLVED,
    }
)


# ---------------------------------------------------------------- report


class ReportMeta(BaseModel):
    schema_version: str
    skill_version: str
    run_id: str
    workbook: str
    reviewed_at: str
    workbook_unchanged: bool
    completion_status: str | None = None
    rules_not_completed: list[str] = []


class RuleMeta(BaseModel):
    rule_id: str
    status: str
    title: str = ""
    theme: str = ""
    what_is_checked: str = ""
    warnings: list[str] = []
    not_tested_parts: list[str] = []


class Location(BaseModel):
    kind: str
    sheet: str | None = None
    cell: str | None = None
    range: str | None = None
    anchor: str | None = None
    anchor_value: Any = None
    author: str | None = None
    drawing_name: str | None = None
    value: Any = None
    has_value: bool = False  # 'value' key present in the report (absent and null differ in 0.3)

    @property
    def ref(self) -> str | None:
        """First non-empty reference: cell, range, anchor."""
        return self.cell or self.range or self.anchor

    @property
    def navigable(self) -> bool:
        return self.kind in {"cell", "range", "comment", "absence"} and bool(self.sheet) and bool(self.ref)


class ProposedChange(BaseModel):
    sheet: str | None = None
    cell: str | None = None
    before: Any = None
    after: Any = None


class FontSpec(BaseModel):
    """Only the properties named in the report. None = not part of the change."""

    model_config = ConfigDict(frozen=True)

    name: str | None = None
    size: float | None = None
    bold: bool | None = None
    wrap: bool | None = None

    def describe(self) -> str:
        parts = []
        if self.name:
            parts.append(self.name)
        if self.size is not None:
            parts.append(f"{self.size:g}")
        if self.bold:
            parts.append("bold")
        if self.wrap is not None:
            parts.append("wrap text" if self.wrap else "no wrap")
        return " ".join(parts)


class SetValue(BaseModel):
    op: Literal["set_value"] = "set_value"
    sheet: str
    cell: str
    before: Any
    after: str | int | float | bool


class SetFormula(BaseModel):
    op: Literal["set_formula"] = "set_formula"
    sheet: str
    cell: str
    before: Any
    after: str  # with the leading '='


class SetFont(BaseModel):
    op: Literal["set_font"] = "set_font"
    sheet: str
    target: str  # cell or range
    before: FontSpec
    after: FontSpec


class SetColumnWidth(BaseModel):
    op: Literal["set_column_width"] = "set_column_width"
    sheet: str
    column: str
    before: float
    after: float


class Manual(BaseModel):
    op: Literal["manual"] = "manual"
    reason: str


Operation = Annotated[SetValue | SetFormula | SetFont | SetColumnWidth | Manual, Field(discriminator="op")]
AutoOperation = SetValue | SetFormula | SetFont | SetColumnWidth


class NormalizedFinding(BaseModel):
    id: str
    fingerprint: str = ""
    rule: RuleMeta
    resolution: Resolution
    case: str = "default"
    message: str = ""
    locations: list[Location] = []
    hidden: bool = False
    change: ProposedChange | None = None
    fallback_reason: str | None = None
    operation: Operation
    duplicates: list[str] = []  # referenced ids that exist
    missing_duplicates: list[str] = []  # referenced ids not in the report
    check_problems: list[str] = []
    raw: dict[str, Any] = {}

    @property
    def is_quick_fix(self) -> bool:
        return self.resolution == Resolution.QUICK_FIX

    @property
    def automatic(self) -> bool:
        return self.operation.op != "manual"

    @property
    def sheets(self) -> list[str]:
        return sorted({loc.sheet for loc in self.locations if loc.sheet})

    @property
    def primary_label(self) -> str:
        from review_app.addresses import format_ref

        for loc in self.locations:
            if loc.sheet:
                return format_ref(loc.sheet, loc.ref)
        return "Workbook"


# ---------------------------------------------------------------- workbook


class SheetProfile(BaseModel):
    name: str
    state: str  # visible | hidden | veryHidden
    part: str | None = None
    hidden_rows: list[int] = []
    hidden_columns: list[str] = []
    merged_ranges: list[str] = []
    protected: bool = False


class WorkbookProfile(BaseModel):
    file_name: str
    extension: str
    sha256: str
    sheets: list[SheetProfile] = []
    defined_names: list[str] = []
    structure_protected: bool = False
    has_vba: bool = False
    has_signature: bool = False
    parts: list[str] = []
    writable: bool = True
    not_writable_reasons: list[str] = []

    def sheet(self, name: str | None) -> SheetProfile | None:
        return next((s for s in self.sheets if s.name == name), None)

    @property
    def sheet_names(self) -> list[str]:
        return [s.name for s in self.sheets]


class CellSnapshot(BaseModel):
    sheet: str
    cell: str
    exists: bool = True
    value: Any = None  # stored value (formula text for formula cells)
    cached: Any = None  # saved result of a formula
    formula: str | None = None  # with leading '='
    data_type: str | None = None
    number_format: str | None = None
    font_name: str | None = None
    font_size: float | None = None
    bold: bool = False
    italic: bool = False
    wrap: bool = False
    fill: str | None = None
    horizontal: str | None = None
    vertical: str | None = None
    merged_range: str | None = None
    row_hidden: bool = False
    column_hidden: bool = False
    sheet_state: str = "visible"


# ---------------------------------------------------------------- review


class Decision(BaseModel):
    finding_id: str
    value: DecisionValue
    comment: str = ""
    decided_at: str = Field(default_factory=utc_now)
    stale_override: bool = False


class AppliedChange(BaseModel):
    seq: int
    finding_id: str
    operation: Operation
    before_snapshot: dict[str, Any] = {}
    applied_at: str = Field(default_factory=utc_now)
    stale_override: bool = False


class VisibilityRequest(BaseModel):
    finding_id: str
    sheet: str
    what: Literal["sheet", "row", "column"]
    ref: str  # sheet name, row number or column letter
    original_state: str

    @property
    def key(self) -> tuple[str, str, str]:
        return (self.sheet, self.what, self.ref)


class IntegrityResult(BaseModel):
    check: str
    status: Literal["pass", "warn", "fail"]
    detail: str = ""
    blocks_download: bool = False


class ValidationIssue(BaseModel):
    level: Literal["error", "warning"]
    message: str
