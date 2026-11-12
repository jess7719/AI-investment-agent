"""Durable explicit work, atomic claims, expiring leases and provider credit accounting."""

from datetime import datetime, timedelta, timezone
import os
import time
import uuid

from .directory import Directory
from .providers import ProviderClient, ProviderDeferred
from .storage import BusyJob, utc_now

ACTIVE = {"queued", "waiting", "running"}


def get_job(store, job_id):
    with store.connect() as db:
        row = db.execute("SELECT * FROM analysis_jobs WHERE job_id=?", (job_id,)).fetchone()
    return dict(row) if row else None


def enqueue(store, sid, kind="prepare"):
    if kind not in {"prepare", "refresh", "scheduled", "directory"}:
        raise ValueError("Unknown job type")
    if kind != "directory" and not Directory(store).get(sid).ready:
        raise ValueError("Reference identity needs review before analysis preparation")
    with store.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        old = db.execute("SELECT * FROM analysis_jobs WHERE " +
                         ("job_type='directory'" if kind == "directory" else "security_id=?") +
                         " AND state IN ('queued','waiting','running')",
                         () if kind == "directory" else (sid,)).fetchone()
        if old:
            return dict(old)
        # Local burst cap independent of vendor budgets; repeated active jobs reuse above.
        recent = db.execute("SELECT COUNT(*) FROM analysis_jobs WHERE created_at>?",
                            ((datetime.now(timezone.utc) - timedelta(hours=1)).isoformat(),)).fetchone()[0]
        if recent >= 30:
            raise ValueError("Hourly preparation limit reached; retry later")
        job = uuid.uuid4().hex
        db.execute("INSERT INTO analysis_jobs(job_id,security_id,job_type,state,created_at,idempotency_key,priority) "
                   "VALUES(?,?,?,'queued',?,?,?)",
                   (job, sid, kind, utc_now(), f"{sid}:{kind}",
                    0 if kind == "directory" else 1 if kind == "scheduled" else 10))
        if sid:
            db.execute("UPDATE analysis_coverage SET last_attempt=? WHERE security_id=?", (utc_now(), sid))
    return get_job(store, job)


def enqueue_recent(store, now=None):
    """Opt-in scheduler scope: successful analysis in the last week, never sidebar clicks."""
    from .calendar import completed_session
    now = now or datetime.now(timezone.utc)
    cutoff = completed_session(now)
    with store.connect() as db:
        rows = db.execute("SELECT c.security_id FROM analysis_coverage c JOIN securities s USING(security_id) "
                          "JOIN analysis_snapshots a ON a.id=c.last_snapshot_id WHERE s.status='active' "
                          "AND c.price=1 AND c.last_success>=? AND a.as_of<? ORDER BY c.last_success DESC LIMIT 20",
                          ((now - timedelta(days=7)).isoformat(), cutoff)).fetchall()
    return [enqueue(store, row[0], "scheduled")["job_id"] for row in rows]


def claim(store, now=None):
    now = time.time() if now is None else now
    owner = uuid.uuid4().hex
    with store.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        db.execute("UPDATE analysis_jobs SET state='queued',lease_owner=NULL,lease_expiry=NULL "
                   "WHERE state='running' AND lease_expiry<?", (now,))
        row = db.execute("SELECT * FROM analysis_jobs WHERE state IN ('queued','waiting') AND retry_after<=? "
                         "ORDER BY priority DESC,created_at,job_id LIMIT 1", (now,)).fetchone()
        if not row:
            return None
        db.execute("UPDATE analysis_jobs SET state='running',started_at=?,lease_owner=?,lease_expiry=?,"
                   "attempts=attempts+1,safe_error=NULL WHERE job_id=?",
                   (utc_now(), owner, now + 3600, row["job_id"]))
    return get_job(store, row["job_id"])


def heartbeat(store):
    active = getattr(store, "queue_claim", None)
    if active:
        with store.connect() as db:
            result = db.execute("UPDATE analysis_jobs SET lease_expiry=? WHERE job_id=? AND lease_owner=? "
                                "AND state='running' AND lease_expiry>?",
                                (time.time() + 3600, *active, time.time()))
            if result.rowcount != 1:
                raise BusyJob("Preparation job lease lost")


def finish(store, job, state, error=None, retry_after=0):
    with store.connect() as db:
        db.execute("UPDATE analysis_jobs SET state=?,safe_error=?,retry_after=?,finished_at=?,"
                   "lease_owner=NULL,lease_expiry=NULL WHERE job_id=? AND lease_owner=? AND state='running'",
                   (state, error, retry_after, None if state == "waiting" else utc_now(),
                    job["job_id"], job["lease_owner"]))


def reserve_credit(store, provider, weight=1):
    """Only queued ingestion uses account-wide budgets; legacy CLI retains its existing cap."""
    if not getattr(store, "queue_claim", None):
        return
    heartbeat(store)
    now = datetime.now(timezone.utc)
    day, minute = now.date().isoformat(), now.strftime("%Y-%m-%dT%H:%M")
    budget = max(0, int(os.getenv(f"COMPANY_{provider.upper()}_DAILY_CREDITS", "0")))
    minute_budget = {"twelve": 6, "sec": 45, "fred": 60}[provider]
    with store.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        for bucket, limit, retry in ((day, budget, (now + timedelta(days=1)).replace(hour=0, minute=0, second=0).timestamp()),
                                      (minute, minute_budget, (now + timedelta(minutes=1)).replace(second=0).timestamp())):
            row = db.execute("SELECT used FROM provider_credits WHERE provider=? AND day=?", (provider, bucket)).fetchone()
            if (row[0] if row else 0) + weight > limit:
                raise ProviderDeferred("Provider credit budget unavailable; configure reviewed account limits", retry)
        for bucket in (day, minute):
            db.execute("INSERT INTO provider_credits VALUES(?,?,?) ON CONFLICT(provider,day) "
                       "DO UPDATE SET used=used+excluded.used", (provider, bucket, weight))


def work_one(store, base_config, *, client=None):
    job = claim(store)
    if not job:
        return {"state": "idle"}
    store.queue_claim = (job["job_id"], job["lease_owner"])
    try:
        if job["job_type"] == "directory":
            with store.lease():
                source = client or ProviderClient(base_config, store)
                try:
                    payload, _ = source.directory()
                    Directory(store).import_sec(payload)
                finally:
                    if client is None:
                        source.close()
            finish(store, job, "success")
        else:
            context = Directory(store).get(job["security_id"])
            config = context.config(base_config)
            if client is None and not (config.price_key and config.fred_key and config.user_agent and config.usage_record):
                raise ProviderDeferred("Configure credentials, SEC contact and reviewed usage permissions", time.time() + 3600)
            from .pipeline import ingest
            result = ingest(store, config, client=client)
            finish(store, job, result["status"], "; ".join(result["errors"]) or None)
    except ProviderDeferred as exc:
        finish(store, job, "waiting", str(exc), exc.retry_after)
    except BusyJob:
        finish(store, job, "waiting", "Another writer is active", time.time() + 60)
    except Exception:
        finish(store, job, "failed", "Preparation failed; previously stored analysis retained")
    finally:
        store.queue_claim = None
    return get_job(store, job["job_id"])
