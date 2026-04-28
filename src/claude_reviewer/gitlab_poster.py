from __future__ import annotations
import asyncio
from dataclasses import dataclass
from typing import Any

from gitlab import GitlabCreateError

from claude_reviewer.config import Config
from claude_reviewer.errors import PostError
from claude_reviewer.markers import render_body, extract_marker
from claude_reviewer.state import State
from claude_reviewer.types import (
    ReviewOutput, InlineComment, MRRefs, TaskKind,
)

POST_SEMAPHORE_LIMIT = 3


def build_position(
    refs: MRRefs, *, new_path: str, new_line: int,
    old_path: str | None = None, old_line: int | None = None,
) -> dict:
    pos: dict = {
        "base_sha": refs.base_sha,
        "start_sha": refs.start_sha,
        "head_sha": refs.head_sha,
        "position_type": "text",
        "new_path": new_path,
        "new_line": new_line,
    }
    # Context (unchanged) lines exist in both sides; GitLab needs old_* too,
    # otherwise it can't compute line_code and rejects the note.
    if old_path is not None and old_line is not None:
        pos["old_path"] = old_path
        pos["old_line"] = old_line
    return pos


def marker_for_inline(*, task_kind: str, head_sha: str,
                      file: str, line: int) -> str:
    return f"{task_kind}:{head_sha[:12]}:{file}:{line}"


def marker_for_summary(*, task_kind: str, head_sha: str) -> str:
    return f"{task_kind}:{head_sha[:12]}"


def marker_for_reply(*, discussion_id: str, action: str, last_note_id: int | str) -> str:
    # action: "reply" | "resolve"
    return f"{action}:{discussion_id}:{last_note_id}"


@dataclass(frozen=True)
class PostContext:
    cfg: Config
    task_kind: TaskKind
    head_sha: str
    dry_run: bool
    diff_text: str | None = None  # if provided, used to pre-filter inline positions


def _render_summary_text(summary) -> str:
    parts = [summary.overall]
    if summary.performance_notes:
        parts.append("\n\n**Performance notes:**\n" + summary.performance_notes)
    return "".join(parts)


def _existing_markers(mr) -> set[tuple[str, str]]:
    found: set[tuple[str, str]] = set()
    for d in mr.discussions.list(all=True):
        for note in d.attributes.get("notes", []):
            info = extract_marker(note.get("body", ""))
            if info:
                found.add((info.kind, info.key))
    return found


