from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
import pytest
from claude_reviewer.orchestrator import (
    decide_tasks_for_project, ReconcileInputs,
)
from claude_reviewer.types import (
    FirstReviewTask, IncrementalReviewTask, ThreadReplyTask,
)
from claude_reviewer.state import State


def _mr(iid, sha, target="develop"):
    return SimpleNamespace(iid=iid, sha=sha, target_branch=target)


def _note(id_, username, body=""):
    return SimpleNamespace(id=id_,
                           author={"username": username}, body=body,
                           attributes={"id": id_, "body": body,
                                       "author": {"username": username}})


def _discussion(did, notes):
    return SimpleNamespace(
        id=did,
        attributes={"id": did,
                    "notes": [dict(id=n.id, author=n.author,
                                   body=n.body) for n in notes]},
    )


def test_first_review_for_unseen_mr(tmp_path: Path):
    s = State(tmp_path / "s.db")
    inputs = ReconcileInputs(
        project_id=42,
        mrs=[_mr(249, "abc", target="develop")],
        bot_discussions=[],
        discussion_loader=lambda did: None,
        target_branches=["develop", "main"],
        bot_username="claude-reviewer",
    )
    tasks = decide_tasks_for_project(inputs, s)
    assert tasks == [FirstReviewTask(42, 249)]


def test_incremental_review_when_sha_changed(tmp_path: Path):
    s = State(tmp_path / "s.db")
    s.set_reviewed_sha(42, 249, "old", datetime.now(timezone.utc))
    inputs = ReconcileInputs(
        project_id=42,
        mrs=[_mr(249, "new", target="develop")],
        bot_discussions=[],
        discussion_loader=lambda did: None,
        target_branches=["develop"],
        bot_username="claude-reviewer",
    )
    tasks = decide_tasks_for_project(inputs, s)
    assert tasks == [IncrementalReviewTask(42, 249, "old", "new")]


def test_skips_when_sha_unchanged(tmp_path: Path):
    s = State(tmp_path / "s.db")
    s.set_reviewed_sha(42, 249, "same", datetime.now(timezone.utc))
    inputs = ReconcileInputs(
        project_id=42, mrs=[_mr(249, "same")],
        bot_discussions=[], discussion_loader=lambda did: None,
        target_branches=["develop"], bot_username="claude-reviewer",
    )
    assert decide_tasks_for_project(inputs, s) == []


def test_thread_reply_when_non_bot_note_appears(tmp_path: Path):
    s = State(tmp_path / "s.db")
    s.add_bot_discussion(
        discussion_id="d1", project_id=42, mr_iid=249,
        file="a.swift", line=10, last_note_id=100,
    )
    disc = _discussion("d1", [
        _note(100, "claude-reviewer", "<!--claude-review:inline:h:a:10-->"),
        _note(200, "alice", "thanks, fixed"),
    ])
    inputs = ReconcileInputs(
        project_id=42, mrs=[], bot_discussions=s.active_bot_discussions(42),
        discussion_loader=lambda did: disc,
        target_branches=["develop"], bot_username="claude-reviewer",
    )
    tasks = decide_tasks_for_project(inputs, s)
    assert tasks == [ThreadReplyTask(42, 249, "d1", 200)]


def test_thread_reply_ignores_new_bot_notes(tmp_path: Path):
    s = State(tmp_path / "s.db")
    s.add_bot_discussion(
        discussion_id="d1", project_id=42, mr_iid=249,
        file="a.swift", line=10, last_note_id=100,
    )
    disc = _discussion("d1", [
        _note(100, "claude-reviewer", "<!--claude-review:inline:h:a:10-->"),
        _note(200, "claude-reviewer", "<!--claude-review:reply:x-->"),
    ])
    inputs = ReconcileInputs(
        project_id=42, mrs=[],
        bot_discussions=s.active_bot_discussions(42),
        discussion_loader=lambda did: disc,
        target_branches=["develop"], bot_username="claude-reviewer",
    )
    assert decide_tasks_for_project(inputs, s) == []
