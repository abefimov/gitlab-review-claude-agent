from __future__ import annotations
from dataclasses import dataclass
from typing import Literal
from pydantic import BaseModel, Field

TaskKind = Literal["first_review", "incremental_review", "thread_reply"]
Severity = Literal["blocker", "major", "minor"]
Category = Literal["bug", "security", "design", "tests"]


class InlineComment(BaseModel):
    file: str
    line: int = Field(gt=0)
    severity: Severity
    category: Category
    body: str = Field(min_length=20, max_length=2000)


class ReviewSummary(BaseModel):
    overall: str = Field(min_length=20, max_length=3000)
    performance_notes: str | None = Field(default=None, max_length=1500)


class ThreadAction(BaseModel):
    action: Literal["reply", "resolve"]
    discussion_id: str
    body: str | None = Field(default=None, max_length=2000)


class ReviewOutput(BaseModel):
    task_type: TaskKind
    inline_comments: list[InlineComment] = Field(default_factory=list)
    summary: ReviewSummary | None = None
    thread_action: ThreadAction | None = None
    approval: bool = False


@dataclass(frozen=True)
class FirstReviewTask:
    project_id: int
    mr_iid: int

@dataclass(frozen=True)
class IncrementalReviewTask:
    project_id: int
    mr_iid: int
    old_head_sha: str
    new_head_sha: str

@dataclass(frozen=True)
class ThreadReplyTask:
    project_id: int
    mr_iid: int
    discussion_id: str
    last_note_id: int

Task = FirstReviewTask | IncrementalReviewTask | ThreadReplyTask


@dataclass(frozen=True)
class MRRefs:
    """Diff refs returned by GitLab API for building position payload."""
    base_sha: str
    start_sha: str
    head_sha: str
