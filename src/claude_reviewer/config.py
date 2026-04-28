from __future__ import annotations
from pathlib import Path
from typing import Literal
from pydantic import BaseModel, Field, ValidationError
import tomllib

from claude_reviewer.errors import ConfigError


class GitLabConfig(BaseModel):
    base_url: str
    bot_username: str


class PollConfig(BaseModel):
    interval_seconds: int = 60
    per_project_backoff_multiplier: int = 2
    stale_mr_ignore_days: int = 30


class ClaudeConfig(BaseModel):
    cli_binary: str = "claude"
    model: str = "sonnet"
    max_turns: int = 40
    timeout_seconds: int = 600


class RepoConfig(BaseModel):
    root: str = "./repos"
    clone_mode: Literal["blobless", "full"] = "blobless"
    worktree_root: str = "/tmp/claude-review"
    fetch_timeout_seconds: int = 60
    worktree_timeout_seconds: int = 120


class ReviewConfig(BaseModel):
    max_inline_comments: int = 10
    dry_run: bool = True
    language: str = "english"
    visible_tag_prefix: str = "**[Claude Review]**"
    daily_cost_limit_usd: float = 5.0
    hourly_tasks_per_project_limit: int = 20


class PathsConfig(BaseModel):
    state_db: str = "./state.db"
    logs_dir: str = "./logs"


class ProjectConfig(BaseModel):
    id: int
    path: str
    stack: str | None = None
    review_enabled: bool = True
    target_branches: list[str] = Field(default_factory=lambda: ["main"])
    ignore_paths: list[str] = Field(default_factory=list)


class StackConfig(BaseModel):
    ignore_paths: list[str] = Field(default_factory=list)


class Config(BaseModel):
    gitlab: GitLabConfig
    poll: PollConfig = Field(default_factory=PollConfig)
    claude: ClaudeConfig = Field(default_factory=ClaudeConfig)
    repo: RepoConfig = Field(default_factory=RepoConfig)
    review: ReviewConfig = Field(default_factory=ReviewConfig)
    paths: PathsConfig = Field(default_factory=PathsConfig)
    projects: list[ProjectConfig] = Field(default_factory=list)
    stacks: dict[str, StackConfig] = Field(default_factory=dict)

    def get_project(self, path: str) -> ProjectConfig | None:
        for p in self.projects:
            if p.path == path:
                return p
        return None

    def get_project_by_id(self, project_id: int) -> ProjectConfig | None:
        for p in self.projects:
            if p.id == project_id:
                return p
        return None

    def resolved_ignore_paths(self, project: ProjectConfig) -> list[str]:
        """Combine stack-level + project-level ignore globs."""
        stack_globs: list[str] = []
        if project.stack and project.stack in self.stacks:
            stack_globs = list(self.stacks[project.stack].ignore_paths)
        return stack_globs + list(project.ignore_paths)


def load_config(path: Path | str) -> Config:
    path = Path(path)
    if not path.exists():
        raise ConfigError(f"config file not found: {path}")
    try:
        with path.open("rb") as f:
            data = tomllib.load(f)
    except tomllib.TOMLDecodeError as e:
        raise ConfigError(f"invalid TOML in {path}: {e}") from e
    try:
        return Config.model_validate(data)
    except ValidationError as e:
        raise ConfigError(f"invalid config in {path}:\n{e}") from e
