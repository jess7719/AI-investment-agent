"""Versioned formulas and conservative, publication-aware SEC financial mapping."""

from datetime import date
import math
import statistics

from .calendar import completed_session, next_session, session_dates
from .providers import CONCEPTS, MACRO


def metric(value, unit, source, **extra):
    return {"value": value, "unit": unit, "source": source, **extra}


def price_metrics(company, market, sector):
    by_symbol = [{b["date"]: b for b in rows} for rows in (company, market, sector)]
    dates = sorted(set.intersection(*(set(rows) for rows in by_symbol)))
    if dates and dates != session_dates(dates[0], dates[-1]):
        raise ValueError("Benchmark alignment contains missing sessions.")
    closes = [[rows[d]["close"] for d in dates] for rows in by_symbol]
    result = {}
    for window in (20, 60, 252):
        returns = [(prices[-1] / prices[-window - 1] - 1) * 100
                   if len(prices) > window else None for prices in closes]
        result[f"return_{window}"] = metric(returns[0], "percent", "twelve")
        for index, label in ((1, "market"), (2, "sector")):
            relative = returns[0] - returns[index] if returns[0] is not None else None
            result[f"relative_{label}_{window}"] = metric(relative, "percentage_points", "twelve")
    daily = [[p[i] / p[i - 1] - 1 for i in range(1, len(p))] for p in closes]
    vol = statistics.stdev(daily[0][-20:]) * math.sqrt(252) * 100 if len(dates) >= 21 else None
    result["volatility_20"] = metric(vol, "percent_annualized", "twelve")
    peak, drawdown = 0, 0
    for value in closes[0][-253:]:
        peak = max(peak, value)
        drawdown = min(drawdown, value / peak - 1)
    result["drawdown_252"] = metric(drawdown * 100 if len(dates) >= 253 else None,
                                      "percent", "twelve")
    beta = None
    if len(dates) >= 253:
        variance = statistics.variance(daily[1][-252:])
        if variance > 0:
            beta = statistics.covariance(daily[0][-252:], daily[1][-252:]) / variance
    result["beta_252"] = metric(beta, "ratio", "twelve")
    volume_ratio = None
    if len(dates) >= 21:
        prior = statistics.mean(by_symbol[0][d]["volume"] for d in dates[-21:-1])
        if prior > 0:
            volume_ratio = by_symbol[0][dates[-1]]["volume"] / prior
    result["volume_ratio"] = metric(volume_ratio, "ratio", "twelve")
    unusual = [dates[i] for i, value in enumerate(daily[0], 1) if abs(value) > .35]
    chart = [{"date": d, "company": closes[0][i], "market": closes[1][i],
              "sector": closes[2][i]} for i, d in enumerate(dates)]
    return result, chart, unusual[-20:]


def available_facts(facts, filings, cutoff):
    acceptance = {f["accession"]: f["accepted_at"][:10] for f in filings}
    return [f for f in facts if f["end"] <= cutoff and next_session(
        acceptance.get(f["accn"], f["filed"])) <= cutoff]


def selected_facts(facts, concept):
    # Pick a single reviewed tag, never sum aliases. Latest disclosure wins per context.
    tags = CONCEPTS[concept]
    candidates = []
    for tag in tags:
        candidates = [f for f in facts if f["tag"] == tag]
        if candidates:
            break
    by_period = {}
    for fact in sorted(candidates, key=lambda f: (f["filed"], f["accn"])):
        by_period[(fact.get("start"), fact["end"])] = fact
    return list(by_period.values())


def quarterly(facts, concept):
    candidates = selected_facts(facts, concept)
    quarters = {}
    for f in candidates:
        if "start" not in f:
            continue
        days = (date.fromisoformat(f["end"]) - date.fromisoformat(f["start"])).days + 1
        if 70 <= days <= 105:
            quarters[(f["start"], f["end"])] = {**f, "derived": False}
        elif 150 <= days <= 380:
            # Subtract cumulative flows only within the same accession and units/tag.
            priors = [p for p in facts if p.get("start") == f["start"]
                      and p["tag"] == f["tag"] and p["unit"] == f["unit"]
                      and p["accn"] == f["accn"] and p["end"] < f["end"]
                      and 70 <= (date.fromisoformat(f["end"]) - date.fromisoformat(p["end"])).days <= 105]
            if priors:
                prior = max(priors, key=lambda p: p["end"])
                from datetime import timedelta
                start = (date.fromisoformat(prior["end"]) + timedelta(days=1)).isoformat()
                derived = {**f, "start": start, "val": f["val"] - prior["val"],
                           "derived": True, "derived_from": [prior, f]}
                quarters.setdefault((start, f["end"]), derived)
    return sorted(quarters.values(), key=lambda q: q["end"])


