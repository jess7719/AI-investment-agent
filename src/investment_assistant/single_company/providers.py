"""Bounded server-side requests and strict normalization before dataset writes."""

from datetime import date, datetime, timezone
from email.utils import parsedate_to_datetime
import math
import random
import re
import time

from .calendar import session_dates


MACRO = {"DGS10": ("10-year Treasury", "percent"), "DGS2": ("2-year Treasury", "percent"),
         "DFF": ("Federal funds rate", "percent"), "CPIAUCSL": ("Consumer price index", "index"),
         "UNRATE": ("Unemployment", "percent")}
CONCEPTS = {
    "revenue": ("RevenueFromContractWithCustomerExcludingAssessedTax", "Revenues",
                "SalesRevenueNet"),
    "net_income": ("NetIncomeLoss",),
    "operating_cash_flow": ("NetCashProvidedByUsedInOperatingActivities",),
    "capex": ("PaymentsToAcquirePropertyPlantAndEquipment",),
    "cash": ("CashAndCashEquivalentsAtCarryingValue",),
    "debt_current": ("DebtCurrent",),
    "debt_noncurrent": ("LongTermDebtNoncurrent",),
    "shares": ("EntityCommonStockSharesOutstanding", "CommonStockSharesOutstanding"),
}


class ProviderError(RuntimeError):
    """Only fixed, safe messages should be passed here; never vendor error bodies."""


class ProviderDeferred(ProviderError):
    def __init__(self, message, retry_after):
        super().__init__(message)
        self.retry_after = retry_after


def finite(value, *, positive=False):
    if isinstance(value, bool):
        raise ProviderError("Invalid numeric value.")
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError):
        raise ProviderError("Invalid numeric value.") from None
    if not math.isfinite(result) or (positive and result <= 0):
        raise ProviderError("Invalid numeric value.")
    return result


def iso_date(value):
    try:
        return date.fromisoformat(value).isoformat()
    except (TypeError, ValueError):
        raise ProviderError("Invalid source date.") from None


