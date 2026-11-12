"""Explicit pilot identity and provider permissions. Secrets never enter snapshots."""

from dataclasses import dataclass
import os
import re


@dataclass(frozen=True)
class CompanyConfig:
    ticker: str = "NVDA"
    name: str = "NVIDIA"
    cik: str = "0001045810"
    exchange: str = "NASDAQ"
    currency: str = "USD"
    market: str = "SPY"
    sector: str = "XLK"
    price_key: str = ""
    fred_key: str = ""
    user_agent: str = ""
    # Explicit human-reviewed records, not automatic assertions about licensing.
    usage_record: str = ""
    adjustment_record: str = ""
    retain_raw: bool = False
    security_id: str = ""
    financial_template: str = "us_gaap_industrial"

    def __post_init__(self):
        if (not re.fullmatch(r"[A-Z][A-Z0-9.\-]{0,14}", self.ticker)
                or not re.fullmatch(r"\d{10}", self.cik)
                or self.exchange not in {"NASDAQ", "NYSE", "NYSE American"}
                or self.currency != "USD"):
            raise ValueError("Configure a US operating company with a verified ticker and CIK.")
        if len(set(self.symbols)) != len(self.symbols) or any(
                not re.fullmatch(r"[A-Z][A-Z0-9.\-]{0,14}", s) for s in self.symbols):
            raise ValueError("Configure distinct US symbols.")

    @property
    def symbols(self):
        return (self.ticker, self.market) + ((self.sector,) if self.sector else ())

    @property
    def identity(self):
        return {"ticker": self.ticker, "name": self.name, "cik": self.cik,
                "exchange": self.exchange, "currency": self.currency,
                "instrument_type": "operating_company", "market": self.market,
                "sector": self.sector}

    @classmethod
    def from_environment(cls):
        return cls(ticker=os.getenv("COMPANY_TICKER", "NVDA").upper(),
                   name=os.getenv("COMPANY_NAME", "NVIDIA"),
                   cik=os.getenv("COMPANY_CIK", "0001045810"),
                   exchange=os.getenv("COMPANY_EXCHANGE", "NASDAQ"),
                   market=os.getenv("COMPANY_MARKET", "SPY"),
                   sector=os.getenv("COMPANY_SECTOR", "XLK"),
                   price_key=os.getenv("TWELVE_DATA_API_KEY", ""),
                   fred_key=os.getenv("FRED_API_KEY", ""),
                   user_agent=os.getenv("SEC_USER_AGENT", ""),
                   usage_record=os.getenv("COMPANY_DATA_USAGE_RECORD", ""),
                   adjustment_record=os.getenv("COMPANY_ADJUSTMENT_RECORD", ""),
                   retain_raw=os.getenv("COMPANY_RETAIN_RAW", "false").lower() == "true")
