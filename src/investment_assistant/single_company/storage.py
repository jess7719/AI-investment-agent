"""Versioned SQLite datasets, immutable snapshots, job lease and consistent backups."""

from contextlib import contextmanager
from datetime import datetime, timezone
import gzip
import hashlib
import json
from pathlib import Path
import sqlite3
import uuid


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def encode(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


SCHEMA = """
CREATE TABLE IF NOT EXISTS schema_migrations(version INTEGER PRIMARY KEY);
INSERT OR IGNORE INTO schema_migrations VALUES(1);
CREATE TABLE IF NOT EXISTS securities(ticker TEXT PRIMARY KEY, identity_json TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS raw_fetches(
 id INTEGER PRIMARY KEY, provider TEXT, redacted_request TEXT, fetched_at TEXT,
 response_hash TEXT, permitted_raw_path TEXT, status TEXT);
CREATE TABLE IF NOT EXISTS datasets(
 id INTEGER PRIMARY KEY, kind TEXT, symbol TEXT, mode TEXT, version TEXT,
 fetch_id INTEGER, fetched_at TEXT, payload_json TEXT,
 UNIQUE(kind,symbol,mode,version));
CREATE TABLE IF NOT EXISTS price_bars(
 security_id TEXT, session_date TEXT, adjustment_mode TEXT, dataset_version TEXT,
 open REAL, high REAL, low REAL, close REAL, volume REAL, fetch_id INTEGER,
 PRIMARY KEY(security_id,session_date,adjustment_mode,dataset_version));
CREATE TABLE IF NOT EXISTS filings(
 cik TEXT, accession TEXT PRIMARY KEY, form TEXT, accepted_at TEXT, filed_date TEXT,
 report_date TEXT, source_url TEXT, fetch_id INTEGER);
CREATE TABLE IF NOT EXISTS financial_facts(
 cik TEXT, taxonomy TEXT, tag TEXT, unit TEXT, period_start TEXT, period_end TEXT,
 value REAL, accession TEXT, filed_date TEXT, context_json TEXT, fetch_id INTEGER,
 PRIMARY KEY(cik,taxonomy,tag,unit,context_json));
CREATE TABLE IF NOT EXISTS macro_observations(
 series_id TEXT, observation_date TEXT, vintage_start TEXT, vintage_end TEXT,
 value REAL, fetch_id INTEGER,
 PRIMARY KEY(series_id,observation_date,vintage_start,vintage_end));
CREATE TABLE IF NOT EXISTS analysis_snapshots(
 id INTEGER PRIMARY KEY, security_id TEXT, as_of TEXT, created_at TEXT,
 input_versions TEXT, feature_version TEXT, model_version TEXT,
 structured_report_json TEXT, data_status TEXT, fingerprint TEXT UNIQUE);
CREATE TABLE IF NOT EXISTS job_runs(
 id TEXT PRIMARY KEY, job TEXT, started_at TEXT, finished_at TEXT, status TEXT,
 request_count INTEGER DEFAULT 0, row_count INTEGER DEFAULT 0, safe_error TEXT);
CREATE TABLE IF NOT EXISTS job_leases(name TEXT PRIMARY KEY, owner TEXT, expires REAL);
CREATE TABLE IF NOT EXISTS forecast_evaluations(
 id TEXT PRIMARY KEY, created_at TEXT, dataset_versions TEXT, report_json TEXT,
 artifact_json TEXT);
CREATE TABLE IF NOT EXISTS forecast_results(
 id TEXT PRIMARY KEY, evaluation_id TEXT, origin_time TEXT, target_start TEXT,
 target_end TEXT, prediction REAL, lower_bound REAL, upper_bound REAL, realized REAL);
CREATE TABLE IF NOT EXISTS forecast_models(
 id TEXT PRIMARY KEY, evaluation_id TEXT, month TEXT, training_cutoff TEXT,
 artifact_json TEXT, dataset_versions TEXT, UNIQUE(evaluation_id,month));
INSERT OR IGNORE INTO schema_migrations VALUES(2);
"""


class BusyJob(RuntimeError):
    pass


class CompanyStore:
    def __init__(self, root):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.path = self.root / "company.sqlite3"
        with self.connect() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript(SCHEMA)
        from .directory import migrate
        migrate(self)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=15)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        try:
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    @contextmanager
    def session(self):
        # An idle connection keeps WAL files open while the web app serves requests.
        # No transaction is held; each request still owns its own connection.
        with self.connect():
            yield

    @contextmanager
    def lease(self, name="company-writer", seconds=3600):
        import time
        owner = uuid.uuid4().hex
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("DELETE FROM job_leases WHERE expires < ?", (time.time(),))
            try:
                db.execute("INSERT INTO job_leases VALUES(?,?,?)",
                           (name, owner, time.time() + seconds))
            except sqlite3.IntegrityError:
                raise BusyJob("A company job is already running.") from None
        try:
            self.lease_owner = owner
            yield owner
        finally:
            with self.connect() as db:
                db.execute("DELETE FROM job_leases WHERE name=? AND owner=?", (name, owner))

    def renew_lease(self):
        import time
        with self.connect() as db:
            updated = db.execute("UPDATE job_leases SET expires=? WHERE owner=? AND expires>?",
                                 (time.time() + 3600, getattr(self, "lease_owner", ""), time.time()))
            if updated.rowcount != 1:
                raise BusyJob("Company writer lease was lost.")

    def start_job(self, job, security_id=None):
        identity = uuid.uuid4().hex
        with self.connect() as db:
            db.execute("INSERT INTO job_runs(id,job,started_at,status,security_id) VALUES(?,?,?,?,?)",
                       (identity, job, utc_now(), "running", security_id))
        return identity

    def finish_job(self, identity, status, requests=0, rows=0, error=None):
        with self.connect() as db:
            db.execute("UPDATE job_runs SET finished_at=?,status=?,request_count=?,"
                       "row_count=?,safe_error=? WHERE id=?",
                       (utc_now(), status, requests, rows, error, identity))

    def latest_dataset(self, kind, symbol, mode=""):
        symbol = self.dataset_key(kind, symbol)
        with self.connect() as db:
            row = db.execute("SELECT * FROM datasets WHERE kind=? AND symbol=? AND mode=? "
                             "ORDER BY id DESC LIMIT 1", (kind, symbol, mode)).fetchone()
        if not row:
            return None
        result = dict(row)
        result["payload"] = json.loads(result.pop("payload_json"))
        return result

    def record_fetch(self, provider, request, payload, retain=False, status="ok"):
        # Callers supply only allowlisted request fields. Defense in depth for credentials.
        safe = {k: v for k, v in request.items()
                if k.lower() not in {"apikey", "api_key", "authorization", "token"}}
        content = encode(payload).encode()
        digest = hashlib.sha256(content).hexdigest()
        raw_path = None
        if retain and status == "ok":
            path = self.root / "raw" / utc_now()[:10] / f"{digest}.json.gz"
            path.parent.mkdir(parents=True, exist_ok=True)
            with gzip.open(path, "wb") as file:
                file.write(content)
            raw_path = str(path.relative_to(self.root))
        with self.connect() as db:
            cursor = db.execute("INSERT INTO raw_fetches(provider,redacted_request,fetched_at,"
                                "response_hash,permitted_raw_path,status) VALUES(?,?,?,?,?,?)",
                                (provider, encode(safe), utc_now(), digest, raw_path, status))
            return cursor.lastrowid

    def save_dataset(self, kind, symbol, payload, fetch_id, mode=""):
        symbol = self.dataset_key(kind, symbol)
        content = encode(payload)
        version = hashlib.sha256(content.encode()).hexdigest()
        previous = self.latest_dataset(kind, symbol, mode)
        if previous and previous["version"] == version:
            # Advance freshness without rewriting values or lineage.
            with self.connect() as db:
                db.execute("UPDATE datasets SET fetched_at=?,fetch_id=? WHERE id=?",
                           (utc_now(), fetch_id, previous["id"]))
            return version
        with self.connect() as db:
            # A correction may restore an earlier version; its position must become latest.
            db.execute("DELETE FROM datasets WHERE kind=? AND symbol=? AND mode=? AND version=?",
                       (kind, symbol, mode, version))
            db.execute("INSERT INTO datasets(kind,symbol,mode,version,fetch_id,fetched_at,"
                       "payload_json) VALUES(?,?,?,?,?,?,?)",
                       (kind, symbol, mode, version, fetch_id, utc_now(), content))
            if kind == "prices":
                db.executemany("INSERT OR IGNORE INTO price_bars VALUES(?,?,?,?,?,?,?,?,?,?)", [
                    (symbol, b["date"], mode, version, b["open"], b["high"], b["low"],
                     b["close"], b["volume"], fetch_id) for b in payload])
            elif kind == "filings":
                db.executemany("INSERT OR IGNORE INTO filings VALUES(?,?,?,?,?,?,?,?)", [
                    (symbol, f["accession"], f["form"], f["accepted_at"], f["filed_date"],
                     f["report_date"], f["url"], fetch_id) for f in payload["recent"]])
            elif kind == "facts":
                db.executemany("INSERT OR IGNORE INTO financial_facts VALUES(?,?,?,?,?,?,?,?,?,?,?)", [
                    (symbol, f["taxonomy"], f["tag"], f["unit"], f.get("start", ""), f["end"],
                     f["val"], f["accn"], f["filed"], encode(f), fetch_id) for f in payload])
            elif kind == "macro":
                db.executemany("INSERT OR IGNORE INTO macro_observations VALUES(?,?,?,?,?,?)", [
                    (symbol, o["date"], o["realtime_start"], o["realtime_end"], o["value"],
                     fetch_id) for o in payload])
        return version

    def save_snapshot(self, ticker, report):
        ticker = self.security_key(ticker)
        content = encode(report)
        fingerprint = hashlib.sha256(encode({"security_id": ticker, "report": report}).encode()).hexdigest()
        with self.connect() as db:
            db.execute("INSERT OR IGNORE INTO analysis_snapshots(security_id,as_of,created_at,"
                       "input_versions,feature_version,model_version,structured_report_json,"
                       "data_status,fingerprint) VALUES(?,?,?,?,?,?,?,?,?)",
                       (ticker, report["as_of"], utc_now(), encode(report["input_versions"]),
                        "company-v1", None, content, report["data_status"], fingerprint))
            snapshot = db.execute("SELECT id FROM analysis_snapshots WHERE fingerprint=?", (fingerprint,)).fetchone()
            db.execute("UPDATE analysis_coverage SET last_snapshot_id=?,last_success=?,fundamental=?,history=? "
                       "WHERE security_id=?", (snapshot[0], utc_now(),
                       int(any(v.get("value") is not None for v in report.get("financials", {}).values())),
                       int(len(report.get("chart", [])) >= 1261), ticker))

    def latest_snapshot(self, ticker):
        ticker = self.security_key(ticker)
        with self.connect() as db:
            row = db.execute("SELECT structured_report_json FROM analysis_snapshots "
                             "WHERE security_id=? ORDER BY id DESC LIMIT 1", (ticker,)).fetchone()
        return json.loads(row[0]) if row else None

    def last_job(self, security_id=None):
        with self.connect() as db:
            row = db.execute("SELECT * FROM job_runs WHERE job='company-ingest' "
                             + ("AND security_id=? " if security_id else "") +
                             "ORDER BY started_at DESC LIMIT 1",
                             (self.security_key(security_id),) if security_id else ()).fetchone()
        return dict(row) if row else None

    def security_key(self, value):
        legacy = {"NVDA": "sec-0001045810-nvda", "SPY": "benchmark-spy", "XLK": "benchmark-xlk"}
        if value in legacy:
            return legacy[value]
        with self.connect() as db:
            row = db.execute("SELECT security_id FROM securities WHERE security_id=?", (value,)).fetchone()
            if row:
                return row[0]
            rows = db.execute("SELECT security_id FROM securities WHERE ticker=? AND status='active'", (value,)).fetchall()
        if len(rows) > 1:
            raise ValueError("Ambiguous ticker; select a canonical security ID")
        return rows[0][0] if rows else value

    def dataset_key(self, kind, symbol):
        if kind != "prices":
            return symbol  # SEC data is issuer/CIK scoped; macro series are shared.
        sid = self.security_key(symbol)
        with self.connect() as db:
            row = db.execute("SELECT storage_key FROM securities WHERE security_id=?", (sid,)).fetchone()
        return row[0] if row else symbol

    def backup(self, destination):
        destination = Path(destination).resolve()
        if destination == self.path.resolve() or destination.exists():
            raise ValueError("Choose a new backup file outside the active database.")
        destination.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as source, sqlite3.connect(destination) as target:
            source.backup(target)
            if target.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise RuntimeError("Backup integrity check failed.")
        return destination
