"""Report validation: schema version first, then the shared JSON Schema, then semantic checks.

Compatibility is decided by schema_version only, never by skill_version."""

from __future__ import annotations

import json
from collections import Counter
from functools import lru_cache
from importlib import resources
from pathlib import PurePath
from typing import Any

from jsonschema import Draft202012Validator

from review_app import limits
from review_app.models import ValidationIssue


@lru_cache(maxsize=1)
def _validator() -> Draft202012Validator:
    text = resources.files("review_app").joinpath("schema/review-report.schema.json").read_text(encoding="utf-8")
    return Draft202012Validator(json.loads(text))


def _err(message: str) -> ValidationIssue:
    return ValidationIssue(level="error", message=message)


def _warn(message: str) -> ValidationIssue:
    return ValidationIssue(level="warning", message=message)


def _path(error) -> str:
    parts = []
    for p in error.absolute_path:
        parts.append(f"[{p}]" if isinstance(p, int) else (f".{p}" if parts else str(p)))
    return "".join(parts) or "(top level)"


def _all_findings(report: dict[str, Any]) -> list[dict[str, Any]]:
    out = []
    for rule in report.get("rules") or []:
        if isinstance(rule, dict):
            out.extend(f for f in rule.get("findings") or [] if isinstance(f, dict))
    return out


def validate_report(report: dict[str, Any]) -> list[ValidationIssue]:
    """Blocking errors and non-blocking warnings for a parsed report. Errors stop the session."""
    issues: list[ValidationIssue] = []
    version = report.get("schema_version")
    if version is None:
        return [
            _err(
                "The report has no schema_version (it is version 0.2 or older), which is not supported. "
                f"Supported: {', '.join(limits.SUPPORTED_SCHEMA_VERSIONS)}. Re-run the review with a current skill."
            )
        ]
    if version not in limits.SUPPORTED_SCHEMA_VERSIONS:
        return [
            _err(
                f"Report schema version {version} is not supported. "
                f"Supported: {', '.join(limits.SUPPORTED_SCHEMA_VERSIONS)}."
            )
        ]

    findings = _all_findings(report)
    if len(findings) > limits.MAX_FINDINGS:
        return [_err(f"The report has more than {limits.MAX_FINDINGS:,} findings and cannot be loaded.")]

    errors = sorted(_validator().iter_errors(report), key=lambda e: list(map(str, e.absolute_path)))
    for e in errors[: limits.MAX_SCHEMA_ERRORS]:
        issues.append(_err(f"The report does not match the review-report schema: {_path(e)}: {e.message}"))
    if len(errors) > limits.MAX_SCHEMA_ERRORS:
        issues.append(_err(f"{len(errors) - limits.MAX_SCHEMA_ERRORS} more schema errors are not shown."))
    if errors:
        return issues

    counts = Counter(f["id"] for f in findings)
    for fid, n in sorted(counts.items()):
        if n > 1:
            issues.append(_err(f"Finding ID {fid} appears more than once."))

    if report.get("workbook_unchanged") is False:
        issues.append(
            _warn(
                "The report says the workbook changed while the review was running. "
                "Some findings may not match the file."
            )
        )
    if not findings:
        issues.append(_warn("The report contains no findings to review."))
    return issues


def check_workbook_name(report_name: str, uploaded_name: str) -> ValidationIssue | None:
    """Mismatch is a warning that needs explicit confirmation, never a rejection."""
    if PurePath(report_name).name == PurePath(uploaded_name).name:
        return None
    return _warn(
        f"The report was created for '{report_name}', but you uploaded '{uploaded_name}'. "
        "Confirm that this report belongs to the uploaded workbook before you continue."
    )
