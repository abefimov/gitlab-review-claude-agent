"""Filter unified-diff blocks whose target path matches an ignore glob."""
from __future__ import annotations
import fnmatch


def matches_any(path: str, globs: list[str]) -> bool:
    return any(fnmatch.fnmatch(path, g) for g in globs)


def _extract_b_path(diff_header: str) -> str | None:
    """From `diff --git a/X b/Y\\n` extract Y. Returns None if malformed."""
    parts = diff_header.strip().split(" ")
    if len(parts) < 4:
        return None
    b_part = parts[3]
    if b_part.startswith("b/"):
        return b_part[2:]
    return b_part


def filter_diff(diff_text: str, ignore_globs: list[str]) -> tuple[str, list[str]]:
    """Replace ignored file blocks with a one-line skip marker.

    Returns (filtered_diff, list_of_skipped_paths).
    """
    if not ignore_globs or not diff_text:
        return diff_text, []

    out: list[str] = []
    skipped: list[str] = []
    current_block: list[str] = []
    current_path: str | None = None

    def flush():
        nonlocal current_block, current_path
        if not current_block:
            return
        if current_path and matches_any(current_path, ignore_globs):
            line_count = sum(
                1 for ln in current_block
                if ln.startswith(("+", "-")) and not ln.startswith(("+++", "---"))
            )
            out.append(
                f"diff --git a/{current_path} b/{current_path}\n"
                f"... [skipped: {current_path} "
                f"({line_count} +/- lines, matched ignore rule)]\n"
            )
            skipped.append(current_path)
        else:
            out.extend(current_block)
        current_block = []
        current_path = None

    for line in diff_text.splitlines(keepends=True):
        if line.startswith("diff --git "):
            flush()
            current_path = _extract_b_path(line)
            current_block = [line]
        else:
            current_block.append(line)
    flush()
    return "".join(out), skipped


def filter_stat(stat_text: str, ignore_globs: list[str]) -> str:
    """Drop `--stat` rows whose path matches an ignore glob.

    Lines look like:  ` path/to/file.py | 12 ++++++++----`
    We match the path before the first `|`.
    """
    if not ignore_globs or not stat_text:
        return stat_text
    out: list[str] = []
    for line in stat_text.splitlines():
        if "|" in line:
            path = line.split("|", 1)[0].strip()
            if matches_any(path, ignore_globs):
                continue
        out.append(line)
    return "\n".join(out)
