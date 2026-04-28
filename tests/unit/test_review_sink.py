import json
from pathlib import Path
import pytest
from claude_reviewer.review_sink_mcp import reset_state


def _call(tool_name: str, **kwargs):
    from claude_reviewer.review_sink_mcp import _TOOLS
    return _TOOLS[tool_name](**kwargs)


def test_add_inline_validates_line(tmp_path, monkeypatch):
    monkeypatch.setenv("REVIEW_SINK_OUTPUT", str(tmp_path / "out.json"))
    monkeypatch.setenv("REVIEW_SINK_TASK", "first_review")
    reset_state()
    res = _call("add_inline_comment",
                file="a.py", line=0, severity="major", category="bug",
                body="x" * 30)
    assert "error" in res


def test_add_inline_rejects_duplicate_file_line(tmp_path, monkeypatch):
    monkeypatch.setenv("REVIEW_SINK_OUTPUT", str(tmp_path / "out.json"))
    monkeypatch.setenv("REVIEW_SINK_TASK", "first_review")
    reset_state()
    ok = _call("add_inline_comment",
               file="a.py", line=5, severity="major", category="bug",
               body="x" * 30)
    assert "ok" in ok
    dup = _call("add_inline_comment",
                file="a.py", line=5, severity="minor", category="design",
                body="y" * 30)
    assert "already exists" in dup


def test_max_inline_comments_cap(tmp_path, monkeypatch):
    monkeypatch.setenv("REVIEW_SINK_OUTPUT", str(tmp_path / "out.json"))
    monkeypatch.setenv("REVIEW_SINK_TASK", "first_review")
    reset_state()
    for i in range(10):
        r = _call("add_inline_comment",
                  file=f"f{i}.py", line=1, severity="minor",
                  category="design", body="x" * 30)
        assert "ok" in r
    overflow = _call("add_inline_comment",
                     file="extra.py", line=1, severity="minor",
                     category="design", body="x" * 30)
    assert "limit reached" in overflow


def test_finalize_writes_file(tmp_path, monkeypatch):
    out = tmp_path / "out.json"
    monkeypatch.setenv("REVIEW_SINK_OUTPUT", str(out))
    monkeypatch.setenv("REVIEW_SINK_TASK", "first_review")
    reset_state()
    _call("add_inline_comment",
          file="a.py", line=1, severity="minor", category="bug",
          body="x" * 30)
    _call("set_summary", overall_assessment="y" * 30,
          performance_notes=None)
    _call("finalize_review")
    data = json.loads(out.read_text())
    assert data["task_type"] == "first_review"
    assert len(data["inline_comments"]) == 1
    assert data["summary"]["overall"] == "y" * 30


def test_set_summary_forbidden_in_thread_reply(tmp_path, monkeypatch):
    monkeypatch.setenv("REVIEW_SINK_OUTPUT", str(tmp_path / "out.json"))
    monkeypatch.setenv("REVIEW_SINK_TASK", "thread_reply")
    reset_state()
    res = _call("set_summary", overall_assessment="x" * 30,
                performance_notes=None)
    assert "error" in res


def test_finalize_returns_error_when_output_env_missing(tmp_path, monkeypatch):
    monkeypatch.delenv("REVIEW_SINK_OUTPUT", raising=False)
    monkeypatch.setenv("REVIEW_SINK_TASK", "first_review")
    reset_state()
    res = _call("finalize_review")
    assert "error" in res
    assert "REVIEW_SINK_OUTPUT" in res


def test_reply_in_thread_forbidden_outside_thread_reply(
    tmp_path, monkeypatch,
):
    monkeypatch.setenv("REVIEW_SINK_OUTPUT", str(tmp_path / "out.json"))
    monkeypatch.setenv("REVIEW_SINK_TASK", "first_review")
    reset_state()
    res = _call("reply_in_thread", discussion_id="d", body="x" * 10)
    assert "error" in res


