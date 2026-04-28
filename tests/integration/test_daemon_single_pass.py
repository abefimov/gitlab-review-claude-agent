import asyncio
from pathlib import Path
from types import SimpleNamespace
import pytest
from claude_reviewer.orchestrator import decide_tasks_for_project, ReconcileInputs
from claude_reviewer.state import State
from claude_reviewer.types import FirstReviewTask


def test_single_reconcile_produces_first_review_task(tmp_path: Path):
    s = State(tmp_path / "s.db")
    inputs = ReconcileInputs(
        project_id=1,
        mrs=[SimpleNamespace(iid=10, sha="abc", target_branch="main")],
        bot_discussions=[],
        discussion_loader=lambda _: None,
        target_branches=["main"],
        bot_username="bot",
    )
    assert decide_tasks_for_project(inputs, s) == [FirstReviewTask(1, 10)]