def financial_metrics(facts, filings, cutoff, raw_price, cik=None):
    # Older facts remain in storage but are unnecessary for current TTM/YoY metrics.
    oldest = date.fromisoformat(cutoff).toordinal() - 900
    facts = [f for f in facts if date.fromisoformat(f["end"]).toordinal() >= oldest]
    facts = available_facts(facts, filings, cutoff)
    links = {f["accession"]: f["url"] for f in filings}

    def entry(f, value=None, unit=None):
        if not f:
            return metric(None, unit or "USD", "sec", reason="Supported compatible facts unavailable")
        return metric(f["val"] if value is None else value, unit or f["unit"], "sec",
                      period_start=f.get("start"), period_end=f["end"],
                      filed_date=f["filed"], accession=f["accn"],
                      url=links.get(f["accn"], f"https://www.sec.gov/Archives/edgar/data/"
                                    f"{int(cik or f['accn'][:10])}/{f['accn'].replace('-', '')}/"),
                      derived=f.get("derived", False))

    result, quarters = {}, {}
    for concept in ("revenue", "net_income", "operating_cash_flow", "capex"):
        values = quarterly(facts, concept)
        quarters[concept] = values
        latest = values[-1] if values else None
        result[concept] = entry(latest)
        prior = next((v for v in reversed(values[:-1]) if latest and
                      350 <= (date.fromisoformat(latest["end"]) - date.fromisoformat(v["end"])).days <= 380
                      and abs((date.fromisoformat(latest["end"]) - date.fromisoformat(latest["start"])).days
                              - (date.fromisoformat(v["end"]) - date.fromisoformat(v["start"])).days) <= 10), None)
        result[f"{concept}_yoy"] = (entry(latest, (latest["val"] / prior["val"] - 1) * 100, "percent")
                                    if prior and prior["val"] > 0 else entry(None, unit="percent"))

    def ttm(concept):
        values = quarters[concept][-4:]
        annuals = [f for f in selected_facts(facts, concept) if f.get("start")
                   and 330 <= (date.fromisoformat(f["end"]) - date.fromisoformat(f["start"])).days <= 380
                   and (not values or f["end"] >= values[-1]["end"])]
        if annuals:
            return max(annuals, key=lambda f: f["end"])
        if len(values) != 4:
            return None
        if any(not 1 <= (date.fromisoformat(b["start"]) - date.fromisoformat(a["end"])).days <= 7
               for a, b in zip(values, values[1:])):
            return None
        if not 330 <= (date.fromisoformat(values[-1]["end"]) - date.fromisoformat(values[0]["start"])).days <= 380:
            return None
        return {**values[-1], "val": sum(v["val"] for v in values), "start": values[0]["start"],
                "derived": True}

    for concept in quarters:
        result[f"{concept}_ttm"] = entry(ttm(concept))
    for concept in ("cash", "debt_current", "debt_noncurrent", "shares"):
        values = [f for f in selected_facts(facts, concept) if "start" not in f]
        result[concept] = entry(max(values, key=lambda f: f["end"]) if values else None,
                                unit="shares" if concept == "shares" else "USD")
    for label, numerator, denominator in (("profit_margin", "net_income", "revenue"),):
        a, b = result[numerator], result[denominator]
        value = a["value"] / b["value"] * 100 if (a["value"] is not None and b["value"]
                    and a.get("period_start") == b.get("period_start")
                    and a.get("period_end") == b.get("period_end")) else None
        result[label] = metric(value, "percent", "sec", period_end=a.get("period_end"))
    cash, current, debt = (result[c] for c in ("cash", "debt_current", "debt_noncurrent"))
    compatible = all(m["value"] is not None for m in (cash, current, debt)) and len(
        {m.get("period_end") for m in (cash, current, debt)}) == 1
    result["net_debt"] = metric(current["value"] + debt["value"] - cash["value"] if compatible else None,
                                 "USD", "sec", period_end=cash.get("period_end"))
    ocf, capex = result["operating_cash_flow_ttm"], result["capex_ttm"]
    compatible = (ocf["value"] is not None and capex["value"] is not None
                  and ocf.get("period_start") == capex.get("period_start")
                  and ocf.get("period_end") == capex.get("period_end"))
    result["free_cash_flow_ttm"] = metric(ocf["value"] - capex["value"] if compatible else None,
                                          "USD", "sec", period_end=ocf.get("period_end"))
    shares, earnings = result["shares"], result["net_income_ttm"]
    compatible = (raw_price and shares["value"] and shares["value"] > 0 and earnings["value"]
                  and earnings["value"] > 0
                  and 0 <= (date.fromisoformat(cutoff) - date.fromisoformat(shares["period_end"])).days <= 120
                  and 0 <= (date.fromisoformat(cutoff) - date.fromisoformat(earnings["period_end"])).days <= 150)
    result["pe"] = metric(raw_price * shares["value"] / earnings["value"] if compatible else None,
                           "ratio", "sec+twelve", shares_date=shares.get("period_end"),
                           reason="Current unadjusted price × reported outstanding shares / positive TTM earnings")
    prior_shares = [f for f in selected_facts(facts, "shares") if shares["value"] and
                    350 <= (date.fromisoformat(shares["period_end"]) - date.fromisoformat(f["end"])).days <= 380]
    prior = max(prior_shares, key=lambda f: f["end"]) if prior_shares else None
    result["share_count_change"] = metric((shares["value"] / prior["val"] - 1) * 100
                                           if prior and prior["val"] > 0 else None,
                                           "percent", "sec", period_end=shares.get("period_end"),
                                           reason="Reported shares; split effects may affect the comparison")
    return result


