from __future__ import annotations
import contextlib
import shutil
import subprocess
import uuid
from dataclasses import dataclass
from pathlib import Path

from claude_reviewer.errors import RepoError


@dataclass(frozen=True)
class RepoOptions:
    repo_root: Path
    worktree_root: Path
    clone_mode: str = "blobless"
    fetch_timeout_seconds: int = 60
    worktree_timeout_seconds: int = 120


class RepoManager:
    def __init__(self, opts: RepoOptions):
        self.opts = opts
        opts.repo_root.mkdir(parents=True, exist_ok=True)
        opts.worktree_root.mkdir(parents=True, exist_ok=True)

    def bare_path(self, project_path: str) -> Path:
        return self.opts.repo_root / project_path

    def _run(self, cmd: list[str], *, cwd: Path | None = None, timeout: int):
        try:
            return subprocess.run(
                cmd, cwd=cwd, check=True,
                capture_output=True, text=True, timeout=timeout,
            )
        except subprocess.CalledProcessError as e:
            raise RepoError(
                f"git command failed: {' '.join(cmd)}\nstderr: {e.stderr}"
            ) from e
        except subprocess.TimeoutExpired as e:
            raise RepoError(f"git command timed out: {' '.join(cmd)}") from e

    def ensure_cloned(self, project_path: str, clone_url: str) -> Path:
        bare = self.bare_path(project_path)
        if not bare.exists():
            bare.parent.mkdir(parents=True, exist_ok=True)
            cmd = ["git", "clone", "--bare"]
            if self.opts.clone_mode == "blobless":
                cmd += ["--filter=blob:none"]
            cmd += [clone_url, str(bare)]
            self._run(cmd, timeout=self.opts.fetch_timeout_seconds * 5)

        # Always (re)apply fetch refspecs: safe whether this is a fresh clone
        # or a pre-existing bare repo without the refspecs configured.
        self._run(["git", "-C", str(bare), "config", "--replace-all",
                   "remote.origin.fetch",
                   "+refs/heads/*:refs/remotes/origin/*"],
                  timeout=5)
        self._run(["git", "-C", str(bare), "config", "--add",
                   "remote.origin.fetch",
                   "+refs/merge-requests/*/head:refs/remotes/origin/mr/*"],
                  timeout=5)
        return bare

    def fetch(self, project_path: str) -> None:
        bare = self.bare_path(project_path)
        if not bare.exists():
            raise RepoError(f"repo not cloned yet: {project_path}")
        self._run(["git", "-C", str(bare), "fetch", "--prune", "origin"],
                  timeout=self.opts.fetch_timeout_seconds)

    def fetch_mr_ref(self, project_path: str, mr_iid: int) -> None:
        """Fetch a specific MR head ref on-demand.

        The bulk `fetch --prune origin` occasionally misses individual
        `refs/merge-requests/<iid>/head` refs (e.g. fork-source MRs on some
        GitLab configurations). Use this as a targeted fallback.
        """
        bare = self.bare_path(project_path)
        if not bare.exists():
            raise RepoError(f"repo not cloned yet: {project_path}")
        refspec = (
            f"+refs/merge-requests/{mr_iid}/head:"
            f"refs/remotes/origin/mr/{mr_iid}"
        )
        self._run(
            ["git", "-C", str(bare), "fetch", "origin", refspec],
            timeout=self.opts.fetch_timeout_seconds,
        )

    @contextlib.contextmanager
    def worktree(self, project_path: str, sha: str):
        bare = self.bare_path(project_path)
        wt = self.opts.worktree_root / f"{uuid.uuid4().hex}"
        self._run(
            ["git", "-C", str(bare), "worktree", "add", "--force",
             str(wt), sha],
            timeout=self.opts.worktree_timeout_seconds,
        )
        try:
            yield wt
        finally:
            # Best-effort removal
            try:
                self._run(
                    ["git", "-C", str(bare), "worktree", "remove",
                     "--force", str(wt)],
                    timeout=30,
                )
            except RepoError:
                shutil.rmtree(wt, ignore_errors=True)

    def run_git(self, project_path: str, *args: str,
                cwd_worktree: Path | None = None, timeout: int = 30) -> str:
        """Run a read-only git command and return stdout."""
        cwd = cwd_worktree or self.bare_path(project_path)
        res = self._run(["git", "-C", str(cwd), *args], timeout=timeout)
        return res.stdout
