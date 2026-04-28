from __future__ import annotations
import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

from claude_reviewer.claude_runner import run_claude, ClaudeInvocation, ClaudeUsage
from claude_reviewer.config import Config, ProjectConfig
from claude_reviewer.gitlab_client import GitLabClient
from claude_reviewer.gitlab_poster import (
    post_review, post_thread_action, PostContext,
)
from claude_reviewer.markers import is_bot_note
from claude_reviewer.prompt_builder import (
    build_first_review_prompt, FirstReviewInputs,
    build_incremental_review_prompt, IncrementalReviewInputs,
    build_thread_reply_prompt, ThreadReplyInputs, ThreadNote,
)
from claude_reviewer.errors import RepoError
from claude_reviewer.repo_manager import RepoManager
from claude_reviewer.state import State, BotDiscussion
from claude_reviewer.types import (
    Task, FirstReviewTask, IncrementalReviewTask, ThreadReplyTask, MRRefs,
)


def _clone_url(cfg, project_path: str) -> str:
    """Build the SSH clone URL for a configured project."""
    host = cfg.gitlab.base_url.split("//", 1)[1]
    # strip optional trailing slash and any path
    host = host.split("/", 1)[0]
    return f"git@{host}:{project_path}.git"


@dataclass
class ReconcileInputs:
    project_id: int
    mrs: list                        # python-gitlab MR objects or SimpleNamespace
    bot_discussions: list[BotDiscussion]
    discussion_loader: Callable[[str], object | None]
    target_branches: list[str]
    bot_username: str


def decide_tasks_for_project(inputs: ReconcileInputs, state: State) -> list[Task]:
    tasks: list[Task] = []

    # 1. First / incremental review per MR
    for mr in inputs.mrs:
        if inputs.target_branches and mr.target_branch not in inputs.target_branches:
            continue
        last_sha = state.get_reviewed_sha(inputs.project_id, mr.iid)
        if last_sha is None:
            tasks.append(FirstReviewTask(inputs.project_id, mr.iid))
        elif last_sha != mr.sha:
            tasks.append(IncrementalReviewTask(
                inputs.project_id, mr.iid, last_sha, mr.sha,
            ))

    # 2. Thread replies: scan bot-owned discussions for new non-bot notes
    for bd in inputs.bot_discussions:
        disc = inputs.discussion_loader(bd.discussion_id)
        if disc is None:
            continue
        notes = disc.attributes.get("notes", [])
        new_human_notes = [
            n for n in notes
            if n["id"] > bd.last_note_id
            and not is_bot_note(n, bot_username=inputs.bot_username)
        ]
        if new_human_notes:
            tasks.append(ThreadReplyTask(
                inputs.project_id, bd.mr_iid, bd.discussion_id,
                new_human_notes[-1]["id"],
            ))
    return tasks


def poll_forever_shutdown_event() -> asyncio.Event:
    return asyncio.Event()


def _log_dir(cfg: Config) -> Path:
    return Path(cfg.paths.logs_dir) / "reviews" / \
        datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _resolve_local_shas(rm: RepoManager, proj_cfg: ProjectConfig,
                        mr_iid: int, target_branch: str) -> tuple[str, str]:
    """Compute (base_sha, head_sha) from local refs (API's diff_refs can be stale)."""
    mr_ref = f"refs/remotes/origin/mr/{mr_iid}"
    try:
        head_sha = rm.run_git(
            proj_cfg.path, "rev-parse", mr_ref, timeout=10,
        ).strip()
    except RepoError:
        # Bulk fetch occasionally misses individual MR refs; fetch on-demand.
        rm.fetch_mr_ref(proj_cfg.path, mr_iid)
        head_sha = rm.run_git(
            proj_cfg.path, "rev-parse", mr_ref, timeout=10,
        ).strip()
    target = rm.run_git(
        proj_cfg.path, "rev-parse",
        f"refs/remotes/origin/{target_branch}",
        timeout=10,
    ).strip()
    base_sha = rm.run_git(
        proj_cfg.path, "merge-base", head_sha, target,
        timeout=10,
    ).strip()
    return base_sha, head_sha


