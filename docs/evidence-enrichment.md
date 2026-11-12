# Phase 1: enriched research evidence

Phase 1 improves the evidence prepared by `analyse --dry-run`. It does not change
the AI report format and does not make an OpenAI request.

## Evidence depth

Every selected item has exactly one label:

- `official filing extract`
- `official company release`
- `full public article extract`
- `source-provided summary`
- `public metadata description`
- `user-provided notes`
- `headline only`

The public article adapter resolves aggregator redirects when the redirect target
is exposed, checks the publisher's `robots.txt`, refuses disallowed pages, does
not bypass access controls, and extracts no more than 3,000 characters. It tries
public body text first, then a source-provided summary, JSON-LD content, Open
Graph description, and finally the headline. Trafilatura performs primary
boilerplate removal; BeautifulSoup is the fallback metadata parser.

The SEC adapter uses an identifiable `SEC_USER_AGENT`, waits at least 0.2 seconds
between uncached SEC requests in this process, and caches downloaded public
filings under `.cache/investment-assistant/sec-filings`. This is at most five
requests per second, below the SEC's published ten-request-per-second threshold.
An 8-K extract is capped at 5,000 characters and includes detected item headings,
bounded item text, exhibit descriptions, and an extractive event summary. Forms
3 and 4 use structured ownership XML when accessible.

Cached filings, bounded article extracts, private notes, and generated reports are
ignored by Git.

## Manual subscription notes

The application never signs in to subscription services. Add your own notes
interactively:

```powershell
investment-assistant add-note SPCX
```

The summary, key facts, and explanation prompts are hidden. Or prepare a JSON
file based on `data/manual-note.example.json`:

```powershell
investment-assistant add-note SPCX --file data/manual-note.example.json
```

Files are stored privately under `notes/TICKER/`. The command does not print the
summary or facts.

## Context and selection

Market context uses trading-session windows for 5, 30, and 90 sessions, plus the
30-session range, 20-session average volume, volume comparison, and annualised
20-session volatility. SPCX is compared with SPY and RKLB when those public price
series are available.

Fundamental and valuation values are third-party snapshots from Yahoo Finance.
Each field is marked unavailable, estimated/unverified, or not meaningful. These
values must be checked against official filings before a material decision.

Related stories are clustered using shared event terms. Selection prefers
relevance, importance, confidence, evidence depth, and recency. No more than
three distinct clusters become main developments, and headline-only opinion is
not used merely to fill space.

Run the Phase 1 preview without OpenAI:

```powershell
investment-assistant analyse SPCX --dry-run
```
