import subprocess
import uuid
from pathlib import Path
import pytest
from claude_reviewer.repo_manager import RepoManager, RepoOptions


def _init_source_repo(root: Path) -> Path:
    src = root / "source.git"
    src.mkdir()
    subprocess.run(["git", "init", "--bare", "-b", "main"], cwd=src, check=True)

    wc = root / "wc"
    wc.mkdir()
    subprocess.run(["git", "init", "-b", "main"], cwd=wc, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=wc, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=wc, check=True)
    subprocess.run(["git", "config", "commit.gpgsign", "false"], cwd=wc, check=True)
    (wc / "a.txt").write_text("hello\n")
    subprocess.run(["git", "add", "a.txt"], cwd=wc, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=wc, check=True)
    subprocess.run(["git", "remote", "add", "origin", str(src)], cwd=wc, check=True)
    subprocess.run(["git", "push", "origin", "main"], cwd=wc, check=True)
    return src


def test_clone_bare_and_worktree(tmp_path: Path):
    src = _init_source_repo(tmp_path)
    repo_root = tmp_path / "repos"
    wt_root = tmp_path / "wt"
    repo_root.mkdir(); wt_root.mkdir()

    rm = RepoManager(RepoOptions(
        repo_root=repo_root, worktree_root=wt_root,
        clone_mode="full",  # blobless on file:// is awkward, use full
        fetch_timeout_seconds=30,
        worktree_timeout_seconds=30,
    ))

    clone_url = f"file://{src}"
    rm.ensure_cloned("local/proj", clone_url)
    assert (repo_root / "local" / "proj" / "HEAD").exists()

    rm.fetch("local/proj")

    head_sha = subprocess.run(
        ["git", "-C", str(repo_root / "local" / "proj"),
         "rev-parse", "main"],
        capture_output=True, text=True, check=True,
    ).stdout.strip()

    with rm.worktree("local/proj", head_sha) as wt:
        assert (wt / "a.txt").read_text() == "hello\n"
    # worktree is cleaned up
    assert not (wt_root / "local_proj" ).exists() or \
           not any((wt_root).iterdir())


def test_ensure_cloned_is_idempotent_on_refspecs(tmp_path: Path):
    src = _init_source_repo(tmp_path)
    repo_root = tmp_path / "repos"
    wt_root = tmp_path / "wt"
    repo_root.mkdir(); wt_root.mkdir()

    rm = RepoManager(RepoOptions(
        repo_root=repo_root, worktree_root=wt_root,
        clone_mode="full",
        fetch_timeout_seconds=30, worktree_timeout_seconds=30,
    ))
    clone_url = f"file://{src}"
    rm.ensure_cloned("proj", clone_url)
    rm.ensure_cloned("proj", clone_url)  # second call, no-op clone

    refspecs = subprocess.run(
        ["git", "-C", str(repo_root / "proj"), "config",
         "--get-all", "remote.origin.fetch"],
        capture_output=True, text=True, check=True,
    ).stdout.strip().splitlines()
    assert len(refspecs) == 2
    assert any("refs/heads/*" in r for r in refspecs)
    assert any("refs/merge-requests" in r for r in refspecs)
