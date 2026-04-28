import json
from pathlib import Path
import pytest
from claude_reviewer.claude_runner import (
    build_mcp_config, build_cli_args, ClaudeInvocation,
)


def test_build_mcp_config_points_at_module(tmp_path):
    import sys
    output = tmp_path / "out.json"
    cfg = build_mcp_config(
        module="claude_reviewer.review_sink_mcp",
        output_path=output,
        task_type="first_review",
    )
    assert "review_sink" in cfg["mcpServers"]
    server = cfg["mcpServers"]["review_sink"]
    assert server["command"] == sys.executable
    assert "claude_reviewer.review_sink_mcp" in server["args"][-1]
    assert server["env"]["REVIEW_SINK_OUTPUT"] == str(output)
    assert server["env"]["REVIEW_SINK_TASK"] == "first_review"


def test_build_mcp_config_passes_max_inline(tmp_path):
    output = tmp_path / "out.json"
    cfg = build_mcp_config(
        module="claude_reviewer.review_sink_mcp",
        output_path=output, task_type="first_review",
        max_inline_comments=15,
    )
    assert cfg["mcpServers"]["review_sink"]["env"]["REVIEW_SINK_MAX_INLINE"] == "15"


def test_build_cli_args_contains_required_flags(tmp_path):
    mcp_cfg_path = tmp_path / "mcp.json"
    args = build_cli_args(
        claude_binary="claude",
        prompt="the prompt",
        mcp_config_path=mcp_cfg_path,
        max_turns=40,
        allowed_tools=["Read", "Grep", "mcp__review_sink__*"],
    )
    assert args[0] == "claude"
    assert "-p" in args
    assert "--mcp-config" in args
    assert str(mcp_cfg_path) in args
    assert "--allowedTools" in args
    assert "--max-turns" in args
    assert "40" in args
    assert "--output-format" in args and "stream-json" in args
    assert "--verbose" in args
