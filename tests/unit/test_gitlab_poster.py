from types import SimpleNamespace
from claude_reviewer.gitlab_poster import (
    build_position, marker_for_inline, marker_for_summary,
)
from claude_reviewer.types import MRRefs


def test_build_position_text_payload():
    refs = MRRefs(base_sha="b", start_sha="s", head_sha="h")
    pos = build_position(refs, new_path="Sources/Foo.swift", new_line=42)
    assert pos == {
        "base_sha": "b", "start_sha": "s", "head_sha": "h",
        "position_type": "text",
        "new_path": "Sources/Foo.swift",
        "new_line": 42,
    }


def test_build_position_context_line_includes_old_side():
    refs = MRRefs(base_sha="b", start_sha="s", head_sha="h")
    pos = build_position(
        refs, new_path="Sources/Foo.swift", new_line=42,
        old_path="Sources/Foo.swift", old_line=40,
    )
    assert pos["old_path"] == "Sources/Foo.swift"
    assert pos["old_line"] == 40
    assert pos["new_line"] == 42


def test_marker_keys_are_stable():
    k1 = marker_for_inline(task_kind="first_review", head_sha="abcdef1234567",
                           file="a.py", line=1)
    k2 = marker_for_inline(task_kind="first_review", head_sha="abcdef1234567",
                           file="a.py", line=1)
    assert k1 == k2
    assert "abcdef1234567" in k1 or "abcdef1" in k1


def test_marker_differs_for_different_line():
    k1 = marker_for_inline(task_kind="first_review", head_sha="h",
                           file="a.py", line=1)
    k2 = marker_for_inline(task_kind="first_review", head_sha="h",
                           file="a.py", line=2)
    assert k1 != k2


from claude_reviewer.gitlab_poster import marker_for_reply


def test_marker_for_reply_includes_last_note_id():
    k1 = marker_for_reply(discussion_id="d1", action="reply", last_note_id=100)
    k2 = marker_for_reply(discussion_id="d1", action="reply", last_note_id=200)
    assert k1 != k2, "marker must change when responding to a new human note"
    assert "100" in k1 and "200" in k2


import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock
from claude_reviewer.gitlab_poster import post_review, PostContext
from claude_reviewer.config import Config, GitLabConfig
from claude_reviewer.state import State
from claude_reviewer.types import (
    InlineComment, ReviewOutput, MRRefs,
)


def _make_cfg() -> Config:
    return Config(
        gitlab=GitLabConfig(base_url="https://example.com",
                            bot_username="claude-reviewer"),
    )


@pytest.mark.asyncio
async def test_pre_dropped_only_still_posts_summary(tmp_path):
    """Reproduces the bot-found bug: incremental review with all comments
    pre-dropped and no summary must still post a dropped-summary note,
    not silently swallow them."""
    cfg = _make_cfg()
    state = State(tmp_path / "s.db")

    # MR mock: a discussions list with no existing markers
    mr = MagicMock()
    mr.discussions.list.return_value = []
    mr.unapprove = MagicMock()
    mr.notes.create = MagicMock()
    mr.discussions.create = MagicMock()

    # Diff that has only file foo.py with one + line at line 2.
    # Bot will produce a comment on line 99 (NOT in diff) — should pre-drop.
    diff_text = """diff --git a/foo.py b/foo.py
--- a/foo.py
+++ b/foo.py
@@ -1,1 +1,2 @@
 a
+b
"""
    review = ReviewOutput(
        task_type="incremental_review",
        inline_comments=[InlineComment(
            file="foo.py", line=99,
            severity="major", category="bug",
            body="x" * 30,
        )],
        summary=None,
        thread_action=None,
        approval=False,
    )

    refs = MRRefs(base_sha="b", start_sha="b", head_sha="h")
    ctx = PostContext(
        cfg=cfg, task_kind="incremental_review",
        head_sha="h" * 40, dry_run=False,
        diff_text=diff_text,
    )

    await post_review(
        mr=mr, refs=refs, review=review, ctx=ctx,
        state=state, project_id=1, mr_iid=1,
    )

    # The dropped-summary note MUST have been posted with the file mention.
    assert mr.notes.create.called, (
        "dropped-summary note was not posted — pre_dropped findings lost"
    )
    posted_body = mr.notes.create.call_args[0][0]["body"]
    assert "dropped" in posted_body.lower()
    assert "foo.py" in posted_body
    state.close()
