import json
from pathlib import Path
from claude_reviewer.metrics import Metrics


def test_metrics_increment_and_write(tmp_path: Path):
    m = Metrics(tmp_path / "metrics.json")
    m.start()
    m.record_task("first_review", "posted")
    m.record_task("thread_reply", "failed")
    m.record_cost(0.25)
    m.write()
    data = json.loads((tmp_path / "metrics.json").read_text())
    assert data["tasks_by_type"] == {"first_review": 1, "thread_reply": 1}
    assert data["tasks_by_status"] == {"posted": 1, "failed": 1}
    assert data["cost_today_usd"] == 0.25
    assert "uptime_seconds" in data
