# claude-reviewer

Autonomous GitLab Merge Request reviewer powered by the [Claude CLI](https://docs.claude.com/claude-code) — polls MRs, posts inline review comments, and tracks state across runs.

## Overview

`claude-reviewer` is a self-hosted GitLab MR review bot driven by the `claude` CLI. It periodically polls configured GitLab projects for open merge requests, checks out the source branch into an isolated git worktree, and asks Claude to review the diff with awareness of the project's stack (Python, Swift/iOS, Kotlin/Android, TypeScript, Java E2E, …) and configurable ignore paths. Findings are posted as inline discussion comments on the MR via a dedicated bot user, with markers that let the bot recognize and skip its own past comments on subsequent runs.

## Features

- **MR polling daemon** — watches configured projects, respects per-project backoff and an `stale_mr_ignore_days` window.
- **Stack-aware reviews** — built-in ignore presets for `python-backend`, `swift-ios`, `kotlin-android`, `typescript-frontend`, `java-e2e`; project-level overrides merge on top.
- **Isolated worktrees** — blobless clones + `git worktree` per MR, with configurable fetch / worktree timeouts.
- **Inline comments** — diff-aware discussions posted under a bot account, capped by `max_inline_comments`.
- **Idempotent** — SQLite state DB plus comment markers prevent duplicate reviews and self-replies.
- **Cost & rate limits** — `daily_cost_limit_usd` and `hourly_tasks_per_project_limit` enforce safe operation.
- **Dry-run mode** — render reviews to logs without posting.
- **MCP review sink** — Claude streams findings through an MCP server for structured collection.

## Requirements

- Python ≥ 3.11
- The [`claude` CLI](https://docs.claude.com/claude-code) on `PATH`, authenticated (or `ANTHROPIC_API_KEY` exported)
- A GitLab account for the bot (see [Bot account setup](#bot-account-setup))
- Git ≥ 2.40 (for `git worktree` + partial clone)

## Install

```bash
git clone <this-repo>
cd gitlab-review-claude-agent
python -m venv .venv && source .venv/bin/activate
pip install -e '.[dev]'
```

## Configure

```bash
cp config.example.toml config.toml
cp .env.example .env
```

Edit `.env`:

```dotenv
GITLAB_TOKEN=<personal access token of the bot user, scope: api>
# ANTHROPIC_API_KEY=...   # if claude CLI isn't already authenticated
```

Edit `config.toml` — at minimum:

- `gitlab.base_url` — your GitLab instance URL
- `gitlab.bot_username` — must match the bot user's username exactly (default: `claude-reviewer`)
- `[[projects]]` — at least one entry with `id`, `path`, `stack`, `target_branches`
- `review.dry_run` — keep `true` until you're ready to post live

See `config.example.toml` for the full schema and stack presets.

## Bot account setup

1. As a GitLab admin, create a regular user with username **`claude-reviewer`** (or whatever you set `bot_username` to).
2. Add the bot as **Reporter** (read + comment) — or **Developer** if you want it to approve / merge — on every project listed in `config.toml`.
3. Sign in as the bot, generate a Personal Access Token with the **`api`** scope, and put it in `.env` as `GITLAB_TOKEN`.

For local testing under a personal PAT, validation downgrades the username mismatch to a warning rather than an error — but in production the bot must own its own account so its comments can be filtered out on re-runs.

## Usage

The package installs a `claude-reviewer` CLI:

```bash
# One-shot review of a single MR
claude-reviewer review https://gitlab.example.com/group/project/-/merge_requests/123

# Force live mode for a single invocation
claude-reviewer review <mr-url> --live

# Run the polling daemon
claude-reviewer daemon

# Pre-clone a project into the configured repo root
claude-reviewer clone group/subgroup/project

# Inspect cost spend
claude-reviewer cost --since 2026-04-01

# State management
claude-reviewer state list
claude-reviewer state forget <mr-url>

# Re-print Claude's raw output for the last review of an MR
claude-reviewer show-output <mr-url>
```

Pass `--config path/to/config.toml` to the top-level command to use a non-default config file.

## Project layout

```
src/claude_reviewer/
  cli.py             # click commands: review, daemon, cost, clone, state, …
  orchestrator.py    # per-MR review pipeline
  config.py          # pydantic settings + TOML loading
  diff_parser.py     # GitLab diff → reviewable hunks
  prompt_builder.py  # stack- and project-aware prompt assembly
  gitlab_poster.py   # inline discussion posting
  review_sink_mcp.py # MCP server Claude posts findings to
  markers.py         # bot-comment recognition
  state.py           # SQLite-backed run state
  metrics.py         # cost + run metrics
  validation.py      # token / binary preflight checks
tests/               # unit + integration tests
```

## Development

```bash
pytest                # full suite
pytest tests/unit     # fast unit tests
ruff check src tests  # lint
```


## License

TBD.