def test_only_one_thread_action(tmp_path, monkeypatch):
    monkeypatch.setenv("REVIEW_SINK_OUTPUT", str(tmp_path / "out.json"))
    monkeypatch.setenv("REVIEW_SINK_TASK", "thread_reply")
    reset_state()
    ok = _call("reply_in_thread", discussion_id="d", body="x" * 10)
    assert "ok" in ok
    second = _call("resolve_thread", discussion_id="d", body=None)
    assert "already set" in second


def test_thread_reply_finalize_writes_action(tmp_path, monkeypatch):
    import json
    out = tmp_path / "out.json"
    monkeypatch.setenv("REVIEW_SINK_OUTPUT", str(out))
    monkeypatch.setenv("REVIEW_SINK_TASK", "thread_reply")
    reset_state()
    _call("resolve_thread", discussion_id="d42", body="looks good")
    _call("finalize_review")
    data = json.loads(out.read_text())
    assert data["task_type"] == "thread_reply"
    assert data["thread_action"]["action"] == "resolve"
    assert data["thread_action"]["discussion_id"] == "d42"


def test_approve_mr_writes_to_payload(tmp_path, monkeypatch):
    import json
    out = tmp_path / "out.json"
    monkeypatch.setenv("REVIEW_SINK_OUTPUT", str(out))
    monkeypatch.setenv("REVIEW_SINK_TASK", "first_review")
    reset_state()
    _call("set_summary", overall_assessment="clean review " * 5,
          performance_notes=None)
    res = _call("approve_mr")
    assert "ok" in res
    _call("finalize_review")
    data = json.loads(out.read_text())
    assert data["approval"] is True


def test_approve_mr_forbidden_in_thread_reply(tmp_path, monkeypatch):
    monkeypatch.setenv("REVIEW_SINK_OUTPUT", str(tmp_path / "out.json"))
    monkeypatch.setenv("REVIEW_SINK_TASK", "thread_reply")
    reset_state()
    res = _call("approve_mr")
    assert "error" in res


def test_default_approval_is_false(tmp_path, monkeypatch):
    import json
    out = tmp_path / "out.json"
    monkeypatch.setenv("REVIEW_SINK_OUTPUT", str(out))
    monkeypatch.setenv("REVIEW_SINK_TASK", "first_review")
    reset_state()
    _call("set_summary", overall_assessment="x" * 30, performance_notes=None)
    _call("finalize_review")
    data = json.loads(out.read_text())
    assert data["approval"] is False


def test_approve_blocked_by_blocker_severity(tmp_path, monkeypatch):
    monkeypatch.setenv("REVIEW_SINK_OUTPUT", str(tmp_path / "out.json"))
    monkeypatch.setenv("REVIEW_SINK_TASK", "first_review")
    reset_state()
    _call("add_inline_comment",
          file="a.py", line=1, severity="blocker", category="bug",
          body="x" * 30)
    res = _call("approve_mr")
    assert "error" in res
    assert "blocker" in res or "major" in res


def test_approve_blocked_by_major_severity(tmp_path, monkeypatch):
    monkeypatch.setenv("REVIEW_SINK_OUTPUT", str(tmp_path / "out.json"))
    monkeypatch.setenv("REVIEW_SINK_TASK", "first_review")
    reset_state()
    _call("add_inline_comment",
          file="a.py", line=1, severity="major", category="bug",
          body="x" * 30)
    res = _call("approve_mr")
    assert "error" in res


def test_approve_allowed_with_only_minor(tmp_path, monkeypatch):
    import json
    out = tmp_path / "out.json"
    monkeypatch.setenv("REVIEW_SINK_OUTPUT", str(out))
    monkeypatch.setenv("REVIEW_SINK_TASK", "first_review")
    reset_state()
    _call("add_inline_comment",
          file="a.py", line=1, severity="minor", category="design",
          body="x" * 30)
    res = _call("approve_mr")
    assert "ok" in res
    _call("set_summary", overall_assessment="x" * 30, performance_notes=None)
    _call("finalize_review")
    data = json.loads(out.read_text())
    assert data["approval"] is True