async def handle_task(task: Task, *, cfg: Config, state: State,
                      gl: GitLabClient, rm: RepoManager) -> float | None:
    if isinstance(task, FirstReviewTask):
        return await _handle_first_or_incremental(task, cfg=cfg, state=state,
                                                  gl=gl, rm=rm,
                                                  kind="first_review")
    elif isinstance(task, IncrementalReviewTask):
        return await _handle_first_or_incremental(task, cfg=cfg, state=state,
                                                  gl=gl, rm=rm,
                                                  kind="incremental_review")
    elif isinstance(task, ThreadReplyTask):
        return await _handle_thread_reply(task, cfg=cfg, state=state, gl=gl, rm=rm)
    else:
        raise RuntimeError(f"unknown task type: {task}")


async def _handle_first_or_incremental(task, *, cfg, state, gl, rm, kind):
    proj_cfg = cfg.get_project_by_id(task.project_id)
    if proj_cfg is None:
        return
    mr = gl.get_mr(proj_cfg.id, task.mr_iid)
    task_id = state.log_task_started(
        kind, project_id=task.project_id, mr_iid=task.mr_iid,
        head_sha=None, discussion_id=None,
    )
    try:
        rm.ensure_cloned(proj_cfg.path, _clone_url(cfg, proj_cfg.path))
        rm.fetch(proj_cfg.path)
        base_sha, head_sha = _resolve_local_shas(
            rm, proj_cfg, task.mr_iid, mr.target_branch,
        )
        state.log_task_status(task_id, "started")

        # For git operations (diff, worktree) we use locally-resolved shas to
        # avoid stale-API pitfalls. For GitLab API position payloads we must
        # use what GitLab has internally — that's what GitLab itself uses to
        # resolve positions when validating our inline comments.
        try:
            api_refs = gl.get_diff_refs(mr)
        except Exception:
            # Fall back to local if API refs unusable
            api_refs = MRRefs(base_sha=base_sha, start_sha=base_sha,
                              head_sha=head_sha)
        refs = api_refs

        with rm.worktree(proj_cfg.path, head_sha) as wt:
            if isinstance(task, IncrementalReviewTask):
                diff_text = rm.run_git(
                    proj_cfg.path, "diff",
                    f"{task.old_head_sha}..{task.new_head_sha}",
                    cwd_worktree=wt, timeout=60,
                )
                prompt = build_incremental_review_prompt(IncrementalReviewInputs(
                    project_path=proj_cfg.path, mr_iid=task.mr_iid,
                    mr_title=mr.title,
                    old_head_sha=task.old_head_sha,
                    new_head_sha=task.new_head_sha,
                    diff_text=diff_text, stack=proj_cfg.stack,
                    ignore_paths=cfg.resolved_ignore_paths(proj_cfg),
                ))
            else:
                diff_stat = rm.run_git(
                    proj_cfg.path, "diff", "--stat",
                    f"{base_sha}..{head_sha}",
                    cwd_worktree=wt, timeout=60,
                )
                diff_text = rm.run_git(
                    proj_cfg.path, "diff",
                    f"{base_sha}..{head_sha}",
                    cwd_worktree=wt, timeout=60,
                )
                prompt = build_first_review_prompt(FirstReviewInputs(
                    project_path=proj_cfg.path, mr_iid=task.mr_iid,
                    mr_title=mr.title,
                    mr_description=mr.description or "",
                    author_username=mr.author["username"],
                    target_branch=mr.target_branch,
                    base_sha=base_sha, head_sha=head_sha,
                    diff_stat=diff_stat, diff_text=diff_text,
                    stack=proj_cfg.stack,
                    ignore_paths=cfg.resolved_ignore_paths(proj_cfg),
                ))

            log_dir = _log_dir(cfg); log_dir.mkdir(parents=True, exist_ok=True)
            slug = proj_cfg.path.replace("/", "_")
            log_file = log_dir / f"{slug}_mr{task.mr_iid}_{kind}_{head_sha[:7]}.jsonl"
            output_file = wt / "review-output.json"

            print(f"  running claude ({kind}, head={head_sha[:7]}) ...", flush=True)
            out, usage = await run_claude(ClaudeInvocation(
                prompt=prompt, cwd=wt,
                task_type=kind,
                output_file=output_file, log_file=log_file,
                max_turns=cfg.claude.max_turns,
                timeout_seconds=cfg.claude.timeout_seconds,
                model=cfg.claude.model,
                dry_run=cfg.review.dry_run,
                claude_binary=cfg.claude.cli_binary,
                max_inline_comments=cfg.review.max_inline_comments,
            ))
            state.log_task_status(task_id, "finalized")
            if usage is not None:
                print(
                    f"  cost ~${usage.cost_usd:.4f} "
                    f"(in={usage.input_tokens} +cache_create={usage.cache_creation_tokens}"
                    f" +cache_read={usage.cache_read_tokens}, out={usage.output_tokens})",
                    flush=True,
                )
                state.add_task_cost(task_id, usage.cost_usd, usage)

            await post_review(
                mr=mr, refs=refs, review=out,
                ctx=PostContext(cfg=cfg, task_kind=kind,
                                head_sha=head_sha,
                                dry_run=cfg.review.dry_run,
                                diff_text=diff_text),
                state=state, project_id=task.project_id, mr_iid=task.mr_iid,
            )
            state.log_task_status(task_id, "posted")
            state.set_reviewed_sha(task.project_id, task.mr_iid,
                                   head_sha, datetime.now(timezone.utc))
            return usage.cost_usd if usage else None
    except Exception as e:
        state.log_task_status(task_id, "failed", error=str(e))
        raise


