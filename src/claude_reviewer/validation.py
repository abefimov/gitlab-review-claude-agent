from __future__ import annotations
import subprocess
from dataclasses import dataclass


@dataclass
class ValidationResult:
    ok: bool
    message: str


def check_claude_binary(binary: str) -> ValidationResult:
    try:
        res = subprocess.run([binary, "--version"],
                             capture_output=True, text=True, timeout=5)
    except FileNotFoundError:
        return ValidationResult(False, f"{binary} not found in PATH")
    except subprocess.TimeoutExpired:
        return ValidationResult(False, f"{binary} --version timed out")
    if res.returncode != 0:
        return ValidationResult(False, f"{binary} --version exited nonzero: {res.stderr}")
    return ValidationResult(True, res.stdout.strip())


def check_gitlab_token(gl, *, expected: str) -> ValidationResult:
    try:
        gl.auth()  # populates gl.user
        user = gl.user
    except Exception as e:
        return ValidationResult(False, f"GitLab auth failed: {e}")
    if user is None:
        return ValidationResult(False, "GitLab auth returned no user")
    if user.username != expected:
        return ValidationResult(
            True,
            f"warning: logged in as {user.username}, expected {expected} "
            f"(ok during testing under a personal PAT)",
        )
    return ValidationResult(True, f"ok as {user.username}")
