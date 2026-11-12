"""Local security identities. SEC candidates never imply provider entitlement or template support."""

import base64
from dataclasses import dataclass, replace
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import threading
import unicodedata
import uuid

from .storage import utc_now

SOURCE = "https://www.sec.gov/files/company_tickers_exchange.json"
REFERENCE = Path(__file__).parent / "reference" / "sec_directory.json"
EXCHANGES = {"Nasdaq": "NASDAQ", "NYSE": "NYSE", "NYSE American": "NYSE American"}
PILOT = "sec-0001045810-nvda"
MARKET = "benchmark-spy"
SECTOR = "benchmark-xlk"


def normalized(value):
    return unicodedata.normalize("NFKC", value).casefold().strip()


SCHEMA = """
CREATE TABLE issuers(
 issuer_id TEXT PRIMARY KEY, cik TEXT UNIQUE NOT NULL, name TEXT NOT NULL,
 financial_template TEXT NOT NULL DEFAULT 'unreviewed', reference_source TEXT,
 reference_time TEXT);
CREATE TABLE securities(
 security_id TEXT PRIMARY KEY, issuer_id TEXT REFERENCES issuers(issuer_id),
 ticker TEXT NOT NULL, name TEXT NOT NULL, exchange TEXT NOT NULL, currency TEXT,
 share_class TEXT, instrument_type TEXT NOT NULL DEFAULT 'unreviewed',
 status TEXT NOT NULL DEFAULT 'active', effective_from TEXT, effective_to TEXT,
 ticker_norm TEXT NOT NULL, name_norm TEXT NOT NULL, storage_key TEXT UNIQUE NOT NULL,
 source TEXT NOT NULL);
CREATE UNIQUE INDEX security_active_listing ON securities(ticker,exchange) WHERE status='active';
CREATE INDEX security_ticker_search ON securities(ticker_norm);
CREATE INDEX security_name_search ON securities(name_norm);
CREATE INDEX security_exchange_status ON securities(exchange,status);
CREATE TABLE provider_symbols(
 security_id TEXT REFERENCES securities(security_id), provider TEXT, provider_symbol TEXT,
 provider_exchange TEXT, verification_status TEXT, verified_at TEXT, effective_from TEXT,
 PRIMARY KEY(security_id,provider,effective_from));
CREATE TABLE analysis_coverage(
 security_id TEXT PRIMARY KEY REFERENCES securities(security_id), price INTEGER DEFAULT 0,
 fundamental INTEGER DEFAULT 0, history INTEGER DEFAULT 0, template INTEGER DEFAULT 0,
 last_snapshot_id INTEGER, last_attempt TEXT, last_success TEXT, eligibility_reason TEXT);
CREATE TABLE benchmark_mappings(
 security_id TEXT PRIMARY KEY REFERENCES securities(security_id), market_id TEXT,
 sector_id TEXT, effective_from TEXT, source TEXT);
CREATE TABLE directory_imports(id TEXT PRIMARY KEY, imported_at TEXT, source TEXT,
 source_hash TEXT, row_count INTEGER);
CREATE TABLE analysis_jobs(
 job_id TEXT PRIMARY KEY, security_id TEXT REFERENCES securities(security_id), job_type TEXT,
 state TEXT, created_at TEXT, started_at TEXT, finished_at TEXT, lease_expiry REAL,
 lease_owner TEXT, retry_after REAL DEFAULT 0, safe_error TEXT, idempotency_key TEXT,
 priority INTEGER DEFAULT 0, attempts INTEGER DEFAULT 0);
CREATE UNIQUE INDEX active_security_job ON analysis_jobs(security_id)
 WHERE state IN ('queued','running','waiting');
CREATE INDEX job_claim ON analysis_jobs(state,retry_after,priority,created_at);
CREATE TABLE provider_credits(provider TEXT, day TEXT, used INTEGER,
 PRIMARY KEY(provider,day));
"""