async def _handle_thread_reply(task: ThreadReplyTask, *, cfg, state, gl, rm):
    proj_cfg = cfg.get_project_by_id(task.project_id)
    if proj_cfg is None:
        return
    mr = gl.get_mr(proj_cfg.id, task.mr_iid)
    d = mr.discussions.get(task.discussion_id)
    notes = d.attributes.get("notes", [])
    if not notes:
        return
    latest = notes[-1]
    first_note = notes[0]
    file = (first_note.get("position") or {}).get("new_path")
    line = (first_note.get("position") or {}).get("new_line")

    task_id = state.log_task_started(
        "thread_reply", project_id=task.project_id, mr_iid=task.mr_iid,
        head_sha=None, discussion_id=task.discussion_id,
    )

    try:
        rm.ensure_cloned(proj_cfg.path, _clone_url(cfg, proj_cfg.path))
        rm.fetch(proj_cfg.path)
        _, head_sha = _resolve_local_shas(
            rm, proj_cfg, task.mr_iid, mr.target_branch,
        )

        with rm.worktree(proj_cfg.path, head_sha) as wt:
            excerpt = ""
            if file and line:
                try:
                    full = (wt / file).read_text(errors="replace").splitlines()
                    lo = max(0, line - 20); hi = min(len(full), line + 20)
                    excerpt = "\n".join(
                        f"{i+1:5d}  {full[i]}" for i in range(lo, hi)
                    )
                except FileNotFoundError:
                    excerpt = "(file not found at head)"

            thread_notes = [
                ThreadNote(
                    author=n["author"]["username"],
                    ts=str(n.get("created_at", "")),
                    body=n.get("body", ""),
                ) for n in notes
            ]
            prompt = build_thread_reply_prompt(ThreadReplyInputs(
                project_path=proj_cfg.path, mr_iid=task.mr_iid,
                mr_title=mr.title,
                discussion_id=task.discussion_id,
                file=file, line=line,
                current_code_excerpt=excerpt,
                thread_notes=thread_notes,
                latest_note_body=latest.get("body", ""),
                stack=proj_cfg.stack,
            ))

            log_dir = _log_dir(cfg); log_dir.mkdir(parents=True, exist_ok=True)
            slug = proj_cfg.path.replace("/", "_")
            log_file = log_dir / (
                f"{slug}_mr{task.mr_iid}_thread_{task.discussion_id}_{head_sha[:7]}.jsonl"
            )
            output_file = wt / "review-output.json"

            print(f"  running claude (thread_reply, head={head_sha[:7]}) ...", flush=True)
            out, usage = await run_claude(ClaudeInvocation(
                prompt=prompt, cwd=wt, task_type="thread_reply",
                output_file=output_file, log_file=log_file,
                max_turns=cfg.claude.max_turns,
                timeout_seconds=cfg.claude.timeout_seconds,
                model=cfg.claude.model,
                dry_run=cfg.review.dry_run,
                claude_binary=cfg.claude.cli_binary,
                max_inline_comments=cfg.review.max_inline_comments,
            ))
            state.log_task_status(task_id, "finalized")
            if usage is not None:
                print(
                    f"  cost ~${usage.cost_usd:.4f} "
                    f"(in={usage.input_tokens} +cache_create={usage.cache_creation_tokens}"
                    f" +cache_read={usage.cache_read_tokens}, out={usage.output_tokens})",
                    flush=True,
                )
                state.add_task_cost(task_id, usage.cost_usd, usage)

            await post_thread_action(
                mr=mr, review=out,
                ctx=PostContext(cfg=cfg, task_kind="thread_reply",
                                head_sha=head_sha,
                                dry_run=cfg.review.dry_run),
                state=state,
                last_note_id=task.last_note_id,
            )
            state.log_task_status(task_id, "posted")
            state.update_last_note_id(task.discussion_id, task.last_note_id)
            return usage.cost_usd if usage else None
    except Exception as e:
        state.log_task_status(task_id, "failed", error=str(e))
        raise


