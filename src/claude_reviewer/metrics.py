from __future__ import annotations
import json
import time
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path


@dataclass
class Metrics:
    path: Path
    started_at: float = 0.0
    poll_cycles: int = 0
    tasks_by_type: Counter = field(default_factory=Counter)
    tasks_by_status: Counter = field(default_factory=Counter)
    cost_today_usd: float = 0.0
    cost_day: str = ""
    last_poll_at: str | None = None
    last_error_at: str | None = None
    active_bot_discussions: int = 0

    def start(self) -> None:
        self.started_at = time.time()

    def record_task(self, kind: str, status: str) -> None:
        self.tasks_by_type[kind] += 1
        self.tasks_by_status[status] += 1

    def record_cost(self, usd: float) -> None:
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        if today != self.cost_day:
            self.cost_today_usd = 0.0
            self.cost_day = today
        self.cost_today_usd += usd

    def record_poll(self) -> None:
        self.poll_cycles += 1
        self.last_poll_at = datetime.now(timezone.utc).isoformat()

    def record_error(self) -> None:
        self.last_error_at = datetime.now(timezone.utc).isoformat()

    def write(self) -> None:
        data = {
            "uptime_seconds": int(time.time() - self.started_at) if self.started_at else 0,
            "poll_cycles": self.poll_cycles,
            "tasks_by_type": dict(self.tasks_by_type),
            "tasks_by_status": dict(self.tasks_by_status),
            "last_poll_at": self.last_poll_at,
            "last_error_at": self.last_error_at,
            "cost_today_usd": round(self.cost_today_usd, 4),
            "active_bot_discussions": self.active_bot_discussions,
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(data, indent=2))
