"""MCP server spawned by `claude` CLI. Collects review output into JSON.

Runs as: python -m claude_reviewer.review_sink_mcp
Or via `claude --mcp-config` pointing at this entrypoint.
"""
from __future__ import annotations
import json
import os
import tempfile
from pathlib import Path
from typing import Literal, Callable

from mcp.server.fastmcp import FastMCP

MAX_INLINE_COMMENTS = int(os.environ.get("REVIEW_SINK_MAX_INLINE", "10"))

mcp = FastMCP("review_sink")


class _State:
    def __init__(self):
        self.inline: list[dict] = []
        self.summary: dict | None = None
        self.thread_action: dict | None = None
        self.finalized = False
        self.approval = False


_state = _State()
_TOOLS: dict[str, Callable] = {}


def reset_state() -> None:
    global _state
    _state = _State()


def _task_type() -> str:
    return os.environ.get("REVIEW_SINK_TASK", "first_review")


def _output_path() -> Path:
    p = os.environ.get("REVIEW_SINK_OUTPUT")
    if not p:
        raise RuntimeError("REVIEW_SINK_OUTPUT env not set")
    return Path(p)


def _tool(fn):
    _TOOLS[fn.__name__] = fn
    return mcp.tool()(fn)


@_tool
def add_inline_comment(
    file: str,
    line: int,
    severity: Literal["blocker", "major", "minor"],
    category: Literal["bug", "security", "design", "tests"],
    body: str,
) -> str:
    if _task_type() == "thread_reply":
        return "error: add_inline_comment not allowed in thread_reply task"
    if _state.finalized:
        return "error: review already finalized"
    if line <= 0:
        return f"error: line must be > 0, got {line}"
    if len(body) < 20:
        return "error: body must be at least 20 characters"
    if len(body) > 2000:
        return "error: body must be at most 2000 characters"
    if len(_state.inline) >= MAX_INLINE_COMMENTS:
        return f"error: limit reached ({MAX_INLINE_COMMENTS}). Call finalize_review."
    if any(c["file"] == file and c["line"] == line for c in _state.inline):
        return "error: comment for this file:line already exists; merge feedback"
    _state.inline.append({
        "file": file, "line": line, "severity": severity,
        "category": category, "body": body,
    })
    return f"ok ({len(_state.inline)}/{MAX_INLINE_COMMENTS})"


@_tool
def set_summary(
    overall_assessment: str,
    performance_notes: str | None = None,
) -> str:
    if _task_type() == "thread_reply":
        return "error: set_summary not allowed in thread_reply task"
    if _state.finalized:
        return "error: review already finalized"
    if len(overall_assessment) < 20:
        return "error: overall_assessment must be at least 20 chars"
    if len(overall_assessment) > 3000:
        return "error: overall_assessment too long (max 3000)"
    if performance_notes and len(performance_notes) > 1500:
        return "error: performance_notes too long (max 1500)"
    _state.summary = {
        "overall": overall_assessment,
        "performance_notes": performance_notes,
    }
    return "ok"


@_tool
def finalize_review() -> str:
    if _state.finalized:
        return "error: already finalized"
    try:
        out = _output_path()
    except RuntimeError as e:
        return f"error: {e}"
    payload = {
        "task_type": _task_type(),
        "inline_comments": _state.inline,
        "summary": _state.summary,
        "thread_action": _state.thread_action,
        "approval": _state.approval,
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=out.parent, prefix=".rs-", suffix=".json")
    os.close(fd)
    tmp_path = Path(tmp)
    try:
        tmp_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        os.replace(tmp_path, out)
    except Exception:
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise
    _state.finalized = True
    return "ok, review finalized"


@_tool
def reply_in_thread(discussion_id: str, body: str) -> str:
    if _task_type() != "thread_reply":
        return "error: reply_in_thread only in thread_reply task"
    if _state.thread_action is not None:
        return "error: thread action already set"
    if len(body) < 5 or len(body) > 2000:
        return "error: body length out of range (5..2000)"
    _state.thread_action = {
        "action": "reply", "discussion_id": discussion_id, "body": body,
    }
    return "ok"


@_tool
def resolve_thread(discussion_id: str, body: str | None = None) -> str:
    if _task_type() != "thread_reply":
        return "error: resolve_thread only in thread_reply task"
    if _state.thread_action is not None:
        return "error: thread action already set"
    if body and len(body) > 1000:
        return "error: body too long (max 1000)"
    _state.thread_action = {
        "action": "resolve", "discussion_id": discussion_id, "body": body,
    }
    return "ok"


@_tool
def approve_mr() -> str:
    """Mark this MR as approved by the reviewer bot. Only in first_review/incremental_review.

    Refuses approval if any blocker- or major-severity inline comment was recorded
    in this run. The invariant is enforced here, not just by the system prompt,
    as defense-in-depth.
    """
    if _task_type() not in ("first_review", "incremental_review"):
        return "error: approve_mr only in first_review/incremental_review"
    if _state.finalized:
        return "error: review already finalized"
    blocking = [c for c in _state.inline if c["severity"] in ("blocker", "major")]
    if blocking:
        files = ", ".join(sorted({c["file"] for c in blocking}))
        return (
            f"error: cannot approve — {len(blocking)} blocker/major comment(s) "
            f"recorded ({files}). Resolve those first or downgrade severity."
        )
    _state.approval = True
    return "ok, MR will be approved"


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