async def reconcile_project(proj_cfg: ProjectConfig, *, cfg: Config,
                            state: State, gl: GitLabClient,
                            rm: RepoManager,
                            stop_event: asyncio.Event | None = None,
                            on_task_done=None) -> None:
    """Reconcile one project. Catches per-task failures so the watermark can
    advance. `on_task_done` callback is invoked with (task_kind, status)."""
    last = state.get_last_check(proj_cfg.id)
    now = datetime.now(timezone.utc)
    stale_cutoff = now - timedelta(days=cfg.poll.stale_mr_ignore_days)
    # Skip MRs untouched for longer than stale_mr_ignore_days. On a fresh start
    # (last is None) this also bounds the initial backlog. Watermark still wins
    # if it's more recent than the staleness cutoff.
    effective_after = max(last, stale_cutoff) if last else stale_cutoff
    mrs = gl.list_opened_mrs(
        proj_cfg.id, updated_after=effective_after,
        target_branches=proj_cfg.target_branches,
    )
    bot_discussions = state.active_bot_discussions(proj_cfg.id)

    def _loader(did: str):
        try:
            mr_iid = state.mr_iid_for_discussion(did)
            if mr_iid is None:
                return None
            return gl.get_discussion(proj_cfg.id, mr_iid, did)
        except Exception:
            import traceback
            traceback.print_exc()
            return None

    inputs = ReconcileInputs(
        project_id=proj_cfg.id, mrs=mrs,
        bot_discussions=bot_discussions,
        discussion_loader=_loader,
        target_branches=proj_cfg.target_branches,
        bot_username=cfg.gitlab.bot_username,
    )
    tasks = decide_tasks_for_project(inputs, state)
    if tasks:
        print(f"[{proj_cfg.path}] {len(tasks)} task(s): "
              + ", ".join(_task_summary(t) for t in tasks),
              flush=True)
    had_failure = False
    for t in tasks:
        if stop_event is not None and stop_event.is_set():
            return  # Don't advance watermark if we're shutting down mid-batch
        kind_str = _task_kind_of(t)
        summary = _task_summary(t)
        print(f"[{proj_cfg.path}] -> {summary} ...", flush=True)
        try:
            cost = await handle_task(t, cfg=cfg, state=state, gl=gl, rm=rm)
            cost_str = f" ~${cost:.4f}" if cost else ""
            print(f"[{proj_cfg.path}] <- {summary} posted{cost_str}", flush=True)
            if on_task_done:
                on_task_done(kind_str, "posted", cost or 0.0)
        except Exception as e:
            had_failure = True
            print(f"[{proj_cfg.path}] <- {summary} FAILED: {e}", flush=True)
            import traceback
            traceback.print_exc()
            if on_task_done:
                on_task_done(kind_str, "failed", 0.0)
            # Continue to next task; do NOT re-raise
    if not had_failure:
        # Only advance watermark if every task succeeded. Otherwise the failed
        # MR(s) would not be picked up next cycle if updated_after filters them.
        state.set_last_check(proj_cfg.id, now)
    else:
        print(f"[{proj_cfg.path}] watermark NOT advanced (had failures); "
              f"will retry on next cycle",
              flush=True)


