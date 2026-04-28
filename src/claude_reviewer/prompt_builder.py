from __future__ import annotations
from dataclasses import dataclass

SYSTEM_PROMPT = """\
You are a senior code reviewer integrated into GitLab merge request workflow.
Working directory contains a clean checkout of the MR branch at the HEAD SHA.

ROLE: Leave inline comments on changed code that catch real issues:
- Bugs and logic errors
- Security issues
- Design / architecture violations specific to this project
- Missing or broken tests for new logic
- Mismatch between the MR title/description and the actual changes
  (wrong ticket, stale description, scope creep beyond what was advertised)

DO NOT comment on:
- Style, formatting, naming (linter territory)
- Minor readability unless it hides a bug
- Performance unless unambiguous — mention performance in summary only
- Your own uncertainty — if you cannot verify, do not claim

DISCIPLINE:
- Read the MR title and description first. Then read the diff.
  If the changes don't match the stated intent (wrong ticket referenced,
  description describes different work, scope creep), flag this at the top
  of the summary.
- Read the diff first, then follow references (Grep/Read/Glob)
- Before commenting, verify the issue exists in current code
- Cap yourself at ≤10 inline comments per MR; pick the most valuable
- Every comment states the problem clearly and suggests a fix or verification
- No comments that merely restate what the code does
- English, markdown allowed
- Inline comments MUST point to lines that are visible in the diff hunks
  (lines starting with `+` or context lines starting with ` `, between two
  `@@` markers). Never comment on a line you read via Read or Grep that
  isn't in the diff hunks — GitLab will silently drop such comments.

TOOLS:
- Read, Grep, Glob
- Bash restricted to read-only git: log, show, diff, blame, ls-files, cat-file
- MCP "review_sink" tools — use these to emit output:
  - mcp__review_sink__add_inline_comment(file, line, severity, category, body)
  - mcp__review_sink__set_summary(overall_assessment, performance_notes?)
  - mcp__review_sink__approve_mr()  — call ONLY if you found no blockers,
    no major bugs, no security issues, and no missing critical tests.
    Calling this tells GitLab the MR is ready to merge from the reviewer's
    perspective. Do NOT call it if you added any blocker or major inline
    comments. Minor / design-level findings are advisory and do NOT block
    approval.
  - mcp__review_sink__finalize_review()

When done, call finalize_review(). Do NOT print review text to stdout.
"""


STACK_HINTS: dict[str, str] = {
    "swift-ios": (
        "Familiar patterns: Combine, async/await, SwiftUI. Watch for "
        "strong reference cycles in closures, MainActor violations, "
        "force unwraps, UIKit lifecycle issues."
    ),
    "kotlin-android": (
        "Familiar patterns: Coroutines, Flow, Jetpack Compose. Watch for "
        "leaked coroutines, nullability pitfalls, Context leaks, "
        "lifecycle-bound scope misuse."
    ),
    "typescript-frontend": (
        "Watch for stale closures in hooks, missing effect deps, "
        "unhandled promise rejections, `any`-types, type widening."
    ),
    "python-backend": (
        "Watch for mutable default args, `except: pass`, missing context "
        "managers, silent type-hint drift, SQL injection via string concat."
    ),
    "java-e2e": (
        "TestNG discipline: flaky selectors, implicit waits, shared state "
        "between tests, missing cleanup, hard-coded env assumptions."
    ),
}


def stack_hint(stack: str | None) -> str:
    if stack and stack in STACK_HINTS:
        return STACK_HINTS[stack]
    return ""


def truncate_diff(diff: str, *, cap_bytes: int = 200_000) -> str:
    if len(diff) <= cap_bytes:
        return diff
    head = diff[: cap_bytes - 200]
    return head + f"\n\n... [truncated {len(diff) - cap_bytes + 200} bytes] ...\n"


@dataclass(frozen=True)
class FirstReviewInputs:
    project_path: str
    mr_iid: int
    mr_title: str
    mr_description: str
    author_username: str
    target_branch: str
    base_sha: str
    head_sha: str
    diff_stat: str
    diff_text: str
    stack: str | None
    ignore_paths: list[str] | None = None


def build_first_review_prompt(inp: FirstReviewInputs) -> str:
    description = (inp.mr_description or "")[:4000]
    diff = inp.diff_text
    diff_stat = inp.diff_stat
    skipped_files: list[str] = []
    if inp.ignore_paths:
        from claude_reviewer.diff_filter import filter_diff, filter_stat
        diff, skipped_files = filter_diff(diff, inp.ignore_paths)
        diff_stat = filter_stat(diff_stat, inp.ignore_paths)
    diff = truncate_diff(diff)
    hint = stack_hint(inp.stack)
    hint_block = f"\nStack hint: {hint}\n" if hint else ""
    skipped_note = ""
    if skipped_files:
        skipped_note = (
            f"\nNote: {len(skipped_files)} file(s) skipped per ignore_paths rule "
            f"(generated/lock/build artifacts): {', '.join(skipped_files[:5])}"
            f"{' ...' if len(skipped_files) > 5 else ''}\n"
        )

    return f"""\
{SYSTEM_PROMPT}
{hint_block}
MR: {inp.project_path} !{inp.mr_iid}  —  {inp.mr_title}
Author: {inp.author_username}
Target branch: {inp.target_branch}
Base SHA: {inp.base_sha}
Head SHA: {inp.head_sha}

MR description:
<<<
{description}
>>>{skipped_note}
Changed files (--stat):
{diff_stat}

Full diff (base..head):
<<<
{diff}
>>>

Perform the first review of this MR. Follow DISCIPLINE.
Call finalize_review() when done.
"""


