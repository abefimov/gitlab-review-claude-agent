from datetime import datetime, timezone
from pathlib import Path
import pytest
from claude_reviewer.state import State


def _utc(iso: str) -> datetime:
    return datetime.fromisoformat(iso).replace(tzinfo=timezone.utc)


def test_last_check_round_trip(tmp_path: Path):
    s = State(tmp_path / "s.db")
    assert s.get_last_check(42) is None
    s.set_last_check(42, _utc("2026-04-24T12:00:00"))
    got = s.get_last_check(42)
    assert got == _utc("2026-04-24T12:00:00")


def test_mr_reviewed_sha(tmp_path: Path):
    s = State(tmp_path / "s.db")
    assert s.get_reviewed_sha(42, 249) is None
    s.set_reviewed_sha(42, 249, "abc1234", _utc("2026-04-24T10:00:00"))
    assert s.get_reviewed_sha(42, 249) == "abc1234"
    s.set_reviewed_sha(42, 249, "def5678", _utc("2026-04-24T11:00:00"))
    assert s.get_reviewed_sha(42, 249) == "def5678"


def test_bot_discussions(tmp_path: Path):
    s = State(tmp_path / "s.db")
    s.add_bot_discussion(
        discussion_id="d1", project_id=42, mr_iid=249,
        file="a.swift", line=10, last_note_id=100,
    )
    active = s.active_bot_discussions(42)
    assert len(active) == 1 and active[0].discussion_id == "d1"

    s.update_last_note_id("d1", 150)
    active = s.active_bot_discussions(42)
    assert active[0].last_note_id == 150

    s.mark_discussion_resolved("d1")
    assert s.active_bot_discussions(42) == []


def test_task_log(tmp_path: Path):
    s = State(tmp_path / "s.db")
    tid = s.log_task_started("first_review", project_id=42, mr_iid=249,
                             head_sha="abc", discussion_id=None)
    s.log_task_status(tid, "finalized")
    s.log_task_status(tid, "posted")
    rows = s.recent_tasks(limit=10)
    assert rows[0]["status"] == "posted"


def test_close_and_context_manager(tmp_path: Path):
    db_path = tmp_path / "s.db"
    with State(db_path) as s:
        s.set_last_check(1, _utc("2026-04-24T12:00:00"))
    # After exiting, connection should be closed; a new State instance
    # should still read the same data.
    s2 = State(db_path)
    assert s2.get_last_check(1) == _utc("2026-04-24T12:00:00")
    s2.close()


def test_forget_mr_is_atomic(tmp_path: Path):
    s = State(tmp_path / "s.db")
    s.set_reviewed_sha(1, 10, "abc", _utc("2026-04-24T10:00:00"))
    s.add_bot_discussion(
        discussion_id="d1", project_id=1, mr_iid=10,
        file="a", line=1, last_note_id=100,
    )
    s.forget_mr(1, 10)
    assert s.get_reviewed_sha(1, 10) is None
    assert s.active_bot_discussions(1) == []
