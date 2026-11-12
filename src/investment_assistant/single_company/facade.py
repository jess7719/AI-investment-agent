"""Security-scoped, stored-only adapter to the existing calculation and context engines."""

from datetime import date

from .analysis import current_report
from .directory import Directory


class AnalysisFacade:
    def __init__(self, store, config, context_store=None):
        self.store, self.config, self.context_store = store, config, context_store
        self.directory = Directory(store)

    def latest(self, sid):
        context = self.directory.get(sid)
        row = context.identity
        identity = {k: row.get(k) for k in ("ticker", "name", "cik", "exchange", "currency", "instrument_type")}
        identity.update(market="SPY", sector="XLK" if context.sector_id == "benchmark-xlk" else None)
        if context.ready:
            report = current_report(self.store, context.config(self.config))
        else:
            report = {"company": identity, "as_of": None, "data_status": "unavailable",
                      "expected_session": None, "sources": {}, "forecast": {"status": "not_validated"}}
        report.update(security_id=sid, issuer_id=context.issuer_id,
                      analysis_status="never_prepared" if not report.get("as_of") else report["data_status"],
                      financial_template=context.financial_template, preparation_allowed=context.ready,
                      limitations=[] if context.ready else ["Instrument, currency and provider coverage require review"],
                      historical_context={"scope": "company", "status": "Company-specific analogue unavailable"})
        with self.store.connect() as db:
            snapshot = db.execute("SELECT id FROM analysis_snapshots WHERE security_id=? ORDER BY id DESC LIMIT 1", (sid,)).fetchone()
            job = db.execute("SELECT * FROM analysis_jobs WHERE security_id=? ORDER BY created_at DESC,job_id DESC LIMIT 1", (sid,)).fetchone()
        report["snapshot_id"] = snapshot[0] if snapshot and report["as_of"] else None
        report["job"] = dict(job) if job else None
        if job and job["state"] in {"queued", "running", "waiting", "failed"}:
            report["analysis_status"] = job["state"]
        # Existing context is ticker-keyed. Only pass an active identity, and retain the saved mode/scope.
        if self.context_store and row["status"] == "active" and report["as_of"]:
            saved = self.context_store.read_context(row["ticker"], date.fromisoformat(report["as_of"]), include_cases=True)
            analogue = saved.get("historical_analogues")
            if analogue:
                report["historical_context"] = {"scope": "market" if analogue["mode"] == "market_regime_only" else "company",
                                                "status": "Stored historical context", "data": analogue}
        return report