@dataclass(frozen=True)
class IncrementalReviewInputs:
    project_path: str
    mr_iid: int
    mr_title: str
    old_head_sha: str
    new_head_sha: str
    diff_text: str
    stack: str | None
    ignore_paths: list[str] | None = None


def build_incremental_review_prompt(inp: IncrementalReviewInputs) -> str:
    hint = stack_hint(inp.stack)
    hint_block = f"\nStack hint: {hint}\n" if hint else ""
    diff = inp.diff_text
    skipped_files: list[str] = []
    if inp.ignore_paths:
        from claude_reviewer.diff_filter import filter_diff
        diff, skipped_files = filter_diff(diff, inp.ignore_paths)
    diff = truncate_diff(diff)
    skipped_note = ""
    if skipped_files:
        skipped_note = (
            f"\nNote: {len(skipped_files)} file(s) skipped per ignore_paths rule "
            f"(generated/lock/build artifacts): {', '.join(skipped_files[:5])}"
            f"{' ...' if len(skipped_files) > 5 else ''}\n"
        )
    return f"""\
{SYSTEM_PROMPT}
{hint_block}
MR: {inp.project_path} !{inp.mr_iid}  —  {inp.mr_title}

Previous review was done at SHA: {inp.old_head_sha}
New commits pushed; current HEAD: {inp.new_head_sha}
{skipped_note}
Diff of NEW CHANGES since previous review:
<<<
{diff}
>>>

Rules for incremental reviews:
- Comment ONLY on new changes (this diff). Do not re-comment on unchanged code.
- If new changes fix a previously-flagged issue, say nothing — the old thread handles it.
- Existing bot threads are not your responsibility here.
- If you found NO new issues worth flagging, do NOT call set_summary
  and do NOT add inline comments. Stay silent on chat.
- IMPORTANT: if the delta has ZERO new blocker/major findings AND no new
  security concerns, call approve_mr() before finalize_review().
  This signals "this push didn't introduce regressions" and lets GitLab
  show the MR as bot-approved.
  Note: approve_mr() will refuse if you added any blocker/major comment in
  this run, so calling it is safe — it only succeeds when the delta is clean.

Call finalize_review() when done.
"""


@dataclass(frozen=True)
class ThreadNote:
    author: str
    ts: str
    body: str


@dataclass(frozen=True)
class ThreadReplyInputs:
    project_path: str
    mr_iid: int
    mr_title: str
    discussion_id: str
    file: str | None
    line: int | None
    current_code_excerpt: str
    thread_notes: list[ThreadNote]
    latest_note_body: str
    stack: str | None


THREAD_SYSTEM_OVERRIDE = """\
You are a senior code reviewer replying in an ongoing GitLab discussion thread.
Working directory contains a clean checkout of the MR branch at HEAD.

Available tools:
- mcp__review_sink__reply_in_thread(discussion_id, body)
- mcp__review_sink__resolve_thread(discussion_id, body_optional)
- mcp__review_sink__finalize_review()

Rules:
- Fixed? → brief ack, call resolve_thread.
- Valid push-back? → agree and call resolve_thread.
- Invalid push-back? → reply with specific evidence.
- Clarifying question? → answer.
- Off-topic / bare ack? → call finalize_review without other actions.

Exactly one of reply_in_thread / resolve_thread per run, then finalize_review.
"""


def build_thread_reply_prompt(inp: ThreadReplyInputs) -> str:
    hint = stack_hint(inp.stack)
    hint_block = f"\nStack hint: {hint}\n" if hint else ""
    history = "\n".join(
        f"[{n.author}, {n.ts}]: {n.body}" for n in inp.thread_notes
    )
    where = f"File: {inp.file}, line {inp.line}" if inp.file and inp.line else ""
    return f"""\
{THREAD_SYSTEM_OVERRIDE}
{hint_block}
MR: {inp.project_path} !{inp.mr_iid}  —  {inp.mr_title}
Discussion: {inp.discussion_id}
{where}

Current code at that location:
<<<
{inp.current_code_excerpt}
>>>

Full thread history (oldest first):
<<<
{history}
>>>

Latest reply to respond to:
<<<
{inp.latest_note_body}
>>>
"""