async def post_review(
    *, mr, refs: MRRefs, review: ReviewOutput, ctx: PostContext,
    state: State, project_id: int, mr_iid: int,
):
    """Post inline_comments and summary, recording bot discussions in state."""
    if ctx.dry_run:
        return

    # Always unapprove first: this is a fresh evaluation. If the bot previously
    # approved this MR (or even a different reviewer did) and the code has
    # changed, the prior approval is no longer valid for what we're about to
    # review. We re-approve at the end if the review is clean.
    # No-op if the MR isn't approved or the project has no approval rule.
    try:
        await asyncio.to_thread(mr.unapprove)
    except Exception as e:
        print(f"  unapprove failed (continuing): {e}", flush=True)

    # Pre-filter inline comments against parsed diff hunks: GitLab rejects
    # inline notes whose (file, new_line) isn't in any hunk's addressable set.
    pre_dropped: list[InlineComment] = []
    # Keyed by (file, line) since LineInfo is position-scoped, not object-scoped.
    # Survives list reconstruction or retries that produce fresh comment objects.
    line_info_by_pos: dict[tuple[str, int], Any] = {}
    if ctx.diff_text:
        from claude_reviewer.diff_parser import parse_addressable_lines
        addressable = parse_addressable_lines(ctx.diff_text)
        filtered = []
        for c in review.inline_comments:
            info = addressable.get((c.file, c.line))
            if info is not None:
                filtered.append(c)
                line_info_by_pos[(c.file, c.line)] = info
            else:
                pre_dropped.append(c)
        if pre_dropped:
            print(
                f"  pre-dropped {len(pre_dropped)} inline(s) — line not in diff hunk: "
                + ", ".join(f"{c.file}:{c.line}" for c in pre_dropped),
                flush=True,
            )
        review_for_post = review.model_copy(update={"inline_comments": filtered})
    else:
        review_for_post = review

    # For incremental reviews: if there's literally nothing left to post, post nothing.
    # (first_review always posts a summary so the author knows the bot looked.)
    # We've already unapproved above, so the corner case "approval was there
    # but a new commit landed" is handled even when nothing else is posted.
    # If we pre-dropped any inline comments, stay non-silent so they reach the
    # dropped-summary note instead of being lost to console-only logging.
    if (ctx.task_kind == "incremental_review"
            and not review_for_post.inline_comments
            and review_for_post.summary is None
            and not pre_dropped):
        print(f"  no new issues in incremental review; staying silent",
              flush=True)
        if review_for_post.approval:
            try:
                await asyncio.to_thread(mr.approve)
                print(f"  approved (clean incremental)", flush=True)
            except Exception as e:
                print(f"  approve failed: {e}", flush=True)
        return

    semaphore = asyncio.Semaphore(POST_SEMAPHORE_LIMIT)
    existing = _existing_markers(mr)
    dropped: list[InlineComment] = list(pre_dropped)  # accumulate all dropped

    # Summary first so users see it at top
    if review_for_post.summary:
        key = marker_for_summary(task_kind=ctx.task_kind, head_sha=ctx.head_sha)
        if ("summary", key) not in existing:
            body = render_body(
                kind="summary",
                text=_render_summary_text(review_for_post.summary),
                marker_key=key,
                visible_prefix=ctx.cfg.review.visible_tag_prefix,
            )
            async with semaphore:
                await asyncio.to_thread(mr.notes.create, {"body": body})
                await asyncio.sleep(0.1)

    for c in review_for_post.inline_comments:
        key = marker_for_inline(
            task_kind=ctx.task_kind, head_sha=ctx.head_sha,
            file=c.file, line=c.line,
        )
        if ("inline", key) in existing:
            continue
        body = render_body(
            kind="inline",
            text=c.body,
            severity=c.severity,
            category=c.category,
            marker_key=key,
            visible_prefix=ctx.cfg.review.visible_tag_prefix,
        )
        info = line_info_by_pos.get((c.file, c.line))
        if info is not None and info.kind == "context":
            position = build_position(
                refs, new_path=c.file, new_line=c.line,
                old_path=info.old_path, old_line=info.old_line,
            )
        else:
            position = build_position(refs, new_path=c.file, new_line=c.line)
        try:
            async with semaphore:
                d = await asyncio.to_thread(
                    mr.discussions.create,
                    {"body": body, "position": position},
                )
                await asyncio.sleep(0.1)
        except GitlabCreateError as e:
            print(
                f"  DROPPED inline comment {c.file}:{c.line} — "
                f"GitLab response: {e.response_code} {e.error_message}",
                flush=True,
            )
            print(f"  attempted position: {position}", flush=True)
            dropped.append(c)
            continue
        # record in state for thread-reply detection later
        note_id = d.attributes["notes"][0]["id"]
        state.add_bot_discussion(
            discussion_id=str(d.id), project_id=project_id, mr_iid=mr_iid,
            file=c.file, line=c.line, last_note_id=note_id,
        )

    if dropped:
        # append a follow-up note listing the dropped ones
        body = (
            f"{ctx.cfg.review.visible_tag_prefix} · summary\n\n"
            f"{len(dropped)} inline comment(s) were dropped due to "
            f"invalid diff position. Relevant files: "
            f"{', '.join(sorted({c.file for c in dropped}))}.\n\n"
            f"<!--claude-review:summary:dropped:{ctx.head_sha[:12]}-->"
        )
        async with semaphore:
            await asyncio.to_thread(mr.notes.create, {"body": body})

    # If the review approves this MR, mark it as approved on GitLab.
    if review_for_post.approval:
        try:
            await asyncio.to_thread(mr.approve)
            print(f"  approved by bot", flush=True)
        except Exception as e:
            print(f"  approve failed: {e}", flush=True)


async def post_thread_action(
    *, mr, review: ReviewOutput, ctx: PostContext,
    state: State, last_note_id: int | str,
):
    """Post a thread reply or resolve per review.thread_action."""
    action = review.thread_action
    if action is None or ctx.dry_run:
        return

    existing = _existing_markers(mr)
    key = marker_for_reply(
        discussion_id=action.discussion_id,
        action=action.action,
        last_note_id=last_note_id,
    )
    if (action.action, key) in existing:
        return

    d = mr.discussions.get(action.discussion_id)

    if action.body:
        body = render_body(
            kind=action.action,  # "reply" | "resolve"
            text=action.body,
            marker_key=key,
            visible_prefix=ctx.cfg.review.visible_tag_prefix,
        )
        await asyncio.to_thread(d.notes.create, {"body": body})
        await asyncio.sleep(0.1)

    if action.action == "resolve":
        d.resolved = True
        await asyncio.to_thread(d.save)
        state.mark_discussion_resolved(action.discussion_id)
