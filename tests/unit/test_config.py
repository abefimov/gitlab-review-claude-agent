from pathlib import Path
import pytest
from claude_reviewer.config import load_config, Config
from claude_reviewer.errors import ConfigError


def test_load_valid_config(fixtures_dir: Path):
    cfg = load_config(fixtures_dir / "config_valid.toml")
    assert isinstance(cfg, Config)
    assert cfg.gitlab.base_url == "https://gitlab.example.com"
    assert cfg.gitlab.bot_username == "claude-reviewer"
    assert cfg.poll.interval_seconds == 60
    assert cfg.review.dry_run is True
    assert cfg.review.visible_tag_prefix == "**[Claude Review]**"
    assert len(cfg.projects) == 1
    assert cfg.projects[0].path == "example/mobile/sample-ios"
    assert cfg.projects[0].stack == "swift-ios"
    assert cfg.projects[0].target_branches == ["develop", "main"]


def test_missing_file_raises(tmp_path: Path):
    with pytest.raises(ConfigError, match="not found"):
        load_config(tmp_path / "nonexistent.toml")


def test_invalid_clone_mode(tmp_path: Path, fixtures_dir: Path):
    bad = (fixtures_dir / "config_valid.toml").read_text().replace(
        'clone_mode = "blobless"', 'clone_mode = "invalid"'
    )
    (tmp_path / "bad.toml").write_text(bad)
    with pytest.raises(ConfigError):
        load_config(tmp_path / "bad.toml")


def test_get_project_by_path(fixtures_dir: Path):
    cfg = load_config(fixtures_dir / "config_valid.toml")
    assert cfg.get_project("example/mobile/sample-ios").id == 42
    assert cfg.get_project("nonexistent/path") is None


def test_invalid_toml_raises(tmp_path: Path):
    bad = tmp_path / "broken.toml"
    bad.write_text("this = = not valid\n")
    with pytest.raises(ConfigError, match="invalid TOML"):
        load_config(bad)


def test_get_project_by_id(fixtures_dir: Path):
    cfg = load_config(fixtures_dir / "config_valid.toml")
    assert cfg.get_project_by_id(42).path == "example/mobile/sample-ios"
    assert cfg.get_project_by_id(999) is None


def test_load_config_accepts_str_path(fixtures_dir: Path):
    cfg = load_config(str(fixtures_dir / "config_valid.toml"))
    assert cfg.gitlab.base_url == "https://gitlab.example.com"
