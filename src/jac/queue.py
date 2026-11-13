"""SQLite queue of jobs. The job id is the primary key, so processing a mail twice is harmless."""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

STATUSES = ("new", "prepared", "opened", "applied", "skipped")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    job_id TEXT NOT NULL,
    source TEXT NOT NULL,
    url TEXT NOT NULL,
    title TEXT NOT NULL DEFAULT '',
    company TEXT NOT NULL DEFAULT '',
    location TEXT NOT NULL DEFAULT '',
    verdict TEXT NOT NULL,
    reasons TEXT NOT NULL DEFAULT '[]',
    cv_path TEXT NOT NULL DEFAULT '',
    cv_html_path TEXT NOT NULL DEFAULT '',
    cover_path TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL,
    mail_id TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (source, job_id)
);
CREATE TABLE IF NOT EXISTS quarantine (
    mail_id TEXT PRIMARY KEY,
    sender TEXT NOT NULL,
    reasons TEXT NOT NULL,
    created_at TEXT NOT NULL
);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class Job:
    job_id: str
    source: str
    url: str
    verdict: str
    status: str
    title: str = ""
    company: str = ""
    location: str = ""
    reasons: list[str] = field(default_factory=list)
    cv_path: str = ""
    cv_html_path: str = ""
    cover_path: str = ""
    mail_id: str = ""
    created_at: str = ""
    updated_at: str = ""


class Queue:
    """Thin wrapper around the SQLite file."""

    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(path)
        self._db.row_factory = sqlite3.Row
        self._db.executescript(_SCHEMA)

    def close(self) -> None:
        self._db.close()

    def has(self, source: str, job_id: str) -> bool:
        row = self._db.execute("SELECT 1 FROM jobs WHERE source=? AND job_id=?", (source, job_id)).fetchone()
        return row is not None

    def add(self, job: Job) -> bool:
        """Insert a job. Returns False (and changes nothing) if it is already queued."""
        now = _now()
        cursor = self._db.execute(
            "INSERT OR IGNORE INTO jobs (job_id, source, url, title, company, location, verdict, reasons, cv_path, "
            "cv_html_path, cover_path, status, mail_id, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (job.job_id, job.source, job.url, job.title, job.company, job.location, job.verdict,
             json.dumps(job.reasons), job.cv_path, job.cv_html_path, job.cover_path, job.status, job.mail_id, now, now),
        )
        self._db.commit()
        return cursor.rowcount == 1

    def discard(self, job_id: str, source: str) -> None:
        """Remove a job so it can be processed again (used for 'open manually' jobs)."""
        self._db.execute("DELETE FROM jobs WHERE source=? AND job_id=?", (source, job_id))
        self._db.commit()

    def _job(self, row: sqlite3.Row) -> Job:
        data = dict(row)
        data["reasons"] = json.loads(data["reasons"])
        return Job(**data)

    def get(self, job_id: str, source: str = "linkedin") -> Job | None:
        row = self._db.execute("SELECT * FROM jobs WHERE source=? AND job_id=?", (source, job_id)).fetchone()
        return self._job(row) if row else None

    def list(self, status: str | None = None) -> list[Job]:
        query, args = "SELECT * FROM jobs", ()
        if status:
            query, args = query + " WHERE status=?", (status,)
        rows = self._db.execute(query + " ORDER BY created_at, job_id", args).fetchall()
        return [self._job(r) for r in rows]

    def set_status(self, job_id: str, status: str, source: str = "linkedin") -> bool:
        if status not in STATUSES:
            raise ValueError(f"status must be one of {STATUSES}")
        cursor = self._db.execute(
            "UPDATE jobs SET status=?, updated_at=? WHERE source=? AND job_id=?", (status, _now(), source, job_id)
        )
        self._db.commit()
        return cursor.rowcount == 1

    def quarantine(self, mail_id: str, sender: str, reasons: list[str]) -> None:
        self._db.execute(
            "INSERT OR REPLACE INTO quarantine (mail_id, sender, reasons, created_at) VALUES (?,?,?,?)",
            (mail_id, sender, json.dumps(reasons), _now()),
        )
        self._db.commit()

    def quarantined(self) -> list[tuple[str, str, list[str]]]:
        rows = self._db.execute("SELECT mail_id, sender, reasons FROM quarantine ORDER BY created_at, mail_id").fetchall()
        return [(r["mail_id"], r["sender"], json.loads(r["reasons"])) for r in rows]
