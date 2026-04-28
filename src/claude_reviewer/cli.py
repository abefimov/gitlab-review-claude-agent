from __future__ import annotations
import asyncio
import json as _json
import signal
import sys
from pathlib import Path
from datetime import datetime, timezone

import click
from dotenv import load_dotenv

from claude_reviewer.config import load_config
from claude_reviewer.errors import ReviewerError
from claude_reviewer.gitlab_client import GitLabClient, parse_mr_url
from claude_reviewer.orchestrator import poll_loop
from claude_reviewer.repo_manager import RepoManager, RepoOptions
from claude_reviewer.prompt_builder import (
    build_first_review_prompt, FirstReviewInputs,
)
from claude_reviewer.claude_runner import run_claude, ClaudeInvocation
from claude_reviewer.state import State


@click.group()
@click.option("--config", "config_path", default="config.toml",
              type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.pass_context
def main(ctx: click.Context, config_path: Path):
    load_dotenv()  # load .env from cwd if present
    ctx.ensure_object(dict)
    ctx.obj["cfg"] = load_config(config_path)


@main.command("review")
@click.argument("mr_url")
@click.option("--dry-run/--live", default=None,
              help="Override config.review.dry_run")
@click.pass_context
def review_cmd(ctx: click.Context, mr_url: str, dry_run: bool | None):
    cfg = ctx.obj["cfg"]
    if dry_run is not None:
        cfg.review.dry_run = dry_run
    try:
        asyncio.run(_do_review(cfg, mr_url))
    except ReviewerError as e:
        click.echo(f"error: {e}", err=True)
        sys.exit(2)


async def _do_review(cfg, mr_url: str):
    ref = parse_mr_url(mr_url)
    proj_cfg = cfg.get_project(ref.project_path)
    if proj_cfg is None:
        click.echo(f"project {ref.project_path} not in config.projects",
                   err=True)
        sys.exit(2)

    gl = GitLabClient(cfg)
    click.echo(f"fetching MR {ref.project_path} !{ref.mr_iid} ...")
    mr = gl.get_mr(proj_cfg.id, ref.mr_iid)
    diff_refs = gl.get_diff_refs(mr)

    rm = RepoManager(RepoOptions(
        repo_root=Path(cfg.repo.root),
        worktree_root=Path(cfg.repo.worktree_root),
        clone_mode=cfg.repo.clone_mode,
        fetch_timeout_seconds=cfg.repo.fetch_timeout_seconds,
        worktree_timeout_seconds=cfg.repo.worktree_timeout_seconds,
    ))

    clone_url = f"git@{cfg.gitlab.base_url.split('//')[1]}:{ref.project_path}.git"
    click.echo("ensuring clone ...")
    rm.ensure_cloned(ref.project_path, clone_url)
    click.echo("fetching ...")
    rm.fetch(ref.project_path)

    # Resolve the actual head sha from the local MR ref instead of trusting
    # diff_refs.head_sha — the API can return a stale or merge-result sha
    # that isn't reachable from origin (happens with drafts or after rebase).
    head_sha_local = rm.run_git(
        ref.project_path, "rev-parse",
        f"refs/remotes/origin/mr/{ref.mr_iid}",
        timeout=10,
    ).strip()
    if not head_sha_local:
        raise ReviewerError(
            f"MR !{ref.mr_iid}: no local ref refs/remotes/origin/mr/{ref.mr_iid}. "
            "Run `git fetch origin` on the bare repo and retry."
        )
    click.echo(f"mr head sha (local): {head_sha_local}")
    if head_sha_local != diff_refs.head_sha:
        click.echo(
            f"note: API head_sha={diff_refs.head_sha[:12]} differs from "
            f"local mr/{ref.mr_iid}={head_sha_local[:12]}; using local"
        )

    # Resolve target branch local sha
    try:
        target_local = rm.run_git(
            ref.project_path, "rev-parse",
            f"refs/remotes/origin/{mr.target_branch}",
            timeout=10,
        ).strip()
    except Exception as e:
        raise ReviewerError(
            f"MR !{ref.mr_iid}: target branch '{mr.target_branch}' not found locally "
            f"after fetch ({e}). Check target_branches config or that branch exists."
        ) from e

    # Compute base via local merge-base (API's diff_refs.base_sha may be stale)
    base_sha_local = rm.run_git(
        ref.project_path, "merge-base", head_sha_local, target_local,
        timeout=10,
    ).strip()
    if not base_sha_local:
        raise ReviewerError(
            f"MR !{ref.mr_iid}: could not compute merge-base between "
            f"{head_sha_local[:12]} and {target_local[:12]}"
        )
    click.echo(f"base sha (merge-base): {base_sha_local}")
    if base_sha_local != diff_refs.base_sha:
        click.echo(
            f"note: API base_sha={diff_refs.base_sha[:12]} differs from "
            f"local merge-base={base_sha_local[:12]}; using local"
        )

    with rm.worktree(ref.project_path, head_sha_local) as wt:
        click.echo(f"worktree: {wt}")

        diff_stat = rm.run_git(
            ref.project_path, "diff", "--stat",
            f"{base_sha_local}..{head_sha_local}",
            cwd_worktree=wt, timeout=60,
        )
        diff_text = rm.run_git(
            ref.project_path, "diff",
            f"{base_sha_local}..{head_sha_local}",
            cwd_worktree=wt, timeout=60,
        )

        prompt = build_first_review_prompt(FirstReviewInputs(
            project_path=ref.project_path, mr_iid=ref.mr_iid,
            mr_title=mr.title, mr_description=mr.description or "",
            author_username=mr.author["username"],
            target_branch=mr.target_branch,
            base_sha=base_sha_local, head_sha=head_sha_local,
            diff_stat=diff_stat, diff_text=diff_text,
            stack=proj_cfg.stack,
            ignore_paths=cfg.resolved_ignore_paths(proj_cfg),
        ))

        log_dir = Path(cfg.paths.logs_dir) / "reviews" / \
            datetime.now(timezone.utc).strftime("%Y-%m-%d")
        log_dir.mkdir(parents=True, exist_ok=True)
        slug = ref.project_path.replace("/", "_")
        head7 = head_sha_local[:7]
        log_file = log_dir / f"{slug}_mr{ref.mr_iid}_first_{head7}.jsonl"
        output_file = wt / "review-output.json"

        click.echo("running claude ...")
        out, usage = await run_claude(ClaudeInvocation(
            prompt=prompt, cwd=wt, task_type="first_review",
            output_file=output_file, log_file=log_file,
            max_turns=cfg.claude.max_turns,
            timeout_seconds=cfg.claude.timeout_seconds,
            model=cfg.claude.model,
            dry_run=cfg.review.dry_run,
            claude_binary=cfg.claude.cli_binary,
            max_inline_comments=cfg.review.max_inline_comments,
        ))

    click.echo(f"\nreview finalized: {len(out.inline_comments)} inline, "
               f"summary={'yes' if out.summary else 'no'}")
    click.echo(f"log: {log_file}")
    click.echo(f"archived output: {log_file.with_suffix('.output.json')}")
    click.echo(f"prompt: {log_file.with_suffix('.prompt.txt')}")
    if cfg.review.dry_run:
        click.echo("[dry-run] nothing posted to GitLab")
    if usage is not None:
        click.echo(f"cost ~${usage.cost_usd:.4f} "
                   f"(in={usage.input_tokens}+cache_create={usage.cache_creation_tokens}"
                   f"+cache_read={usage.cache_read_tokens}, out={usage.output_tokens})")


@main.command("cost")
@click.option("--since", default=None,
              help="ISO date/time, e.g. 2026-04-25 or 2026-04-25T00:00:00")
@click.pass_context
def cost_cmd(ctx, since):
    cfg = ctx.obj["cfg"]
    st = State(Path(cfg.paths.state_db))
    summary = st.cost_summary(since_iso=since)
    if not summary:
        click.echo("no cost data recorded yet")
        return
    total_cost = sum(v["cost_usd"] for v in summary.values())
    total_in = sum(v["input_tokens"] for v in summary.values())
    total_cache = sum(v["cache_read_tokens"] for v in summary.values())
    total_cache_create = sum(v["cache_creation_tokens"] for v in summary.values())
    total_out = sum(v["output_tokens"] for v in summary.values())
    total_n = sum(v["n"] for v in summary.values())

    click.echo(f"{'task_type':<22} {'count':>6} {'cost USD':>10} "
               f"{'in':>9} {'cache_create':>13} {'cache_read':>11} {'out':>8}")
    click.echo("-" * 88)
    for kind, v in sorted(summary.items()):
        click.echo(f"{kind:<22} {v['n']:>6} ${v['cost_usd']:>9.4f} "
                   f"{v['input_tokens']:>9,} "
                   f"{v['cache_creation_tokens']:>13,} "
                   f"{v['cache_read_tokens']:>11,} "
                   f"{v['output_tokens']:>8,}")
    click.echo("-" * 88)
    click.echo(f"{'TOTAL':<22} {total_n:>6} ${total_cost:>9.4f} "
               f"{total_in:>9,} {total_cache_create:>13,} "
               f"{total_cache:>11,} {total_out:>8,}")


def _make_runtime(cfg):
    gl = GitLabClient(cfg)
    state = State(Path(cfg.paths.state_db))
    rm = RepoManager(RepoOptions(
        repo_root=Path(cfg.repo.root),
        worktree_root=Path(cfg.repo.worktree_root),
        clone_mode=cfg.repo.clone_mode,
        fetch_timeout_seconds=cfg.repo.fetch_timeout_seconds,
        worktree_timeout_seconds=cfg.repo.worktree_timeout_seconds,
    ))
    return gl, state, rm


@main.command("daemon")
@click.pass_context
def daemon_cmd(ctx):
    cfg = ctx.obj["cfg"]
    gl, state, rm = _make_runtime(cfg)

    from claude_reviewer.validation import (
        check_claude_binary, check_gitlab_token,
    )
    cb = check_claude_binary(cfg.claude.cli_binary)
    if not cb.ok:
        click.echo(f"validation failed: {cb.message}", err=True)
        sys.exit(2)
    click.echo(f"claude: {cb.message}")
    gt = check_gitlab_token(gl.gl, expected=cfg.gitlab.bot_username)
    if not gt.ok:
        click.echo(f"validation failed: {gt.message}", err=True)
        sys.exit(2)
    click.echo(f"gitlab: {gt.message}")

    # Pre-clone all enabled projects so the first poll cycle doesn't fail
    # on RepoError. Idempotent — already-cloned repos are skipped quickly.
    host = cfg.gitlab.base_url.split("//", 1)[1].split("/", 1)[0]
    for proj in cfg.projects:
        if not proj.review_enabled:
            continue
        try:
            click.echo(f"ensuring clone: {proj.path} ...")
            rm.ensure_cloned(proj.path, f"git@{host}:{proj.path}.git")
        except Exception as e:
            click.echo(f"  failed to clone {proj.path}: {e}", err=True)
            sys.exit(2)

    async def _run():
        stop = asyncio.Event()
        loop = asyncio.get_running_loop()
        for s in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(s, stop.set)
        await poll_loop(cfg, state, gl, rm, stop)

    click.echo("starting daemon (Ctrl+C to stop) ...")
    asyncio.run(_run())


@main.command("clone")
@click.argument("project_path")
@click.pass_context
def clone_cmd(ctx, project_path):
    cfg = ctx.obj["cfg"]
    _, _, rm = _make_runtime(cfg)
    proj_cfg = cfg.get_project(project_path)
    if not proj_cfg:
        click.echo(f"project {project_path} not in config", err=True)
        sys.exit(2)
    host = cfg.gitlab.base_url.split("//", 1)[1]
    clone_url = f"git@{host}:{project_path}.git"
    rm.ensure_cloned(project_path, clone_url)
    rm.fetch(project_path)
    click.echo("cloned and fetched")


@main.group("state")
def state_group():
    pass


@state_group.command("list")
@click.pass_context
def state_list(ctx):
    cfg = ctx.obj["cfg"]
    st = State(Path(cfg.paths.state_db))
    for proj in cfg.projects:
        ds = st.active_bot_discussions(proj.id)
        click.echo(f"- {proj.path} (id={proj.id}): "
                   f"{len(ds)} active bot discussions")


@state_group.command("forget")
@click.argument("mr_url")
@click.pass_context
def state_forget(ctx, mr_url):
    cfg = ctx.obj["cfg"]
    ref = parse_mr_url(mr_url)
    proj_cfg = cfg.get_project(ref.project_path)
    if not proj_cfg:
        click.echo("project not in config", err=True); sys.exit(2)
    st = State(Path(cfg.paths.state_db))
    st.forget_mr(proj_cfg.id, ref.mr_iid)
    click.echo(f"forgot MR {ref.project_path}!{ref.mr_iid}")


@main.command("show-output")
@click.argument("json_path", type=click.Path(exists=True, path_type=Path))
@click.pass_context
def show_output(ctx, json_path: Path):
    data = _json.loads(json_path.read_text())
    click.echo(f"task_type: {data['task_type']}")
    click.echo(f"inline_comments: {len(data.get('inline_comments', []))}")
    for c in data.get("inline_comments", []):
        click.echo(f"  - {c['file']}:{c['line']} [{c['severity']}/{c['category']}] "
                   f"{c['body'][:80]}...")
    if data.get("summary"):
        click.echo("\nSUMMARY:")
        click.echo(data["summary"]["overall"])
    if data.get("thread_action"):
        click.echo(f"\nTHREAD ACTION: {data['thread_action']}")
