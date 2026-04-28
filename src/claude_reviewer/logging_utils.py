from __future__ import annotations
import re

_TOKEN_RES = [
    re.compile(r"PRIVATE-TOKEN:\s*[^\s]+"),
    re.compile(r"oauth2:[a-zA-Z0-9_-]+@"),
    re.compile(r"(ANTHROPIC_API_KEY|GITLAB_TOKEN)=\S+"),
]


def redact(text: str) -> str:
    for r in _TOKEN_RES:
        text = r.sub("[REDACTED]", text)
    return text
