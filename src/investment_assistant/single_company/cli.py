"""CLI integration shared by manual runs and the external scheduler."""

import json
from pathlib import Path
import sys

from investment_assistant.config import PROJECT_ROOT, Settings
from .analysis import current_report
from .config import CompanyConfig
from .storage import BusyJob, CompanyStore


def add_parser(subcommands):
    parser = subcommands.add_parser("company", help="Stored single-company analysis; no paid AI")
    parser.add_argument("--root", type=Path, default=PROJECT_ROOT / "data" / "single-company")
    parser.add_argument("--security-id", help="Canonical security ID from the local directory")
    commands = parser.add_subparsers(dest="company_command", required=True)
    commands.add_parser("status", help="Local configuration and source freshness; no vendor calls")
    ingest = commands.add_parser("ingest", help="Refresh stored data using the locked scheduled job")
    ingest.add_argument("--full", action="store_true", help="Reconcile complete price history")
    commands.add_parser("evaluate", help="Freeze a purged walk-forward/holdout ridge evaluation")
    commands.add_parser("scheduled", help="Run when due at 22:30 New York or morning catch-up")
    backup = commands.add_parser("backup", help="Write a consistent SQLite backup to a new file")
    backup.add_argument("destination", type=Path)
    directory = commands.add_parser("directory-import", help="Atomically import an official SEC exchange directory JSON")
    directory.add_argument("file", type=Path)
    reference = commands.add_parser("reference-import", help="Verify matches from a Twelve Data /stocks reference catalogue JSON")
    reference.add_argument("file", type=Path)
    commands.add_parser("directory-refresh", help="Enqueue weekly SEC directory refresh")
    worker = commands.add_parser("worker", help="Process durable preparation jobs; run periodically with your scheduler")
    worker.add_argument("--limit", type=int, default=5)
    worker.add_argument("--scheduled", action="store_true", help="Also queue daily updates for recently prepared companies")
    commands.add_parser("prepare", help="Enqueue explicit preparation for --security-id")
    commands.add_parser("refresh", help="Enqueue explicit refresh for --security-id")


def run(args):
    Settings.from_environment()  # Load the existing project's .env without exposing its contents.
    config = CompanyConfig.from_environment()
    store = CompanyStore(args.root)
    try:
        from .directory import Directory
        from .jobs import enqueue, work_one
        sid = getattr(args, "security_id", None)
        if sid and args.company_command not in {"prepare", "refresh"}:
            config = Directory(store).get(sid).config(config)
        if args.company_command == "status":
            result = current_report(store, config)
            result.pop("chart", None)
            result["configuration"] = {"twelve_key": bool(config.price_key),
                                       "fred_key": bool(config.fred_key),
                                       "sec_contact": bool(config.user_agent),
                                       "usage_reviewed": bool(config.usage_record),
                                       "adjustments_verified": bool(config.adjustment_record),
                                       "raw_retention_enabled": config.retain_raw}
        elif args.company_command == "ingest":
            from .pipeline import ingest
            result = ingest(store, config, full=args.full)
        elif args.company_command == "evaluate":
            from .forecast import evaluate_stored
            result = evaluate_stored(store, config)
        elif args.company_command == "scheduled":
            from .schedule import scheduled_run
            result = scheduled_run(store, config)
        elif args.company_command == "directory-import":
            result = {"imported": Directory(store).import_sec(json.loads(args.file.read_text(encoding="utf-8")))}
        elif args.company_command == "reference-import":
            result = {"verified": Directory(store).verify_reference(json.loads(args.file.read_text(encoding="utf-8")))}
        elif args.company_command == "directory-refresh":
            result = enqueue(store, None, "directory")
        elif args.company_command in {"prepare", "refresh"}:
            if not sid:
                raise ValueError("Specify --security-id")
            result = enqueue(store, sid, args.company_command)
        elif args.company_command == "worker":
            if not 1 <= args.limit <= 20:
                raise ValueError("Worker limit must be 1–20")
            from datetime import datetime, timezone
            with store.connect() as db:
                imported = db.execute("SELECT MAX(imported_at) FROM directory_imports").fetchone()[0]
            if config.user_agent and (datetime.now(timezone.utc) - datetime.fromisoformat(imported)).days >= 7:
                enqueue(store, None, "directory")
            if args.scheduled:
                from .jobs import enqueue_recent
                enqueue_recent(store)
            jobs = []
            for _ in range(args.limit):
                job = work_one(store, config)
                if job["state"] == "idle":
                    break
                jobs.append(job)
            result = {"jobs": jobs, "status": "failed" if any(j["state"] == "failed" for j in jobs) else "ok"}
        else:
            result = {"backup": str(store.backup(args.destination))}
        print(json.dumps(result, indent=2, allow_nan=False))
        return 1 if result.get("status") in {"failed", "partial"} else 0
    except BusyJob:
        print("A company job is already running; retry after it completes.", file=sys.stderr)
        return 1
    except (ValueError, KeyError, OSError, RuntimeError):
        print("Company command failed. Check local configuration, saved inputs and dependencies.", file=sys.stderr)
        return 1
