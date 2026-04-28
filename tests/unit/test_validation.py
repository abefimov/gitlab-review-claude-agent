import subprocess
from types import SimpleNamespace
import pytest
from claude_reviewer.validation import (
    check_claude_binary, check_gitlab_token, ValidationResult,
)


def test_check_claude_binary_ok(monkeypatch):
    def fake_run(*a, **kw):
        return subprocess.CompletedProcess(a, 0, stdout="claude 0.2\n", stderr="")
    monkeypatch.setattr(subprocess, "run", fake_run)
    r = check_claude_binary("claude")
    assert r.ok


def test_check_claude_binary_missing(monkeypatch):
    def fake_run(*a, **kw):
        raise FileNotFoundError
    monkeypatch.setattr(subprocess, "run", fake_run)
    r = check_claude_binary("claude")
    assert not r.ok
    assert "not found" in r.message.lower()


def _gl_with(username):
    # Mimic python-gitlab: gl.auth() populates gl.user
    class _GL:
        def __init__(self):
            self.user = None
        def auth(self):
            self.user = SimpleNamespace(username=username)
    return _GL()


def test_check_gitlab_token_matches_expected_bot():
    gl = _gl_with("claude-reviewer")
    r = check_gitlab_token(gl, expected="claude-reviewer")
    assert r.ok


def test_check_gitlab_token_wrong_user_is_warning():
    gl = _gl_with("mshuram")
    r = check_gitlab_token(gl, expected="claude-reviewer")
    assert r.ok  # warning, not failure
    assert "warning" in r.message.lower() or "expected" in r.message.lower()
