"""One scheduled writer; page requests never call a vendor."""

from datetime import date, datetime, timedelta, timezone

from .analysis import build_report
from .calendar import completed_session, session_dates
from .providers import MACRO, ProviderClient, ProviderDeferred, ProviderError, normalize_facts, normalize_submissions


def ingest(store, config, *, full=False, client=None, now=None):
    now = now or datetime.now(timezone.utc)
    cutoff = completed_session(now)
    start = (date.fromisoformat(cutoff) - timedelta(days=3653)).isoformat()
    owned = client is None
    errors, row_count = [], 0
    with store.lease():
        job = store.start_job("company-ingest", store.security_key(config.security_id or config.ticker))
        client = client or ProviderClient(config, store)
        try:
            def dataset(kind, symbol, fetch, mode=""):
                nonlocal row_count
                key = config.security_id if kind == "prices" and symbol == config.ticker and config.security_id else symbol
                try:
                    store.renew_lease()
                    payload, fetch_id = fetch()
                    if kind == "prices":
                        previous = store.latest_dataset(kind, key, mode)
                        # Refuse an incomplete new tail even if merging could hide the problem.
                        if not payload or payload[-1]["date"] < cutoff:
                            raise ProviderError("Latest completed price session is missing.")
                        payload = [b for b in payload if b["date"] <= cutoff]
                        if previous and reconcile and payload[0]["date"] > previous["payload"][0]["date"]:
                            raise ProviderError("Full price reconciliation returned truncated history.")
                        if previous and not reconcile:
                            combined = {b["date"]: b for b in previous["payload"]}
                            combined.update({b["date"]: b for b in payload})
                            payload = [combined[d] for d in sorted(combined) if d <= cutoff]
                        if [b["date"] for b in payload] != session_dates(payload[0]["date"], cutoff):
                            raise ProviderError("Merged prices contain missing sessions.")
                    store.save_dataset(kind, key, payload, fetch_id, mode)
                    row_count += len(payload)
                    return payload
                except ProviderDeferred:
                    raise
                except (ProviderError, ValueError, KeyError, TypeError, OverflowError):
                    # Fixed messages protect keys, contact details and provider response text.
                    errors.append(f"{kind}:{symbol}:{mode}: fetch or validation failed")
                    store.record_fetch("pipeline", {"dataset": f"{kind}:{symbol}:{mode}"},
                                       {"error": "fetch or validation failed"}, status="failed")
                    return None

            for symbol, mode in [(s, "all") for s in config.symbols] + [(config.ticker, "none")]:
                key = config.security_id if symbol == config.ticker and config.security_id else symbol
                previous = store.latest_dataset("prices", key, mode)
                marker_key = store.dataset_key("prices", key)
                # Every seven days reconcile complete adjusted history, including past actions.
                reconcile = full or not previous or (
                    now - datetime.fromisoformat(previous["fetched_at"])).days >= 7
                # fetched_at changes daily; use the last full-reconciliation job marker below.
                with store.connect() as db:
                    marker = db.execute("SELECT finished_at FROM job_runs WHERE job=? AND status='success' "
                                        "ORDER BY finished_at DESC LIMIT 1", (f"full:{marker_key}:{mode}",)).fetchone()
                if not marker or (now - datetime.fromisoformat(marker[0])).days >= 7:
                    reconcile = True
                if (symbol != config.ticker and previous and not reconcile
                        and previous["payload"][-1]["date"] == cutoff):
                    continue  # Shared benchmark already covers this completed session.
                tail_start = previous["payload"][0]["date"] if previous else start
                if previous and not reconcile:
                    tail_start = previous["payload"][max(0, len(previous["payload"]) - 10)]["date"]
                payload = dataset("prices", symbol,
                                  lambda s=symbol, m=mode, t=tail_start: client.prices(s, m, t), mode)
                if payload and reconcile:
                    marker_job = store.start_job(f"full:{marker_key}:{mode}")
                    store.finish_job(marker_job, "success")

            def submissions():
                payload, fetch_id = client.submissions()
                return normalize_submissions(payload, config), fetch_id

            previous_filings = store.latest_dataset("filings", config.cik)
            shared_filings = previous_filings and previous_filings["fetched_at"][:10] == now.date().isoformat()
            filings = previous_filings["payload"] if shared_filings and not full else dataset("filings", config.cik, submissions)
            old = {f["accession"] for f in previous_filings["payload"]["recent"]
                   if f["form"] in {"10-K", "10-Q", "10-K/A", "10-Q/A"}} if previous_filings else set()
            new = {f["accession"] for f in filings["recent"]
                   if f["form"] in {"10-K", "10-Q", "10-K/A", "10-Q/A"}} if filings else set()
            facts = store.latest_dataset("facts", config.cik)
            unpublished_in_facts = filings and facts and any(
                f["form"] in {"10-K", "10-Q", "10-K/A", "10-Q/A"}
                and datetime.fromisoformat(f["accepted_at"]) > datetime.fromisoformat(facts["fetched_at"])
                for f in filings["recent"])
            if filings and (full or not facts or new - old or unpublished_in_facts or
                            (now - datetime.fromisoformat(facts["fetched_at"])).days >= 7):
                def get_facts():
                    payload, fetch_id = client.facts()
                    return normalize_facts(payload, config.cik), fetch_id
                dataset("facts", config.cik, get_facts)
            for series in MACRO:
                cached = store.latest_dataset("macro", series)
                if not full and cached and cached["fetched_at"][:10] == now.date().isoformat():
                    continue
                dataset("macro", series, lambda s=series: client.macro(s, start))
            # Failed prices cannot publish a newly dated report or a fresh forecast.
            price_failed = any(e.startswith("prices:") for e in errors)
            if not price_failed:
                report = build_report(store, config, cutoff)
                if errors:
                    report["data_status"] = "partial"
                from .forecast import evaluation_id, publish_forecast
                prices = {r["date"]: r["company"] for r in report["chart"]}
                with store.connect() as db:
                    unresolved = db.execute("SELECT id,target_start,target_end FROM forecast_results "
                                            "WHERE id LIKE 'live:%' AND evaluation_id=? "
                                            "AND realized IS NULL AND target_end<=?",
                                            (evaluation_id(config), report["as_of"])).fetchall()
                    for prediction in unresolved:
                        if prediction["target_start"] in prices and prediction["target_end"] in prices:
                            realized = prices[prediction["target_end"]] / prices[prediction["target_start"]] - 1
                            db.execute("UPDATE forecast_results SET realized=? WHERE id=? AND realized IS NULL",
                                       (realized, prediction["id"]))
                if config.sector:
                    publish_forecast(store, config, report)
                store.save_snapshot(config.security_id or config.ticker, report)
            status = "failed" if price_failed else "partial" if errors else "success"
            store.finish_job(job, status, client.request_count, row_count, "; ".join(errors) or None)
            return {"job_id": job, "status": status, "expected_session": cutoff,
                    "request_count": client.request_count, "row_count": row_count, "errors": errors}
        except Exception:
            store.finish_job(job, "failed", getattr(client, "request_count", 0), row_count,
                             "Company job failed; previously stored analysis was preserved.")
            raise
        finally:
            if owned:
                client.close()
