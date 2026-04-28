from __future__ import annotations
import asyncio
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path

from claude_reviewer.errors import ClaudeRunError, ReviewFailed
from claude_reviewer.logging_utils import redact
from claude_reviewer.types import ReviewOutput


@dataclass(frozen=True)
class ClaudeUsage:
    cost_usd: float
    input_tokens: int
    cache_creation_tokens: int
    cache_read_tokens: int
    output_tokens: int
    duration_ms: int | None = None


def parse_usage_from_log(log_path: Path) -> ClaudeUsage | None:
    """Scan the streamed claude log for the final 'result' event and extract usage.

    Returns None if not found (e.g., process killed before final event).
    """
    try:
        text = log_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    found: dict | None = None
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict) and obj.get("type") == "result":
            found = obj
    if not found:
        return None
    usage = found.get("usage") or {}
    return ClaudeUsage(
        cost_usd=float(found.get("total_cost_usd") or 0.0),
        input_tokens=int(usage.get("input_tokens") or 0),
        cache_creation_tokens=int(usage.get("cache_creation_input_tokens") or 0),
        cache_read_tokens=int(usage.get("cache_read_input_tokens") or 0),
        output_tokens=int(usage.get("output_tokens") or 0),
        duration_ms=int(found.get("duration_ms")) if found.get("duration_ms") else None,
    )


@dataclass(frozen=True)
class ClaudeInvocation:
    prompt: str
    cwd: Path
    task_type: str
    output_file: Path
    log_file: Path
    max_turns: int = 40
    timeout_seconds: int = 600
    model: str = "sonnet"
    dry_run: bool = True  # only affects logging
    claude_binary: str = "claude"
    max_inline_comments: int = 10
    allowed_tools: tuple[str, ...] = (
        "Read", "Grep", "Glob",
        "Bash(git log:*)", "Bash(git show:*)", "Bash(git diff:*)",
        "Bash(git blame:*)", "Bash(git ls-files:*)", "Bash(git cat-file:*)",
        "mcp__review_sink__*",
    )


def build_mcp_config(*, module: str, output_path: Path, task_type: str,
                     dry_run: bool = True,
                     max_inline_comments: int = 10) -> dict:
    # Use sys.executable so the MCP subprocess uses the SAME interpreter
    # (and venv) as the orchestrator, not a different `python` from PATH.
    return {
        "mcpServers": {
            "review_sink": {
                "command": sys.executable,
                "args": ["-u", "-m", module],
                "env": {
                    "REVIEW_SINK_OUTPUT": str(output_path),
                    "REVIEW_SINK_TASK": task_type,
                    "REVIEW_SINK_DRYRUN": "true" if dry_run else "false",
                    "REVIEW_SINK_MAX_INLINE": str(max_inline_comments),
                    "PYTHONPATH": os.environ.get("PYTHONPATH", ""),
                },
            }
        }
    }


def build_cli_args(*, claude_binary: str, prompt: str,
                   mcp_config_path: Path, max_turns: int,
                   allowed_tools: list[str],
                   model: str | None = None) -> list[str]:
    args = [
        claude_binary,
        "-p", prompt,
        "--mcp-config", str(mcp_config_path),
        "--allowedTools", ",".join(allowed_tools),
        "--permission-mode", "bypassPermissions",
        "--output-format", "stream-json",
        "--verbose",
        "--max-turns", str(max_turns),
    ]
    if model:
        args += ["--model", model]
    return args


async def run_claude(inv: ClaudeInvocation) -> tuple[ReviewOutput, ClaudeUsage | None]:
    mcp_cfg = build_mcp_config(
        module="claude_reviewer.review_sink_mcp",
        output_path=inv.output_file,
        task_type=inv.task_type,
        dry_run=inv.dry_run,
        max_inline_comments=inv.max_inline_comments,
    )
    mcp_cfg_path = inv.cwd / ".mcp-config.json"
    mcp_cfg_path.write_text(json.dumps(mcp_cfg))

    args = build_cli_args(
        claude_binary=inv.claude_binary, prompt=inv.prompt,
        mcp_config_path=mcp_cfg_path, max_turns=inv.max_turns,
        allowed_tools=list(inv.allowed_tools), model=inv.model,
    )

    inv.log_file.parent.mkdir(parents=True, exist_ok=True)
    prompt_file = inv.log_file.with_suffix(".prompt.txt")
    prompt_file.write_text(inv.prompt, encoding="utf-8")

    # Log all args except the prompt body itself (too large + already saved)
    safe_args = []
    skip_next = False
    for i, a in enumerate(args):
        if skip_next:
            safe_args.append(f"[PROMPT: {len(inv.prompt)} chars, see {prompt_file.name}]")
            skip_next = False
            continue
        if a == "-p":
            safe_args.append(a)
            skip_next = True
        else:
            safe_args.append(a)

    try:
        with inv.log_file.open("w") as log:
            log.write(f"# claude args: {redact(' '.join(safe_args))}\n")
            log.flush()
            proc = await asyncio.create_subprocess_exec(
                *args, cwd=str(inv.cwd),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                stdout, stderr = await asyncio.wait_for(
                    proc.communicate(), timeout=inv.timeout_seconds,
                )
            except asyncio.TimeoutError:
                proc.kill()
                raise ClaudeRunError(
                    f"claude timed out after {inv.timeout_seconds}s"
                )
            log.write("# STDOUT\n")
            log.write(redact(stdout.decode("utf-8", errors="replace")))
            log.write("\n# STDERR\n")
            log.write(redact(stderr.decode("utf-8", errors="replace")))

        if proc.returncode != 0:
            raise ClaudeRunError(
                f"claude exited with {proc.returncode}. See {inv.log_file}"
            )

        if not inv.output_file.exists():
            raise ReviewFailed(
                f"Claude did not finalize review; {inv.output_file} missing"
            )

        # Preserve a copy of review-output.json next to the log — the original
        # lives in the worktree, which is cleaned up after the review returns.
        archived_output = inv.log_file.with_suffix(".output.json")
        try:
            archived_output.write_text(
                inv.output_file.read_text(encoding="utf-8"),
                encoding="utf-8",
            )
        except OSError:
            pass  # best effort; don't fail the review on archive failure

        try:
            review = ReviewOutput.model_validate_json(inv.output_file.read_text())
        except Exception as e:
            raise ReviewFailed(
                f"invalid review output: {e}. See {inv.log_file} and "
                f"{archived_output}"
            ) from e
        usage = parse_usage_from_log(inv.log_file)
        return review, usage
    finally:
        try:
            mcp_cfg_path.unlink(missing_ok=True)
        except OSError:
            pass