def migrate(store):
    """Append-only migration, retaining all old reports, versions and forecast artifacts."""
    with store.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        if db.execute("SELECT 1 FROM schema_migrations WHERE version=3").fetchone():
            return
        db.execute("ALTER TABLE securities RENAME TO legacy_security_identities")
        # executescript commits implicitly; individual statements keep this migration atomic.
        for statement in SCHEMA.split(";"):
            if statement.strip():
                db.execute(statement)
        db.execute("ALTER TABLE job_runs ADD COLUMN security_id TEXT")
        db.execute("CREATE INDEX company_job_scope ON job_runs(security_id,started_at)")
        db.execute("CREATE INDEX snapshot_scope ON analysis_snapshots(security_id,as_of)")
        _import(db, parse_seed(json.loads(REFERENCE.read_text(encoding="utf-8"))),
                hashlib.sha256(REFERENCE.read_bytes()).hexdigest(), seed=True)
        verified_pilot = False
        for snapshot in db.execute("SELECT id,structured_report_json FROM analysis_snapshots WHERE security_id='NVDA'").fetchall():
            try:
                identity = json.loads(snapshot["structured_report_json"])["company"]
                verified = all(identity.get(k) == v for k, v in {
                    "ticker": "NVDA", "cik": "0001045810", "exchange": "NASDAQ", "currency": "USD"}.items())
            except (ValueError, TypeError, KeyError):
                verified = False
            if verified:
                verified_pilot = True
                db.execute("UPDATE analysis_snapshots SET security_id=? WHERE id=?", (PILOT, snapshot["id"]))
        # Old job rows have no identity. Attribute them only when the archived pilot is verified.
        legacy = db.execute("SELECT ticker FROM legacy_security_identities").fetchall()
        if verified_pilot and all(row[0] == "NVDA" for row in legacy):
            db.execute("UPDATE job_runs SET security_id=? WHERE job='company-ingest'", (PILOT,))
        db.execute("UPDATE analysis_coverage SET last_snapshot_id=(SELECT MAX(id) FROM "
                   "analysis_snapshots WHERE security_id=analysis_coverage.security_id)")
        db.execute("INSERT INTO schema_migrations VALUES(3)")


def parse_seed(payload):
    fields, rows = payload.get("fields"), payload.get("data")
    if (not isinstance(fields, list) or len(fields) != len(set(fields))
            or not {"cik", "name", "ticker", "exchange"} <= set(fields)
            or not isinstance(rows, list) or not rows):
        raise ValueError("Invalid SEC directory columns or empty import")
    result, seen = [], set()
    for values in rows:
        if not isinstance(values, list) or len(values) != len(fields):
            raise ValueError("Malformed SEC directory row")
        row = dict(zip(fields, values))
        if row["exchange"] not in EXCHANGES:
            continue
        ticker, name, cik = row["ticker"], row["name"], str(row["cik"]).zfill(10)
        if (not isinstance(ticker, str) or not re.fullmatch(r"[A-Z][A-Z0-9.\-]{0,14}", ticker)
                or not isinstance(name, str) or not name.strip() or len(name) > 300
                or not re.fullmatch(r"\d{10}", cik)):
            raise ValueError("Invalid SEC candidate identity")
        exchange = EXCHANGES[row["exchange"]]
        if (ticker, exchange) in seen:
            raise ValueError("Duplicate active listing")
        seen.add((ticker, exchange))
        result.append((cik, name, ticker, exchange))
    if not result:
        raise ValueError("No eligible listings")
    return result


