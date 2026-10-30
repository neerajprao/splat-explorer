"""SQLite job store. One table, no ORM.

Job lifecycle:
    uploaded -> queued -> running -> done
                               \\-> failed / cancelled
A failed or cancelled job can be queued again ("retry"); stages that already
finished are skipped because each stage leaves a marker file in the job folder.
"""

from __future__ import annotations

import json
import sqlite3
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

STATUSES = ("uploaded", "queued", "running", "done", "failed", "cancelled")
STAGES = ("probe", "frames", "colmap", "train", "export")

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    status TEXT NOT NULL,
    stage TEXT,
    progress REAL NOT NULL DEFAULT 0,
    message TEXT,
    error TEXT,
    quality TEXT NOT NULL,
    video_filename TEXT,
    video_info TEXT,
    frames_total INTEGER,
    frames_registered INTEGER,
    splat_count INTEGER,
    warnings TEXT NOT NULL DEFAULT '[]',
    cancel_requested INTEGER NOT NULL DEFAULT 0,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    started_at REAL,
    finished_at REAL
);
CREATE INDEX IF NOT EXISTS jobs_status ON jobs(status, created_at);
"""

JSON_FIELDS = ("video_info", "warnings")


class JobStore:
    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._conn() as conn:
            conn.executescript(SCHEMA)

    @contextmanager
    def _conn(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.db_path, timeout=30, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        try:
            yield conn
        finally:
            conn.close()

    @staticmethod
    def _row(row: sqlite3.Row | None) -> dict[str, Any] | None:
        if row is None:
            return None
        job = dict(row)
        for key in JSON_FIELDS:
            if job.get(key):
                try:
                    job[key] = json.loads(job[key])
                except (TypeError, json.JSONDecodeError):
                    pass
        if job.get("warnings") in (None, ""):
            job["warnings"] = []
        job["cancel_requested"] = bool(job.get("cancel_requested"))
        return job

    def create(self, name: str, quality: str, video_filename: str | None = None,
               video_info: dict | None = None, status: str = "uploaded") -> dict[str, Any]:
        job_id = uuid.uuid4().hex[:12]
        now = time.time()
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO jobs (id, name, status, quality, video_filename, video_info,"
                " created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (job_id, name, status, quality, video_filename,
                 json.dumps(video_info) if video_info is not None else None, now, now),
            )
        return self.get(job_id)  # type: ignore[return-value]

    def get(self, job_id: str) -> dict[str, Any] | None:
        with self._conn() as conn:
            return self._row(conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone())

    def list(self, status: str | None = None) -> list[dict[str, Any]]:
        with self._conn() as conn:
            if status:
                rows = conn.execute(
                    "SELECT * FROM jobs WHERE status = ? ORDER BY created_at DESC", (status,))
            else:
                rows = conn.execute("SELECT * FROM jobs ORDER BY created_at DESC")
            return [self._row(r) for r in rows.fetchall()]  # type: ignore[misc]

    def update(self, job_id: str, **fields: Any) -> dict[str, Any] | None:
        if not fields:
            return self.get(job_id)
        for key in JSON_FIELDS:
            if key in fields and not isinstance(fields[key], (str, type(None))):
                fields[key] = json.dumps(fields[key])
        if "cancel_requested" in fields:
            fields["cancel_requested"] = 1 if fields["cancel_requested"] else 0
        fields["updated_at"] = time.time()
        cols = ", ".join(f"{k} = ?" for k in fields)
        with self._conn() as conn:
            conn.execute(f"UPDATE jobs SET {cols} WHERE id = ?", (*fields.values(), job_id))
        return self.get(job_id)

    def add_warning(self, job_id: str, warning: str) -> None:
        job = self.get(job_id)
        if job is None:
            return
        warnings = list(job.get("warnings") or [])
        if warning not in warnings:
            warnings.append(warning)
            self.update(job_id, warnings=warnings)

    def delete(self, job_id: str) -> None:
        with self._conn() as conn:
            conn.execute("DELETE FROM jobs WHERE id = ?", (job_id,))

    def enqueue(self, job_id: str, quality: str | None = None) -> dict[str, Any] | None:
        fields: dict[str, Any] = dict(status="queued", error=None, message="Waiting for worker",
                                      cancel_requested=False, progress=0.0, finished_at=None)
        if quality:
            fields["quality"] = quality
        return self.update(job_id, **fields)

    def claim_next(self) -> dict[str, Any] | None:
        """Atomically move the oldest queued job to running and return it."""
        with self._conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                row = conn.execute(
                    "SELECT id FROM jobs WHERE status = 'queued' ORDER BY created_at LIMIT 1"
                ).fetchone()
                if row is None:
                    conn.execute("COMMIT")
                    return None
                now = time.time()
                conn.execute(
                    "UPDATE jobs SET status = 'running', started_at = ?, updated_at = ?,"
                    " message = 'Starting' WHERE id = ?",
                    (now, now, row["id"]),
                )
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise
        return self.get(row["id"])

    def requeue_interrupted(self) -> int:
        """Jobs left 'running' by a crashed/stopped worker go back to the queue."""
        with self._conn() as conn:
            cur = conn.execute(
                "UPDATE jobs SET status = 'queued', message = 'Resumed after worker restart',"
                " updated_at = ? WHERE status = 'running'",
                (time.time(),),
            )
            return cur.rowcount