class ProviderClient:
    def __init__(self, config, store, *, transport=None, sleep=time.sleep):
        import httpx
        self.config, self.store, self.sleep = config, store, sleep
        self.client = httpx.Client(timeout=httpx.Timeout(30, connect=10),
                                   follow_redirects=False, transport=transport)
        self.last_request = {}
        self.request_count = 0

    def close(self):
        self.client.close()

    def get(self, provider, url, params=None, headers=None):
        import httpx
        interval = {"twelve": 10.1, "sec": 1.1, "fred": 0.25}[provider]
        for attempt in range(4):
            if self.request_count >= 80:
                raise ProviderError("Per-run request budget exceeded.")
            elapsed = time.monotonic() - self.last_request.get(provider, -1e9)
            self.sleep(max(0, interval - elapsed))
            self.last_request[provider] = time.monotonic()
            from .jobs import reserve_credit
            reserve_credit(self.store, provider)
            self.request_count += 1
            try:
                response = self.client.get(url, params=params, headers=headers)
            except (httpx.TimeoutException, httpx.NetworkError):
                if attempt == 3:
                    raise ProviderError("Provider timed out or could not be reached.") from None
                self.sleep(2 ** attempt + random.random())
                continue
            if response.status_code == 429 or response.status_code >= 500:
                if attempt == 3:
                    raise ProviderError("Provider quota or temporary service failure.")
                retry = response.headers.get("Retry-After", "")
                try:
                    delay = float(retry)
                except ValueError:
                    try:
                        delay = (parsedate_to_datetime(retry) - datetime.now(timezone.utc)).total_seconds()
                    except (TypeError, ValueError):
                        delay = 2 ** attempt + random.random()
                if not math.isfinite(delay) or delay > 60:
                    raise ProviderDeferred("Provider requested a later retry", time.time() + (delay if math.isfinite(delay) else 3600))
                if response.status_code == 429 and getattr(self.store, "queue_claim", None):
                    raise ProviderDeferred("Provider quota reached; waiting before retry", time.time() + max(60, delay))
                self.sleep(max(0, delay))
                continue
            if response.status_code != 200:
                raise ProviderError("Provider rejected the request; check account permissions.")
            try:
                payload = response.json()
            except ValueError:
                raise ProviderError("Provider returned invalid JSON.") from None
            if (isinstance(payload, dict) and getattr(self.store, "queue_claim", None)
                    and (payload.get("code") == 429 or payload.get("error_code") == 429)):
                raise ProviderDeferred("Provider quota response; waiting before retry", time.time() + 3600)
            if not isinstance(payload, dict) or payload.get("status") == "error" or "error_code" in payload:
                raise ProviderError("Provider returned an error or quota response.")
            fetch = self.store.record_fetch(provider, {"url": url, **(params or {})}, payload,
                                            retain=self.config.retain_raw)
            return payload, fetch
        raise ProviderError("Provider unavailable.")

    def prices(self, symbol, mode, start=None):
        if not self.config.price_key or not self.config.usage_record:
            raise ProviderError("Configure Twelve Data credentials and a reviewed data-usage record.")
        with self.store.connect() as db:
            mapping = db.execute("SELECT p.provider_symbol,p.provider_exchange FROM provider_symbols p "
                                 "JOIN securities s USING(security_id) WHERE s.security_id=? "
                                 "AND p.provider='twelve' AND p.verification_status='verified' "
                                 "ORDER BY p.effective_from DESC LIMIT 1", (
                                     self.config.security_id if symbol == self.config.ticker and self.config.security_id
                                     else self.store.security_key(symbol),)).fetchone()
        provider_symbol = mapping[0] if mapping else symbol
        params = {"symbol": provider_symbol, "interval": "1day", "outputsize": 5000,
                  "adjust": mode, "apikey": self.config.price_key}
        if mapping:
            params["exchange"] = mapping[1]
        if start:
            params["start_date"] = start
        payload, fetch = self.get("twelve", "https://api.twelvedata.com/time_series", params)
        return normalize_prices(payload, provider_symbol, self.config.currency,
                                self.config.exchange if symbol == self.config.ticker else None), fetch

    def directory(self):
        if not re.search(r"[^\s@]+@[^\s@]+\.[^\s@]+", self.config.user_agent):
            raise ProviderError("Configure SEC contact before directory refresh")
        return self.get("sec", "https://www.sec.gov/files/company_tickers_exchange.json",
                        headers={"User-Agent": self.config.user_agent})

    def submissions(self):
        if not re.search(r"[^\s@]+@[^\s@]+\.[^\s@]+", self.config.user_agent) or "example.com" in self.config.user_agent:
            raise ProviderError("Configure a descriptive SEC User-Agent with your contact email.")
        return self.get("sec", f"https://data.sec.gov/submissions/CIK{self.config.cik}.json",
                        headers={"User-Agent": self.config.user_agent})

    def facts(self):
        return self.get("sec", "https://data.sec.gov/api/xbrl/companyfacts/"
                        f"CIK{self.config.cik}.json", headers={"User-Agent": self.config.user_agent})

    def macro(self, series, start):
        if series not in MACRO or not self.config.fred_key or not self.config.usage_record:
            raise ProviderError("Configure FRED credentials and reviewed series usage rights.")
        payload, fetch = self.get("fred", "https://api.stlouisfed.org/fred/series/observations",
                                  {"series_id": series, "file_type": "json",
                                   "api_key": self.config.fred_key, "observation_start": start,
                                   "realtime_start": "1776-07-04", "realtime_end": date.today().isoformat(),
                                   "output_type": 1, "limit": 100000})
        rows = payload.get("observations")
        if not isinstance(rows, list) or not rows or int(payload.get("count", len(rows))) > len(rows):
            raise ProviderError("FRED returned empty or truncated observations.")
        result, seen = [], set()
        for row in rows:
            value = None if row.get("value") == "." else finite(row.get("value"))
            item = {"date": iso_date(row.get("date")), "value": value,
                    "realtime_start": iso_date(row.get("realtime_start")),
                    "realtime_end": iso_date(row.get("realtime_end"))}
            key = (item["date"], item["realtime_start"], item["realtime_end"])
            if key in seen or item["realtime_start"] > item["realtime_end"]:
                raise ProviderError("Invalid or duplicate macro vintage.")
            seen.add(key)
            result.append(item)
        return sorted(result, key=lambda o: (o["date"], o["realtime_start"])), fetch


