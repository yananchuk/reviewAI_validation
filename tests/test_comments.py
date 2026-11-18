import pytest

from review_app.comments import validate_comment
from review_app.models import DecisionValue as D


@pytest.mark.parametrize("text", ["", "   ", "TBD", "todo", "N/A", "n.a.", "xxx", " TBD "])
def test_placeholders_refused(text):
    assert "Placeholder" in validate_comment(D.RESOLVED, text)


def test_minimum_length_after_trimming():
    assert "at least 10" in validate_comment(D.NOT_APPLICABLE, "   too short   ")
    assert validate_comment(D.RESOLVED, "Explained in WP C.07, ref 12") is None
    assert validate_comment(D.NOT_APPLICABLE, "N/A - scoped out in planning") is None


def test_manual_resolution_needs_a_comment():
    assert validate_comment(D.MANUALLY_RESOLVED, "") is not None


@pytest.mark.parametrize("decision", [D.KEPT_OPEN, D.ACCEPTED, D.REJECTED, D.OPEN])
def test_optional_comment(decision):
    assert validate_comment(decision, "") is None