def _import(db, rows, digest, *, seed=False):
    now = utc_now()
    prior = db.execute("SELECT COUNT(*) FROM securities WHERE source=? AND status='active'",
                       (SOURCE,)).fetchone()[0]
    if prior and len(rows) < prior * .8:
        raise ValueError("Suspiciously shrinking directory; last good import retained")
    active = set()
    for cik, name, ticker, exchange in rows:
        if ticker in {"SPY", "XLK"}:
            continue  # Existing benchmark identities, never operating-company candidates.
        issuer = f"sec-{cik}"
        db.execute("INSERT INTO issuers(issuer_id,cik,name,reference_source,reference_time) "
                   "VALUES(?,?,?,?,?) ON CONFLICT(issuer_id) DO UPDATE SET name=excluded.name,"
                   "reference_time=excluded.reference_time", (issuer, cik, name, SOURCE, now))
        old = db.execute("SELECT * FROM securities WHERE ticker=? AND exchange=? AND status='active'",
                         (ticker, exchange)).fetchone()
        if old and old["issuer_id"] != issuer:
            # Ticker reuse requires explicit review, never move old data to a new issuer.
            raise ValueError("Listing identity changed; identity review required")
        sid = old["security_id"] if old else (
            PILOT if cik == "0001045810" and ticker == "NVDA" else "sec-" + uuid.uuid4().hex)
        active.add(sid)
        if sid == PILOT:
            name = "NVIDIA"  # Preserve the archived pilot identity and frozen evaluation hash.
        db.execute("INSERT INTO securities(security_id,issuer_id,ticker,name,exchange,currency,"
                   "ticker_norm,name_norm,storage_key,source,effective_from) VALUES(?,?,?,?,?,?,?,?,?,?,?) "
                   "ON CONFLICT(security_id) DO UPDATE SET name=excluded.name,name_norm=excluded.name_norm",
                   (sid, issuer, ticker, name, exchange, None, normalized(ticker), normalized(name),
                    "NVDA" if sid == PILOT else sid, SOURCE, now))
        db.execute("INSERT OR IGNORE INTO analysis_coverage(security_id,eligibility_reason) VALUES(?,?)",
                   (sid, "Reference identity only; instrument, currency and provider mapping need review"))
        db.execute("INSERT OR IGNORE INTO benchmark_mappings VALUES(?,?,?,?,?)",
                   (sid, MARKET, None, now, "Default US market; sector unreviewed"))
    for old in db.execute("SELECT security_id FROM securities WHERE source=? AND status='active'",
                          (SOURCE,)).fetchall():
        if old[0] not in active:
            db.execute("UPDATE securities SET status='retired',effective_to=? WHERE security_id=?",
                       (now, old[0]))
    if seed:
        for sid, ticker, name in ((MARKET, "SPY", "SPDR S&P 500 ETF Trust"),
                                  (SECTOR, "XLK", "Technology Select Sector SPDR Fund")):
            db.execute("INSERT INTO securities(security_id,ticker,name,exchange,currency,instrument_type,"
                       "ticker_norm,name_norm,storage_key,source) VALUES(?,?,?,?,?,?,?,?,?,?)",
                       (sid, ticker, name, "NYSE Arca", "USD", "benchmark", normalized(ticker),
                        normalized(name), ticker, "Existing reviewed pilot configuration"))
        db.execute("UPDATE securities SET name='NVIDIA',currency='USD',instrument_type='operating_company' "
                   "WHERE security_id=?", (PILOT,))
        db.execute("UPDATE issuers SET financial_template='us_gaap_industrial' WHERE cik='0001045810'")
        db.execute("UPDATE analysis_coverage SET price=1,template=1,eligibility_reason=? WHERE security_id=?",
                   ("Existing pilot mapping; account permissions and adjustment review still required", PILOT))
        db.execute("UPDATE benchmark_mappings SET sector_id=?,source=? WHERE security_id=?",
                   (SECTOR, "Existing NVDA/SPY/XLK configuration", PILOT))
        for sid, symbol, exchange in ((PILOT, "NVDA", "NASDAQ"), (MARKET, "SPY", "NYSE Arca"),
                                      (SECTOR, "XLK", "NYSE Arca")):
            db.execute("INSERT INTO provider_symbols VALUES(?,?,?,?,?,?,?)",
                       (sid, "twelve", symbol, exchange, "verified", now, now))
    db.execute("INSERT INTO directory_imports VALUES(?,?,?,?,?)",
               (uuid.uuid4().hex, now, SOURCE, digest, len(rows)))