def build_report(store, config, cutoff):
    versions, sources, price_rows, warnings = {}, {}, [], []
    for symbol in config.symbols:
        dataset = store.latest_dataset("prices", config.security_id if symbol == config.ticker and config.security_id else symbol, "all")
        if not dataset:
            raise ValueError("All three adjusted price datasets are required.")
        versions[f"prices:{symbol}:all"] = dataset["version"]
        rows = [b for b in dataset["payload"] if b["date"] <= cutoff]
        if not rows:
            raise ValueError("No completed-session price data.")
        price_rows.append(rows)
        sources[f"prices:{symbol}"] = {"date": rows[-1]["date"], "fetched_at": dataset["fetched_at"],
                                      "status": "current" if rows[-1]["date"] == cutoff else "stale"}
    # Never combine different ending sessions or hide a lagging benchmark via intersection.
    as_of = min(rows[-1]["date"] for rows in price_rows)
    price_rows = [[b for b in rows if b["date"] <= as_of] for rows in price_rows]
    metrics, chart, unusual = price_metrics(*price_rows, *([price_rows[1]] if not config.sector else []))
    if not config.sector:
        for key in metrics:
            if key.startswith("relative_sector_"):
                metrics[key]["value"] = None
        for row in chart:
            row["sector"] = None
        warnings.append("Sector comparison unavailable: no reviewed sector mapping.")
    if unusual:
        warnings.append("Unusual daily returns retained for review: " + ", ".join(unusual))
    if not config.adjustment_record:
        warnings.append("Adjustment behaviour has not been verified; returns are provisional and forecasts are disabled.")
    raw = store.latest_dataset("prices", config.security_id or config.ticker, "none")
    raw_bar = next((b for b in reversed(raw["payload"]) if b["date"] == as_of), None) if raw else None
    if raw:
        versions[f"prices:{config.ticker}:none"] = raw["version"]
        sources["unadjusted_price"] = {"date": raw["payload"][-1]["date"],
                                        "fetched_at": raw["fetched_at"],
                                        "status": "current" if raw_bar else "unavailable"}
    filings = store.latest_dataset("filings", config.cik)
    facts = store.latest_dataset("facts", config.cik)
    for name, dataset in (("filings", filings), ("facts", facts)):
        if dataset:
            versions[name] = dataset["version"]
            age = (date.fromisoformat(cutoff) - date.fromisoformat(dataset["fetched_at"][:10])).days
            sources[name] = {"date": dataset["fetched_at"][:10], "status": "current" if age <= 8 else "stale"}
        else:
            sources[name] = {"date": None, "status": "unavailable"}
    recent = filings["payload"]["recent"] if filings else []
    financial = financial_metrics(facts["payload"] if facts else [], recent, as_of,
                                  raw_bar["close"] if raw_bar else None, config.cik)
    if config.financial_template != "us_gaap_industrial":
        for value in financial.values():
            value.update(value=None, reason="Financial template has not been reviewed for this issuer")
        warnings.append("Industrial financial template unsupported or unreviewed; affected metrics withheld.")
    elif config.security_id and config.ticker != "NVDA":
        for key in ("pe", "shares", "share_count_change"):
            financial[key].update(value=None, reason="Security-specific share-count mapping requires review")
    macro = {}
    for series, (label, unit) in MACRO.items():
        dataset = store.latest_dataset("macro", series)
        rows = [o for o in dataset["payload"] if o["value"] is not None and o["date"] <= cutoff
                and o["realtime_start"] <= cutoff <= o["realtime_end"]] if dataset else []
        latest = max(rows, key=lambda o: (o["date"], o["realtime_start"])) if rows else None
        macro[series] = metric(latest["value"] if latest else None, unit, "fred", label=label,
                                observation_date=latest["date"] if latest else None,
                                vintage_start=latest["realtime_start"] if latest else None,
                                url=f"https://fred.stlouisfed.org/series/{series}")
        stale_days = 75 if series in {"UNRATE", "CPIAUCSL"} else 7
        sources[series] = {"date": latest["date"] if latest else None,
                           "fetched_at": dataset["fetched_at"] if dataset else None,
                           "status": ("current" if (date.fromisoformat(cutoff) - date.fromisoformat(latest["date"])).days <= stale_days else "stale") if latest else "unavailable"}
        if dataset:
            versions[series] = dataset["version"]
    ten, two = macro["DGS10"], macro["DGS2"]
    macro["yield_spread"] = metric(ten["value"] - two["value"] if ten["value"] is not None
        and two["value"] is not None and ten["observation_date"] == two["observation_date"] else None,
        "percentage_points", "fred", observation_date=ten["observation_date"])
    return {"company": config.identity, "security_id": store.security_key(config.security_id or config.ticker),
            "as_of": as_of, "expected_session": cutoff,
            "data_status": "current" if all(v["status"] == "current" for v in sources.values()) else "partial",
            "sources": sources, "input_versions": versions, "feature_version": "company-v1",
            "return_convention": "simple close-to-close; Twelve Data adjust=all (vendor conventions)",
            "adjustments_verified": bool(config.adjustment_record), "metrics": metrics,
            "unadjusted_price": metric(raw_bar["close"] if raw_bar else None, "USD", "twelve"),
            "financials": financial, "macro": macro, "chart": chart,
            "filings": [f for f in recent if f["filed_date"] <= cutoff][:20],
            "filing_history": filings["payload"].get("history", []) if filings else [],
            "warnings": warnings, "forecast": {"status": "not_validated", "horizon_sessions": 20},
            "commentary": {"market": "Returns compare matching exchange sessions. Beta describes historical association.",
                "financial": "Only supported SEC concepts and compatible periods are shown; missing values remain unavailable.",
                "macro": "Observation and vintage dates differ. These indicators provide context, not a cause for a price move."}}


