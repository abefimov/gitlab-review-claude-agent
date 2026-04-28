from claude_reviewer.prompt_builder import (
    build_first_review_prompt, truncate_diff, FirstReviewInputs,
    stack_hint,
)


def test_stack_hint_swift():
    h = stack_hint("swift-ios")
    assert "Swift" in h or "iOS" in h or "Combine" in h


def test_stack_hint_unknown_is_empty():
    assert stack_hint(None) == ""


def test_truncate_diff_under_cap_unchanged():
    diff = "a" * 1000
    assert truncate_diff(diff, cap_bytes=2000) == diff


def test_truncate_diff_over_cap_gets_marker():
    diff = "a" * 300_000
    out = truncate_diff(diff, cap_bytes=200_000)
    assert "truncated" in out.lower()
    assert len(out) <= 200_000 + 200


def test_first_review_prompt_contains_key_fields():
    prompt = build_first_review_prompt(FirstReviewInputs(
        project_path="example/mobile/sample-ios",
        mr_iid=249,
        mr_title="Swift 6 core storage",
        mr_description="Migrates to Swift 6 concurrency.",
        author_username="alice",
        target_branch="develop",
        base_sha="c598314",
        head_sha="8e873c1",
        diff_stat="Podfile | 2 +-\nPodfile.lock | 8 ++++----",
        diff_text="diff --git a/Podfile b/Podfile\n...",
        stack="swift-ios",
    ))
    assert "example/mobile/sample-ios !249" in prompt
    assert "Swift 6 core storage" in prompt
    assert "develop" in prompt
    assert "c598314" in prompt
    assert "8e873c1" in prompt
    assert "Perform the first review" in prompt


def test_first_review_prompt_truncates_long_description():
    long_desc = "x" * 10_000
    prompt = build_first_review_prompt(FirstReviewInputs(
        project_path="p", mr_iid=1, mr_title="t", mr_description=long_desc,
        author_username="a", target_branch="main",
        base_sha="b", head_sha="h",
        diff_stat="", diff_text="", stack=None,
    ))
    assert long_desc not in prompt
    assert "x" * 4000 in prompt  # truncated to 4000 chars


from claude_reviewer.prompt_builder import (
    build_incremental_review_prompt, IncrementalReviewInputs,
    build_thread_reply_prompt, ThreadReplyInputs, ThreadNote,
)


def test_incremental_prompt_has_old_and_new_sha():
    p = build_incremental_review_prompt(IncrementalReviewInputs(
        project_path="p", mr_iid=1, mr_title="t",
        old_head_sha="aaaaaa", new_head_sha="bbbbbb",
        diff_text="some diff",
        stack=None,
    ))
    assert "aaaaaa" in p and "bbbbbb" in p
    assert "Comment ONLY on new changes" in p


def test_thread_reply_prompt_has_history_and_latest():
    p = build_thread_reply_prompt(ThreadReplyInputs(
        project_path="p", mr_iid=1, mr_title="t",
        discussion_id="d123", file="a.py", line=10,
        current_code_excerpt="def foo():\n    pass",
        thread_notes=[
            ThreadNote(author="bot", ts="2026-04-24T10:00", body="original"),
            ThreadNote(author="alice", ts="2026-04-24T11:00", body="fixed!"),
        ],
        latest_note_body="fixed!",
        stack=None,
    ))
    assert "d123" in p
    assert "def foo()" in p
    assert "[bot" in p and "[alice" in p
    assert "fixed!" in p
    assert "Available tools" in p
