import pytest
from pydantic import ValidationError
from claude_reviewer.types import (
    InlineComment, ReviewSummary, ThreadAction, ReviewOutput,
    TaskKind,
)


def test_inline_comment_valid():
    c = InlineComment(
        file="Sources/Foo.swift", line=42,
        severity="major", category="bug",
        body="This is at least twenty characters long.",
    )
    assert c.severity == "major"


def test_inline_comment_rejects_zero_line():
    with pytest.raises(ValidationError):
        InlineComment(
            file="f.py", line=0, severity="minor", category="bug",
            body="twenty characters minimum body",
        )


def test_inline_comment_rejects_bad_severity():
    with pytest.raises(ValidationError):
        InlineComment(
            file="f.py", line=1, severity="critical", category="bug",  # type: ignore
            body="twenty characters minimum body",
        )


def test_review_output_roundtrip():
    data = {
        "task_type": "first_review",
        "inline_comments": [
            {
                "file": "a.py", "line": 1, "severity": "minor",
                "category": "tests", "body": "long enough body text here x",
            }
        ],
        "summary": {
            "overall": "twenty characters minimum summary text",
            "performance_notes": None,
        },
        "thread_action": None,
    }
    out = ReviewOutput.model_validate(data)
    assert out.task_type == "first_review"
    assert len(out.inline_comments) == 1
    assert out.summary.overall.startswith("twenty")


def test_task_kind_values():
    assert set(TaskKind.__args__) == {
        "first_review", "incremental_review", "thread_reply",
    }


def test_review_output_default_approval_false():
    out = ReviewOutput.model_validate({
        "task_type": "first_review",
        "inline_comments": [],
        "summary": None,
        "thread_action": None,
    })
    assert out.approval is False


def test_review_output_explicit_approval_true():
    out = ReviewOutput.model_validate({
        "task_type": "first_review",
        "inline_comments": [],
        "summary": None,
        "thread_action": None,
        "approval": True,
    })
    assert out.approval is True