@dataclass(frozen=True)
class SecurityContext:
    security_id: str
    issuer_id: str | None
    identity: dict
    provider_symbols: tuple
    financial_template: str
    market_id: str | None
    sector_id: str | None

    def config(self, base):
        row = self.identity
        if not self.ready:
            raise ValueError("Provider/instrument identity must be verified before preparation")
        # Human reviews for one company cannot authorize another's adjustments.
        pilot = self.security_id == PILOT and base.ticker == "NVDA" and base.cik == "0001045810"
        return replace(base, ticker=row["ticker"], name=row["name"], cik=row["cik"],
                       exchange=row["exchange"], currency=row["currency"], market="SPY",
                       sector="XLK" if self.sector_id == SECTOR else "",
                       security_id=self.security_id, financial_template=self.financial_template,
                       adjustment_record=base.adjustment_record if pilot else "")

    @property
    def ready(self):
        return (self.identity["status"] == "active" and self.identity["currency"] == "USD"
                and self.identity["instrument_type"] == "operating_company"
                and any(p["verification_status"] == "verified" for p in self.provider_symbols))


class Directory:
    def __init__(self, store):
        self.store = store
        self._reader = None
        self._search_lock = threading.RLock()

    def close(self):
        with self._search_lock:
            if self._reader is not None:
                self._reader.close()
                self._reader = None

    def get(self, sid):
        with self.store.connect() as db:
            row = db.execute("SELECT s.*,i.cik,i.financial_template FROM securities s LEFT JOIN issuers i "
                             "ON s.issuer_id=i.issuer_id WHERE security_id=?", (sid,)).fetchone()
            if not row:
                raise KeyError(sid)
            symbols = db.execute("SELECT * FROM provider_symbols WHERE security_id=? ORDER BY effective_from DESC",
                                 (sid,)).fetchall()
            benchmarks = db.execute("SELECT * FROM benchmark_mappings WHERE security_id=?", (sid,)).fetchone()
        return SecurityContext(sid, row["issuer_id"], dict(row), tuple(dict(p) for p in symbols),
                               row["financial_template"] or "unsupported",
                               benchmarks["market_id"] if benchmarks else None,
                               benchmarks["sector_id"] if benchmarks else None)

    def import_sec(self, payload):
        rows = parse_seed(payload)
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            _import(db, rows, hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest())
        return len(rows)

    def search(self, q="", filter="all", cursor=None, limit=50, saved=()):
        q = normalized(q)
        if len(q) > 100 or filter not in {"all", "ready", "saved"} or not 1 <= limit <= 100:
            raise ValueError("Invalid directory query")
        query_id = hashlib.sha256(json.dumps([q, filter, sorted(saved)]).encode()).hexdigest()[:16]
        offset = 0
        if cursor:
            try:
                token = json.loads(base64.urlsafe_b64decode(cursor))
                if (not isinstance(token, list) or len(token) != 2 or token[0] != query_id
                        or not isinstance(token[1], int) or not 0 <= token[1] <= 100000):
                    raise ValueError()
                offset = token[1]
            except (ValueError, TypeError, IndexError, UnicodeError):
                raise ValueError("Invalid pagination cursor") from None
        # instr/substr treat punctuation and SQL wildcards literally.
        where = "s.status='active' AND s.instrument_type!='benchmark'"
        if filter == "ready":
            where += " AND c.price=1 AND s.instrument_type='operating_company'"
        if filter == "saved":
            saved = tuple(saved)[:200]
            where += " AND s.security_id IN (" + ",".join("?" for _ in saved) + ")"
        sql = ("SELECT s.security_id,s.ticker,s.name,s.exchange,s.currency,s.share_class,s.instrument_type,"
               "c.price,c.template,c.last_snapshot_id,c.eligibility_reason,"
               "CASE WHEN s.ticker_norm=? THEN 0 WHEN substr(s.ticker_norm,1,length(?))=? THEN 1 "
               "WHEN substr(s.name_norm,1,length(?))=? THEN 2 ELSE 3 END AS rank "
               "FROM securities s JOIN analysis_coverage c USING(security_id) WHERE " + where +
               " AND (?='' OR substr(s.ticker_norm,1,?)=? OR instr(s.name_norm,?)>0 OR EXISTS "
               "(SELECT 1 FROM provider_symbols p WHERE p.security_id=s.security_id "
               "AND lower(p.provider_symbol)=?)) "
               "ORDER BY rank,s.ticker_norm,s.exchange,s.security_id LIMIT ? OFFSET ?")
        # Arguments follow select ranking, saved filter, then search predicate.
        params = [q, q, q, q, q] + (list(saved) if filter == "saved" else []) + [q, len(q), q, q, q, limit + 1, offset]
        with self._search_lock:
            if self._reader is None:
                self._reader = sqlite3.connect(self.store.path, timeout=15, check_same_thread=False)
                self._reader.row_factory = sqlite3.Row
                self._reader.execute("PRAGMA query_only=ON")
                self._reader.execute("PRAGMA cache_size=-16384")
            db = self._reader
            rows = [dict(r) for r in db.execute(sql, params)]
            counts = db.execute("SELECT COUNT(*),SUM(c.last_snapshot_id IS NOT NULL),SUM(c.template=1) "
                                "FROM securities s JOIN analysis_coverage c USING(security_id) "
                                "WHERE s.status='active' AND s.instrument_type!='benchmark'").fetchone()
            imported = db.execute("SELECT imported_at FROM directory_imports ORDER BY imported_at DESC LIMIT 1").fetchone()
        for row in rows:
            row["availability"] = ("Stored analysis" if row["last_snapshot_id"] else
                                   "Ready to analyse" if row["price"] else "Identity only · review needed")
        return {"items": rows[:limit], "next_cursor": base64.urlsafe_b64encode(json.dumps(
            [query_id, offset + limit]).encode()).decode() if len(rows) > limit else None,
                "counts": dict(zip(("directory", "analysed", "template_supported"), [v or 0 for v in counts])),
                "source": SOURCE, "imported_at": imported[0] if imported else None}

    def verify_reference(self, payload):
        """Match the actual provider catalogue by explicit ticker/exchange, never punctuation guessing."""
        rows = payload.get("data")
        if not isinstance(rows, list) or not rows:
            raise ValueError("Empty provider reference catalogue")
        matches = {}
        for row in rows:
            if not isinstance(row, dict):
                raise ValueError("Invalid provider reference")
            exchange = EXCHANGES.get(row.get("exchange"), row.get("exchange"))
            key = (row.get("symbol"), exchange)
            if key in matches:
                raise ValueError("Ambiguous provider reference")
            matches[key] = row
        count = 0
        with self.store.connect() as db:
            for security in db.execute("SELECT * FROM securities WHERE status='active' AND instrument_type!='benchmark'").fetchall():
                row = matches.get((security["ticker"], security["exchange"]))
                if not row or row.get("currency") != "USD" or row.get("type") != "Common Stock":
                    continue
                now = utc_now()
                db.execute("INSERT OR REPLACE INTO provider_symbols VALUES(?,?,?,?,?,?,?)",
                           (security["security_id"], "twelve", row["symbol"], row["exchange"], "verified", now, now))
                db.execute("UPDATE securities SET currency='USD',instrument_type='operating_company' WHERE security_id=?",
                           (security["security_id"],))
                db.execute("UPDATE analysis_coverage SET price=1,eligibility_reason=? WHERE security_id=?",
                           ("Price analysis supported; industrial financial template requires separate review", security["security_id"]))
                count += 1
        return count