def normalize_prices(payload, symbol, currency, exchange=None):
    meta = payload.get("meta", {})
    if meta.get("symbol") != symbol or meta.get("currency") != currency:
        raise ProviderError("Price security identity or currency mismatch.")
    if exchange and meta.get("exchange", "").upper() != exchange.upper():
        raise ProviderError("Price exchange does not match the configured identity.")
    if meta.get("interval") != "1day":
        raise ProviderError("Expected daily prices.")
    values = payload.get("values")
    if not isinstance(values, list) or not values:
        raise ProviderError("Price response is empty.")
    rows, seen = [], set()
    for item in values:
        day = iso_date(item.get("datetime"))
        if day in seen:
            raise ProviderError("Duplicate price sessions.")
        seen.add(day)
        row = {k: finite(item.get(k), positive=k != "volume")
               for k in ("open", "high", "low", "close", "volume")}
        if (row["volume"] < 0 or row["low"] > min(row["open"], row["close"])
                or row["high"] < max(row["open"], row["close"])):
            raise ProviderError("Incoherent OHLC or volume.")
        rows.append({"date": day, **row})
    rows.sort(key=lambda r: r["date"])
    sessions = session_dates(rows[0]["date"], rows[-1]["date"])
    if [r["date"] for r in rows] != sessions:
        raise ProviderError("Price history contains missing or non-exchange sessions.")
    return rows


def normalize_submissions(payload, config):
    if (str(payload.get("cik", "")).zfill(10) != config.cik
            or config.ticker not in payload.get("tickers", [])
            or config.exchange not in payload.get("exchanges", [])):
        raise ProviderError("SEC ticker, exchange or CIK identity mismatch.")
    recent = payload.get("filings", {}).get("recent", {})
    accessions = recent.get("accessionNumber")
    if not isinstance(accessions, list) or not accessions:
        raise ProviderError("SEC submissions are empty.")
    keys = ("form", "acceptanceDateTime", "filingDate", "reportDate", "primaryDocument")
    if any(len(recent.get(k, [])) != len(accessions) for k in keys):
        raise ProviderError("SEC submissions have inconsistent columns.")
    rows = []
    for i, accession in enumerate(accessions):
        if not re.fullmatch(r"\d{10}-\d{2}-\d{6}", accession):
            raise ProviderError("Invalid SEC accession.")
        document = recent["primaryDocument"][i]
        if not re.fullmatch(r"[A-Za-z0-9_.\-]+", document):
            raise ProviderError("Invalid SEC document path.")
        accepted = recent["acceptanceDateTime"][i]
        try:
            stamp = datetime.fromisoformat(accepted.replace("Z", "+00:00"))
            if stamp.tzinfo is None:
                raise ValueError()
        except (ValueError, AttributeError):
            raise ProviderError("Invalid SEC acceptance timestamp.") from None
        rows.append({"accession": accession, "form": recent["form"][i],
                     "accepted_at": stamp.astimezone(timezone.utc).isoformat(),
                     "filed_date": iso_date(recent["filingDate"][i]),
                     "report_date": recent["reportDate"][i],
                     "url": f"https://www.sec.gov/Archives/edgar/data/{int(config.cik)}/"
                            f"{accession.replace('-', '')}/{document}"})
    history = []
    for item in payload.get("filings", {}).get("files", []):
        name = item.get("name", "")
        if re.fullmatch(r"CIK\d{10}-submissions-\d+\.json", name):
            history.append({"url": f"https://data.sec.gov/submissions/{name}",
                            "from": item.get("filingFrom"), "to": item.get("filingTo")})
    return {"recent": rows, "history": history, "identity_verified": True}


def normalize_facts(payload, cik):
    if str(payload.get("cik", "")).zfill(10) != cik:
        raise ProviderError("SEC facts CIK mismatch.")
    allowed = {tag for tags in CONCEPTS.values() for tag in tags}
    rows = []
    for taxonomy in ("us-gaap", "dei"):
        for tag, concept in payload.get("facts", {}).get(taxonomy, {}).items():
            if tag not in allowed:
                continue
            unit = "shares" if "SharesOutstanding" in tag else "USD"
            for fact in concept.get("units", {}).get(unit, []):
                if fact.get("form") not in {"10-Q", "10-K", "10-Q/A", "10-K/A"}:
                    continue
                row = {**fact, "taxonomy": taxonomy, "tag": tag, "unit": unit,
                       "val": finite(fact.get("val")), "end": iso_date(fact.get("end")),
                       "filed": iso_date(fact.get("filed"))}
                if "start" in row:
                    row["start"] = iso_date(row["start"])
                    if row["start"] > row["end"]:
                        raise ProviderError("Invalid financial duration.")
                if not re.fullmatch(r"\d{10}-\d{2}-\d{6}", row.get("accn", "")):
                    raise ProviderError("Invalid fact accession.")
                rows.append(row)
    if not rows:
        raise ProviderError("No supported financial concepts were returned.")
    return sorted(rows, key=lambda r: (r["tag"], r["end"], r["filed"], r.get("start", "")))
