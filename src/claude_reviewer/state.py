from __future__ import annotations
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS project_state (
  project_id INTEGER PRIMARY KEY,
  last_check_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS mr_reviews (
  project_id INTEGER,
  mr_iid INTEGER,
  last_reviewed_head_sha TEXT,
  first_reviewed_at TEXT,
  last_reviewed_at TEXT,
  PRIMARY KEY (project_id, mr_iid)
);

CREATE TABLE IF NOT EXISTS bot_discussions (
  discussion_id TEXT PRIMARY KEY,
  project_id INTEGER NOT NULL,
  mr_iid INTEGER NOT NULL,
  file TEXT,
  line INTEGER,
  last_seen_note_id INTEGER NOT NULL,
  resolved INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS task_log (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  task_type TEXT NOT NULL,
  project_id INTEGER,
  mr_iid INTEGER,
  discussion_id TEXT,
  head_sha TEXT,
  status TEXT NOT NULL,
  started_at TEXT,
  finished_at TEXT,
  error TEXT,
  cost_usd REAL,
  input_tokens INTEGER,
  cache_creation_tokens INTEGER,
  cache_read_tokens INTEGER,
  output_tokens INTEGER
);
"""


@dataclass(frozen=True)
class BotDiscussion:
    discussion_id: str
    project_id: int
    mr_iid: int
    file: str | None
    line: int | None
    last_note_id: int
    resolved: bool


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class State:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self.conn.commit()
        # Migrate older DBs that don't yet have cost columns
        existing_cols = {
            row["name"]
            for row in self.conn.execute("PRAGMA table_info(task_log)")
        }
        for col, ddl in [
            ("cost_usd", "ALTER TABLE task_log ADD COLUMN cost_usd REAL"),
            ("input_tokens", "ALTER TABLE task_log ADD COLUMN input_tokens INTEGER"),
            ("cache_creation_tokens",
             "ALTER TABLE task_log ADD COLUMN cache_creation_tokens INTEGER"),
            ("cache_read_tokens",
             "ALTER TABLE task_log ADD COLUMN cache_read_tokens INTEGER"),
            ("output_tokens", "ALTER TABLE task_log ADD COLUMN output_tokens INTEGER"),
        ]:
            if col not in existing_cols:
                self.conn.execute(ddl)
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> "State":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    # --- project_state ---
    def get_last_check(self, project_id: int) -> datetime | None:
        row = self.conn.execute(
            "SELECT last_check_at FROM project_state WHERE project_id=?",
            (project_id,),
        ).fetchone()
        if not row:
            return None
        return datetime.fromisoformat(row["last_check_at"])

    def set_last_check(self, project_id: int, at: datetime) -> None:
        self.conn.execute(
            "INSERT INTO project_state(project_id, last_check_at) VALUES(?,?) "
            "ON CONFLICT(project_id) DO UPDATE SET last_check_at=excluded.last_check_at",
            (project_id, at.isoformat()),
        )
        self.conn.commit()

    # --- mr_reviews ---
    def get_reviewed_sha(self, project_id: int, mr_iid: int) -> str | None:
        row = self.conn.execute(
            "SELECT last_reviewed_head_sha FROM mr_reviews "
            "WHERE project_id=? AND mr_iid=?",
            (project_id, mr_iid),
        ).fetchone()
        return row["last_reviewed_head_sha"] if row else None

    def set_reviewed_sha(self, project_id: int, mr_iid: int,
                         head_sha: str, at: datetime) -> None:
        self.conn.execute(
            "INSERT INTO mr_reviews(project_id, mr_iid, last_reviewed_head_sha,"
            " first_reviewed_at, last_reviewed_at) VALUES(?,?,?,?,?) "
            "ON CONFLICT(project_id, mr_iid) DO UPDATE SET "
            " last_reviewed_head_sha=excluded.last_reviewed_head_sha,"
            " last_reviewed_at=excluded.last_reviewed_at",
            (project_id, mr_iid, head_sha, at.isoformat(), at.isoformat()),
        )
        self.conn.commit()

    def forget_mr(self, project_id: int, mr_iid: int) -> None:
        with self.conn:
            self.conn.execute(
                "DELETE FROM mr_reviews WHERE project_id=? AND mr_iid=?",
                (project_id, mr_iid),
            )
            self.conn.execute(
                "DELETE FROM bot_discussions WHERE project_id=? AND mr_iid=?",
                (project_id, mr_iid),
            )

    # --- bot_discussions ---
    def add_bot_discussion(self, *, discussion_id: str, project_id: int,
                           mr_iid: int, file: str | None, line: int | None,
                           last_note_id: int) -> None:
        self.conn.execute(
            "INSERT OR IGNORE INTO bot_discussions("
            "  discussion_id, project_id, mr_iid, file, line,"
            "  last_seen_note_id, resolved, created_at"
            ") VALUES(?,?,?,?,?,?,?,?)",
            (discussion_id, project_id, mr_iid, file, line,
             last_note_id, 0, _now()),
        )
        self.conn.commit()

    def update_last_note_id(self, discussion_id: str, note_id: int) -> None:
        self.conn.execute(
            "UPDATE bot_discussions SET last_seen_note_id=? "
            "WHERE discussion_id=?",
            (note_id, discussion_id),
        )
        self.conn.commit()

    def mark_discussion_resolved(self, discussion_id: str) -> None:
        self.conn.execute(
            "UPDATE bot_discussions SET resolved=1 WHERE discussion_id=?",
            (discussion_id,),
        )
        self.conn.commit()

    def mr_iid_for_discussion(self, discussion_id: str) -> int | None:
        row = self.conn.execute(
            "SELECT mr_iid FROM bot_discussions WHERE discussion_id=?",
            (discussion_id,),
        ).fetchone()
        return row["mr_iid"] if row else None

    def active_bot_discussions(self, project_id: int) -> list[BotDiscussion]:
        rows = self.conn.execute(
            "SELECT * FROM bot_discussions "
            "WHERE project_id=? AND resolved=0",
            (project_id,),
        ).fetchall()
        return [BotDiscussion(
            discussion_id=r["discussion_id"], project_id=r["project_id"],
            mr_iid=r["mr_iid"], file=r["file"], line=r["line"],
            last_note_id=r["last_seen_note_id"],
            resolved=bool(r["resolved"]),
        ) for r in rows]

    # --- task_log ---
    def log_task_started(self, task_type: str, *, project_id: int | None,
                         mr_iid: int | None, head_sha: str | None,
                         discussion_id: str | None) -> int:
        cur = self.conn.execute(
            "INSERT INTO task_log(task_type, project_id, mr_iid,"
            " discussion_id, head_sha, status, started_at)"
            " VALUES(?,?,?,?,?,?,?)",
            (task_type, project_id, mr_iid, discussion_id, head_sha,
             "started", _now()),
        )
        self.conn.commit()
        return cur.lastrowid

    def log_task_status(self, task_id: int, status: str,
                        error: str | None = None) -> None:
        finished = _now() if status in ("posted", "failed") else None
        self.conn.execute(
            "UPDATE task_log SET status=?, finished_at=?, error=? "
            "WHERE id=?",
            (status, finished, error, task_id),
        )
        self.conn.commit()

    def recent_tasks(self, limit: int = 50) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM task_log ORDER BY id DESC LIMIT ?", (limit,),
        ).fetchall()
        return [dict(r) for r in rows]

    def add_task_cost(self, task_id: int, cost_usd: float, usage) -> None:
        """Record cost+token usage for a completed task."""
        self.conn.execute(
            "UPDATE task_log SET cost_usd=?, input_tokens=?, "
            "cache_creation_tokens=?, cache_read_tokens=?, output_tokens=? "
            "WHERE id=?",
            (
                cost_usd,
                usage.input_tokens,
                usage.cache_creation_tokens,
                usage.cache_read_tokens,
                usage.output_tokens,
                task_id,
            ),
        )
        self.conn.commit()

    def cost_summary(self, since_iso: str | None = None) -> dict:
        """Aggregate cost by day and by task_type."""
        where = ""
        params: tuple = ()
        if since_iso:
            where = " WHERE started_at >= ?"
            params = (since_iso,)
        rows = self.conn.execute(
            "SELECT task_type, COUNT(*) AS n, "
            "  COALESCE(SUM(cost_usd), 0) AS cost, "
            "  COALESCE(SUM(input_tokens), 0) AS in_tok, "
            "  COALESCE(SUM(cache_creation_tokens), 0) AS cache_create_tok, "
            "  COALESCE(SUM(cache_read_tokens), 0) AS cache_read_tok, "
            "  COALESCE(SUM(output_tokens), 0) AS out_tok "
            f"FROM task_log{where} GROUP BY task_type",
            params,
        ).fetchall()
        return {
            row["task_type"]: {
                "n": row["n"],
                "cost_usd": row["cost"],
                "input_tokens": row["in_tok"],
                "cache_creation_tokens": row["cache_create_tok"],
                "cache_read_tokens": row["cache_read_tok"],
                "output_tokens": row["out_tok"],
            }
            for row in rows
        }