def _task_kind_of(t) -> str:
    if isinstance(t, FirstReviewTask):
        return "first_review"
    if isinstance(t, IncrementalReviewTask):
        return "incremental_review"
    if isinstance(t, ThreadReplyTask):
        return "thread_reply"
    return "unknown"


def _task_summary(t) -> str:
    if isinstance(t, FirstReviewTask):
        return f"first_review MR!{t.mr_iid}"
    if isinstance(t, IncrementalReviewTask):
        return (f"incremental_review MR!{t.mr_iid} "
                f"{t.old_head_sha[:7]}..{t.new_head_sha[:7]}")
    if isinstance(t, ThreadReplyTask):
        return f"thread_reply MR!{t.mr_iid} discussion={t.discussion_id} note={t.last_note_id}"
    return "unknown"


async def poll_loop(cfg: Config, state: State, gl: GitLabClient,
                    rm: RepoManager, stop_event: asyncio.Event) -> None:
    from claude_reviewer.metrics import Metrics
    metrics = Metrics(Path("./metrics.json"))
    metrics.start()

    def _on_task_done(kind: str, status: str, cost_usd: float = 0.0) -> None:
        metrics.record_task(kind, status)
        if cost_usd > 0:
            metrics.record_cost(cost_usd)
        if status == "failed":
            metrics.record_error()

    while not stop_event.is_set():
        metrics.record_poll()
        print(f"[poll cycle #{metrics.poll_cycles}] "
              f"checking {sum(1 for p in cfg.projects if p.review_enabled)} "
              f"enabled project(s)",
              flush=True)
        for proj in cfg.projects:
            if stop_event.is_set():
                break
            if not proj.review_enabled:
                continue
            if Path("./STOP").exists():
                metrics.write()
                return
            try:
                await reconcile_project(
                    proj, cfg=cfg, state=state, gl=gl, rm=rm,
                    stop_event=stop_event,
                    on_task_done=_on_task_done,
                )
            except Exception:
                metrics.record_error()
                import traceback; traceback.print_exc()
        total = 0
        for p in cfg.projects:
            total += len(state.active_bot_discussions(p.id))
        metrics.active_bot_discussions = total
        metrics.write()
        print(f"[poll cycle #{metrics.poll_cycles}] done; "
              f"active bot discussions: {total}; "
              f"sleeping {cfg.poll.interval_seconds}s",
              flush=True)
        try:
            await asyncio.wait_for(stop_event.wait(),
                                   timeout=cfg.poll.interval_seconds)
        except asyncio.TimeoutError:
            pass
