from pathlib import Path
from claude_reviewer.claude_runner import parse_usage_from_log


def test_parse_usage_from_typical_log(tmp_path: Path):
    log = tmp_path / "log.jsonl"
    log.write_text(
        "# claude args: claude -p ...\n"
        '{"type":"system","subtype":"init","model":"sonnet"}\n'
        '{"type":"assistant","message":{"content":[]}}\n'
        '{"type":"result","subtype":"success",'
        '"total_cost_usd":0.0234,'
        '"duration_ms":12345,'
        '"usage":{"input_tokens":100,"cache_creation_input_tokens":2000,'
        '"cache_read_input_tokens":50000,"output_tokens":456}}\n'
    )
    u = parse_usage_from_log(log)
    assert u is not None
    assert u.cost_usd == 0.0234
    assert u.input_tokens == 100
    assert u.cache_creation_tokens == 2000
    assert u.cache_read_tokens == 50000
    assert u.output_tokens == 456
    assert u.duration_ms == 12345


def test_parse_usage_returns_none_when_no_result_event(tmp_path: Path):
    log = tmp_path / "log.jsonl"
    log.write_text("# claude args: ...\nrandom text\n")
    assert parse_usage_from_log(log) is None


def test_parse_usage_returns_none_when_log_missing(tmp_path: Path):
    assert parse_usage_from_log(tmp_path / "nope.jsonl") is None
