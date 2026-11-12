"""Timezone-aware due check for an external half-hourly scheduler."""

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from .calendar import completed_session
from .pipeline import ingest


def scheduled_run(store, config, now=None):
    now = now or datetime.now(timezone.utc)
    local = now.astimezone(ZoneInfo("America/New_York"))
    minutes = local.hour * 60 + local.minute
    due = minutes >= 22 * 60 + 30 or 8 * 60 + 30 <= minutes < 9 * 60 + 30
    if not due:
        return {"status": "skipped", "reason": "Outside New York ingestion/catch-up windows"}
    expected = completed_session(now)
    snapshot = store.latest_snapshot(config.security_id or config.ticker)
    job = store.last_job(config.security_id or config.ticker)
    if (snapshot and snapshot["as_of"] == expected and job and job["status"] == "success"
            and snapshot["company"] == config.identity):
        return {"status": "skipped", "reason": "Expected completed session already ingested"}
    return ingest(store, config, now=now)
