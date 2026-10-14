# ruff: noqa: E501  (SQL and one-line queries)
"""The demo's data for the API.

Read-only: the supplier submissions (data/synthetic) and what the pipeline made of them
(data/results: extraction, copy, audit, eval; rebuilt from the recorded model answers by app.prepare).
Read-write: people's decisions, published products and the event log, kept in SQLite under DATA_DIR so
they survive restarts. "Reset demo" clears them.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from functools import cached_property
from pathlib import Path
from typing import Any

from app.config import Settings
from app.pipeline.checks import check_submission
from app.pipeline.listing import verified_facts
from app.schemas.review import Decision, Issue

ROOT = Path(__file__).resolve().parents[2]
SCHEMA = """
CREATE TABLE IF NOT EXISTS decision (product_id TEXT, issue_id TEXT, action TEXT, value TEXT, note TEXT,
                                     decided_at TEXT, PRIMARY KEY (product_id, issue_id));
CREATE TABLE IF NOT EXISTS published (product_id TEXT PRIMARY KEY, published_at TEXT);
CREATE TABLE IF NOT EXISTS event (id INTEGER PRIMARY KEY AUTOINCREMENT, product_id TEXT, at TEXT, kind TEXT, detail TEXT);
"""


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


class Store:
    def __init__(self, settings: Settings, root: Path = ROOT):
        self.settings = settings
        self.synthetic, self.results = root / "data" / "synthetic", root / "data" / "results"
        Path(settings.data_dir).mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(Path(settings.data_dir) / "shelfready.db", check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)

    # ------------------------------------------------------------------ submissions and pipeline results
    @cached_property
    def submissions(self) -> dict[str, tuple[Path, dict[str, Any]]]:
        """product id → (folder, submission.json)."""
        found = {}
        for manifest in sorted(self.synthetic.glob("SYN-*/submission.json")):
            data = json.loads(manifest.read_text(encoding="utf-8"))
            found[data["product_id"]] = (manifest.parent, data)
        return found

    def result(self, pid: str) -> dict[str, Any] | None:
        path = self.results / "extraction" / f"{pid}.json"
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None

    def issues(self, pid: str) -> list[Issue]:
        result = self.result(pid)
        return check_submission(result) if result and result.get("spec") else []

    def copies(self, pid: str) -> list[dict[str, Any]]:
        """Recorded copies for this product (before review, and after the demo's planned decisions)."""
        folder = self.results / "copy"
        return [json.loads(p.read_text(encoding="utf-8")) for p in sorted(folder.glob(f"{pid}*.json"))]

    def copy_for(self, pid: str, facts: dict[str, Any]) -> dict[str, Any] | None:
        """The recorded copy written from exactly these facts, if any."""
        return next((c for c in self.copies(pid) if c.get("facts") == facts), None)

    def save_copy(self, pid: str, copy: dict[str, Any]) -> None:
        folder = self.results / "copy"
        folder.mkdir(parents=True, exist_ok=True)
        n = len(list(folder.glob(f"{pid}*.json")))
        (folder / f"{pid}.live{n}.json").write_text(
            json.dumps(copy, indent=1, ensure_ascii=False), encoding="utf-8"
        )

    def json_result(self, name: str) -> dict[str, Any] | None:
        path = self.results / name
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None

    # ------------------------------------------------------------------ people's decisions
    def decisions(self, pid: str) -> dict[str, Decision]:
        rows = self.db.execute("SELECT * FROM decision WHERE product_id = ?", (pid,)).fetchall()
        return {r["issue_id"]: Decision(action=r["action"], value=r["value"], note=r["note"]) for r in rows}

    def decided_at(self, pid: str) -> dict[str, str]:
        rows = self.db.execute("SELECT issue_id, decided_at FROM decision WHERE product_id = ?", (pid,))
        return {r["issue_id"]: r["decided_at"] for r in rows}

    def decide(self, pid: str, issue_id: str, decision: Decision) -> None:
        with self.db:
            self.db.execute("INSERT OR REPLACE INTO decision VALUES (?, ?, ?, ?, ?, ?)",
                            (pid, issue_id, decision.action, decision.value, decision.note, _now()))  # fmt: skip
        self.log(
            pid,
            "decision",
            f"{issue_id}: {decision.action}" + (f" ({decision.value})" if decision.value else ""),
        )

    def undo(self, pid: str, issue_id: str) -> None:
        with self.db:
            self.db.execute("DELETE FROM decision WHERE product_id = ? AND issue_id = ?", (pid, issue_id))
        self.log(pid, "reopened", issue_id)

    def facts(self, pid: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        result = self.result(pid)
        if not result or not result.get("spec"):
            return {}, []
        return verified_facts(result["spec"], self.issues(pid), self.decisions(pid))

    # ------------------------------------------------------------------ publishing and the event log
    def published_at(self, pid: str) -> str | None:
        row = self.db.execute("SELECT published_at FROM published WHERE product_id = ?", (pid,)).fetchone()
        return row["published_at"] if row else None

    def publish(self, pid: str) -> None:
        with self.db:
            self.db.execute("INSERT OR REPLACE INTO published VALUES (?, ?)", (pid, _now()))
        self.log(pid, "published", "")

    def log(self, pid: str, kind: str, detail: str) -> None:
        with self.db:
            self.db.execute(
                "INSERT INTO event (product_id, at, kind, detail) VALUES (?, ?, ?, ?)",
                (pid, _now(), kind, detail),
            )

    def events(self, pid: str) -> list[dict[str, Any]]:
        rows = self.db.execute("SELECT at, kind, detail FROM event WHERE product_id = ? ORDER BY id", (pid,))
        return [dict(r) for r in rows]

    def reset(self) -> None:
        with self.db:
            for table in ("decision", "published", "event"):
                self.db.execute(f"DELETE FROM {table}")  # noqa: S608 (fixed table names)
