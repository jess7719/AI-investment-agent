"""Private SQLite state, report versions, and durable single-submission jobs."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4


def digest(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


class WorkspaceStore:
    def __init__(self, root: Path):
        self.root = root
        self.path = root / "profiles" / "workspace.sqlite3"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as db:
            db.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS preparations
                    (id TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS jobs
                    (id TEXT PRIMARY KEY, fingerprint TEXT UNIQUE NOT NULL,
                     status TEXT NOT NULL, value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS reports
                    (id TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS notes
                    (id TEXT PRIMARY KEY, report_id TEXT NOT NULL, value TEXT NOT NULL);
            """)
        os.chmod(self.path, 0o600)

    @contextmanager
    def connection(self):
        db = sqlite3.connect(self.path, timeout=15)
        db.row_factory = sqlite3.Row
        try:
            yield db
            db.commit()
        finally:
            db.close()

    def get(self, key: str, default=None):
        with self.connection() as db:
            row = db.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else default

    def set(self, key: str, value):
        with self.connection() as db:
            db.execute("INSERT OR REPLACE INTO settings VALUES (?,?)", (key, json.dumps(value)))

    def put(self, table: str, value: dict):
        assert table in {"preparations", "reports"}
        with self.connection() as db:
            db.execute(f"INSERT OR REPLACE INTO {table} VALUES (?,?)",
                       (value["id"], json.dumps(value)))

    def read(self, table: str, identity: str):
        assert table in {"preparations", "jobs", "reports", "notes"}
        with self.connection() as db:
            row = db.execute(f"SELECT value FROM {table} WHERE id=?", (identity,)).fetchone()
        return json.loads(row[0]) if row else None

    def all(self, table: str):
        assert table in {"jobs", "reports", "notes"}
        with self.connection() as db:
            rows = db.execute(f"SELECT value FROM {table} ORDER BY rowid DESC").fetchall()
        return [json.loads(row[0]) for row in rows]

    def claim(self, preparation: dict) -> tuple[dict, bool]:
        """A unique digest prevents races, reloads and repeated clicks from paying twice."""
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT value FROM jobs WHERE fingerprint=?",
                             (preparation["fingerprint"],)).fetchone()
            if row:
                return json.loads(row[0]), False
            job = {"id": uuid4().hex, "preparation_id": preparation["id"],
                   "fingerprint": preparation["fingerprint"], "ticker": preparation["ticker"],
                   "status": "queued", "created_at": now(), "message": "Waiting to generate."}
            db.execute("INSERT INTO jobs VALUES (?,?,?,?)",
                       (job["id"], job["fingerprint"], job["status"], json.dumps(job)))
            return job, True

    def update_job(self, job: dict, **changes):
        job = {**job, **changes, "updated_at": now()}
        with self.connection() as db:
            db.execute("UPDATE jobs SET status=?, value=? WHERE id=?",
                       (job["status"], json.dumps(job), job["id"]))
        return job

    def recover_jobs(self):
        for job in self.all("jobs"):
            if job["status"] in {"queued", "running"}:
                self.update_job(job, status="interrupted", message=(
                    "The process stopped. Billing may be unknown. No automatic retry was made. "
                    "Check API usage and retained report artifacts before preparing again."))

    def save_note(self, report_id: str, section: str, text: str, included: bool):
        identity = digest([report_id, section])
        note = {"id": identity, "report_id": report_id, "section": section,
                "text": text, "included": included, "updated_at": now()}
        with self.connection() as db:
            db.execute("INSERT OR REPLACE INTO notes VALUES (?,?,?)",
                       (identity, report_id, json.dumps(note)))
        return note

    def import_reports(self):
        """Keep legacy Markdown readable before onboarding; immutable copies live in SQLite."""
        known = {(r.get("source_path"), digest(r["markdown"])) for r in self.all("reports")}
        for path in sorted((self.root / "reports").glob("*/*.md")):
            content = path.read_text(encoding="utf-8", errors="replace")
            relative = str(path.relative_to(self.root))
            if (relative, digest(content)) in known:
                continue
            identity = digest([relative, content])
            if self.read("reports", identity):
                continue
            ticker = path.stem.removesuffix("-deep").removesuffix("-news")
            self.put("reports", {"id": identity, "ticker": ticker,
                     "title": path.stem, "created_at": path.parent.name,
                     "markdown": content, "preferences": {}, "source": "CLI",
                     "source_path": relative})