def current_report(store, config, now=None):
    report = store.latest_snapshot(config.security_id or config.ticker)
    expected = completed_session(now)
    if not report:
        return {"company": config.identity, "as_of": None, "expected_session": expected,
                "data_status": "unavailable", "sources": {}, "forecast": {"status": "not_validated"}}
    # A config change cannot silently relabel an archived company's analysis.
    if report["company"] != config.identity:
        return {"company": config.identity, "as_of": None, "expected_session": expected,
                "data_status": "unavailable", "sources": {}, "forecast": {"status": "not_validated"}}
    job = store.last_job(config.security_id or config.ticker)
    price_stale = report["as_of"] < expected
    for name, source in report["sources"].items():
        if not source.get("date"):
            continue
        if name.startswith("prices:") or name == "unadjusted_price":
            source["status"] = "current" if source["date"] == expected else "stale"
        else:
            limit = 75 if name in {"UNRATE", "CPIAUCSL"} else 8 if name in {"facts", "filings"} else 7
            if (date.fromisoformat(expected) - date.fromisoformat(source["date"])).days > limit:
                source["status"] = "stale"
    if any(s["status"] != "current" for s in report["sources"].values()):
        report["data_status"] = "partial"
    if price_stale or (job and job["status"] in {"failed", "partial"}):
        report["data_status"] = "stale" if price_stale else "partial"
    report["expected_session"] = expected
    report["last_job"] = job
    if not config.adjustment_record:
        report["adjustments_verified"] = False
        report["forecast"] = {"status": "not_validated", "reason": "Adjustment review required"}
    if report["data_status"] != "current":
        report["forecast"] = {"status": "not_validated", "reason": "Current compatible inputs required"}
    return report
