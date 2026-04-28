from __future__ import annotations
import re
from dataclasses import dataclass
from typing import Literal

MarkerKind = Literal["inline", "summary", "reply", "resolve"]

MARKER_RE = re.compile(r"<!--claude-review:(inline|summary|reply|resolve):(.+?)-->")


@dataclass(frozen=True)
class MarkerInfo:
    kind: MarkerKind
    key: str


def extract_marker(body: str) -> MarkerInfo | None:
    m = MARKER_RE.search(body or "")
    if not m:
        return None
    return MarkerInfo(kind=m.group(1), key=m.group(2))  # type: ignore[arg-type]


def render_body(
    *,
    kind: MarkerKind,
    text: str,
    marker_key: str,
    visible_prefix: str,
    severity: str | None = None,
    category: str | None = None,
) -> str:
    header_parts = [visible_prefix]
    if kind == "inline":
        if not (severity and category):
            raise ValueError("inline requires severity and category")
        header_parts += [category, severity]
    else:
        header_parts.append(kind)
    header = " · ".join(header_parts)
    marker = f"<!--claude-review:{kind}:{marker_key}-->"
    return f"{header}\n\n{text}\n\n{marker}"


def is_bot_note(note: dict, *, bot_username: str) -> bool:
    body = note.get("body") or ""
    if MARKER_RE.search(body):
        return True
    author = (note.get("author") or {}).get("username")
    if author == bot_username:
        return True
    return False
