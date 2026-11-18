"""Reviewer comment rules. Originally ported from the dropped Excel add-in."""

from __future__ import annotations

import re

from review_app.models import DecisionValue

COMMENT_MIN_LENGTH = 10
_PLACEHOLDERS = {"tbd", "todo", "na", "none", "nil", "x", "xxx", "etc", ""}

REQUIRES_COMMENT = frozenset({DecisionValue.RESOLVED, DecisionValue.NOT_APPLICABLE, DecisionValue.MANUALLY_RESOLVED})


def comment_required(decision: DecisionValue) -> bool:
    return decision in REQUIRES_COMMENT


def validate_comment(decision: DecisionValue, comment: str | None) -> str | None:
    """A user-facing message when the comment is not acceptable for this decision, otherwise None."""
    if not comment_required(decision):
        return None
    text = (comment or "").strip()
    normalized = re.sub(r"[^a-z0-9]", "", text.lower())
    if normalized in _PLACEHOLDERS:
        return "Enter an explanation. Placeholder text such as TBD, TODO or N/A is not accepted."
    if len(text) < COMMENT_MIN_LENGTH:
        return f"The explanation must be at least {COMMENT_MIN_LENGTH} characters long."
    return None
