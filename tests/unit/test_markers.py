import pytest

from claude_reviewer.markers import (
    render_body, extract_marker, is_bot_note, MarkerInfo,
)


def test_render_inline_body_has_header_and_marker():
    body = render_body(
        kind="inline",
        text="Null check is missing.",
        severity="major",
        category="bug",
        marker_key="8e873c1:Sources/Foo.swift:42",
        visible_prefix="**[Claude Review]**",
    )
    assert body.startswith("**[Claude Review]** · bug · major")
    assert "Null check is missing." in body
    assert "<!--claude-review:inline:8e873c1:Sources/Foo.swift:42-->" in body


def test_render_summary_has_no_severity_in_header():
    body = render_body(
        kind="summary",
        text="Looks good overall.",
        marker_key="first:8e873c1",
        visible_prefix="**[Claude Review]**",
    )
    assert body.startswith("**[Claude Review]** · summary")
    assert "<!--claude-review:summary:first:8e873c1-->" in body


def test_extract_marker_from_body():
    body = (
        "**[Claude Review]** · bug · major\n\n"
        "Body text here.\n\n"
        "<!--claude-review:inline:abcdef1:foo.py:10-->"
    )
    info = extract_marker(body)
    assert info is not None
    assert info.kind == "inline"
    assert info.key == "abcdef1:foo.py:10"


def test_extract_marker_none_when_absent():
    assert extract_marker("plain user comment") is None


def test_is_bot_note_by_marker_even_under_human_author():
    note = {
        "author": {"username": "mshuram"},
        "body": "reply\n<!--claude-review:reply:abc-->",
    }
    assert is_bot_note(note, bot_username="claude-reviewer") is True


def test_is_bot_note_by_username_when_no_marker():
    note = {"author": {"username": "claude-reviewer"}, "body": "hi"}
    assert is_bot_note(note, bot_username="claude-reviewer") is True


def test_is_bot_note_false_for_regular_human():
    note = {"author": {"username": "mshuram"}, "body": "ok, fixed"}
    assert is_bot_note(note, bot_username="claude-reviewer") is False


def test_render_inline_without_severity_raises():
    with pytest.raises(ValueError, match="severity and category"):
        render_body(
            kind="inline",
            text="x" * 30,
            marker_key="k",
            visible_prefix="**[Claude Review]**",
        )
