"""Minimal unified-diff parser to identify which (file, line) pairs are
addressable via GitLab's inline-comment API, and how to address them.

GitLab's inline-comment position API is picky:
- For an added ('+') line, sending {new_path, new_line} is enough — GitLab
  computes line_code from those.
- For a context (' ') line, the line exists in both sides of the diff, so
  GitLab requires {old_path, old_line, new_path, new_line}; without the
  old_* it returns "line_code can't be blank, must be a valid line code".
- A deleted ('-') line is addressed by old_line only and is not currently
  exposed (we don't let the model comment on removed code).

`parse_addressable_lines` returns a mapping (file, new_line) -> LineInfo
so callers can both check whether a line is addressable AND know what the
position payload should contain.
"""
from __future__ import annotations
import re
from dataclasses import dataclass
from typing import Literal

_HUNK_RE = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")
_NEW_FILE_RE = re.compile(r"^\+\+\+ b/(.+)$")
_OLD_FILE_RE = re.compile(r"^--- a/(.+)$")
_FILE_BOUNDARY_RE = re.compile(r"^diff --git ")


@dataclass(frozen=True)
class LineInfo:
    kind: Literal["add", "context"]
    old_path: str | None
    old_line: int | None  # None for added lines (no counterpart in old file)


def parse_addressable_lines(diff_text: str) -> dict[tuple[str, int], LineInfo]:
    """Map (new_path, new_line) -> LineInfo for every line GitLab will accept
    as the target of an inline comment ('+' added or ' ' context lines)."""
    addressable: dict[tuple[str, int], LineInfo] = {}
    current_new: str | None = None
    current_old: str | None = None
    new_line_no: int | None = None
    old_line_no: int | None = None

    for raw in diff_text.splitlines():
        # File boundary — reset both sides. _OLD_FILE_RE only matches '--- a/',
        # so for newly-added files (git emits '--- /dev/null') current_old
        # would otherwise carry the previous file's path. Reset at 'diff --git'
        # before any --- / +++ line of the next file is parsed.
        if _FILE_BOUNDARY_RE.match(raw):
            current_old = None
            current_new = None
            new_line_no = None
            old_line_no = None
            continue
        m = _OLD_FILE_RE.match(raw)
        if m:
            current_old = m.group(1)
            continue
        m = _NEW_FILE_RE.match(raw)
        if m:
            current_new = m.group(1)
            new_line_no = None
            old_line_no = None
            continue
        m = _HUNK_RE.match(raw)
        if m:
            old_line_no = int(m.group(1))
            new_line_no = int(m.group(3))
            continue
        if current_new is None or new_line_no is None or old_line_no is None:
            continue
        if raw.startswith("+++") or raw.startswith("---"):
            continue
        if raw.startswith("\\"):  # \ No newline at end of file
            continue
        if not raw:
            # blank line is treated as context inside a hunk
            addressable[(current_new, new_line_no)] = LineInfo(
                kind="context", old_path=current_old, old_line=old_line_no,
            )
            new_line_no += 1
            old_line_no += 1
            continue
        prefix = raw[0]
        if prefix == "+":
            addressable[(current_new, new_line_no)] = LineInfo(
                kind="add", old_path=None, old_line=None,
            )
            new_line_no += 1
        elif prefix == "-":
            old_line_no += 1
            continue
        elif prefix == " ":
            addressable[(current_new, new_line_no)] = LineInfo(
                kind="context", old_path=current_old, old_line=old_line_no,
            )
            new_line_no += 1
            old_line_no += 1
        else:
            continue
    return addressable
