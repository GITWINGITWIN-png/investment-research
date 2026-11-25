"""Documents filed with the SEC: the most valuable primary source in the whole system.

## Why it matters more than news

News is a journalist's **interpretation**; a filing is **a fact the company must disclose by law**.

In particular the **8-K** is the "current report" a company must file within a few business days
of a major event (management change, merger, debt default and so on),
which makes it **the legal definition of material news**, not what a journalist chose.

## SEC requirement

The SEC requires every request to carry a User-Agent of **"name email"** with a reachable address.
Without it the response is HTTP 403 at once (tested); it is a condition of use, not a courtesy.
"""

from __future__ import annotations

import json
import time
from typing import Any

import requests

from ... import config
from . import normalize

SOURCE = "sec"
SOURCE_TYPE = "primary"

SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik}.json"
TICKER_MAP_URL = "https://www.sec.gov/files/company_tickers.json"
ARCHIVE_URL = "https://www.sec.gov/Archives/edgar/data/{cik_int}/{accession_nodash}/{doc}"


def _headers() -> dict[str, str]:
    """The SEC requires a User-Agent with a contact email, otherwise 403 (the value comes from FINBG_SEC_USER_AGENT)"""
    if not config.SEC_USER_AGENT:
        raise RuntimeError(
            "FINBG_SEC_USER_AGENT is not set. The SEC asks for a contact name and email in the "
            'User-Agent, for example: export FINBG_SEC_USER_AGENT="your-project you@your-domain.com"'
        )
    return {"User-Agent": config.SEC_USER_AGENT, "Accept-Encoding": "gzip, deflate"}


def _ticker_to_cik() -> dict[str, str]:
    """Ticker -> CIK map, cached on disk.

    The list rarely changes, so it is cached for 30 days instead of reloaded daily.
    """
    cache = config.DERIVED / "sec_ticker_cik.json"
    if cache.exists() and (time.time() - cache.stat().st_mtime) < 30 * 86400:
        return json.loads(cache.read_text(encoding="utf-8"))

    try:
        response = requests.get(TICKER_MAP_URL, headers=_headers(), timeout=30)
        response.raise_for_status()
        mapping = {
            entry["ticker"].upper(): f"{int(entry['cik_str']):010d}"
            for entry in response.json().values()
        }
    except (requests.RequestException, ValueError, KeyError) as exc:
        print(f"  ⚠️  sec: โหลดแผนที่ ticker→CIK ไม่ได้ ({type(exc).__name__})")
        return json.loads(cache.read_text(encoding="utf-8")) if cache.exists() else {}

    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps(mapping), encoding="utf-8")
    return mapping


def _field(recent: dict[str, Any], key: str, i: int) -> str:
    """Read column i of the submissions JSON without failing when the column is missing or shorter"""
    column = recent.get(key) or []
    return str(column[i]) if i < len(column) and column[i] is not None else ""


SEVERITY_RANK = {"info": 0, "high": 1, "critical": 2}


def item_severity(items: list[str]) -> str:
    """Highest severity of an 8-K across all the items filed together"""
    levels = [config.SEC_8K_ITEMS.get(code, ("", "info"))[1] for code in items]
    return max(levels, key=SEVERITY_RANK.get, default="info")


def _title(form: str, ticker: str, description: str, items: list[str]) -> str:
    """A headline that says at once what the filing is about.

    8-K with items: "[8-K] TSLA — 2.02 Results of operations · 9.01 Exhibits"
    Other forms: "[10-Q] GE — 10-Q" (unchanged)
    """
    if form == "8-K" and items:
        named = " · ".join(
            f"{code} {config.SEC_8K_ITEMS.get(code, ('ไม่ทราบหัวข้อ', 'info'))[0]}" for code in items
        )
        return f"[{form}] {ticker} — {named}"
    return f"[{form}] {ticker} — {description}"


def fetch(tickers: list[str] | None = None, lookback_days: int = 7) -> list[dict[str, Any]]:
    """Fetch the latest filings for the watchlist, only forms that report real events.

    Forms 3/4/5 (insider trades) are left out because they are numerous and
    mostly pre-planned transactions, not signals.
    """
    from datetime import date, timedelta

    targets = tickers or config.WATCHLIST
    cik_map = _ticker_to_cik()
    if not cik_map:
        return []

    cutoff = (date.today() - timedelta(days=lookback_days)).isoformat()
    records: list[dict[str, Any]] = []

    for ticker in targets:
        cik = cik_map.get(ticker.upper())
        if not cik:
            continue

        try:
            response = requests.get(
                SUBMISSIONS_URL.format(cik=cik), headers=_headers(), timeout=30
            )
            response.raise_for_status()
            recent = response.json()["filings"]["recent"]
        except (requests.RequestException, ValueError, KeyError) as exc:
            print(f"  ⚠️  sec/{ticker}: {type(exc).__name__}")
            continue

        for i, form in enumerate(recent.get("form", [])):
            filing_date = recent["filingDate"][i]
            if filing_date < cutoff:
                break                                  # Sorted newest to oldest, so we can stop at the first old one
            if form not in config.SEC_FORMS:
                continue

            accession = recent["accessionNumber"][i]
            document = _field(recent, "primaryDocument", i)
            url = ARCHIVE_URL.format(
                cik_int=int(cik),
                accession_nodash=accession.replace("-", ""),
                doc=document,
            )

            items = [code.strip() for code in _field(recent, "items", i).split(",") if code.strip()]
            description = _field(recent, "primaryDocDescription", i) or form
            accepted = _field(recent, "acceptanceDateTime", i)
            records.append(
                normalize(
                    url=url,
                    title=_title(form, ticker, description, items),
                    # The time the SEC actually accepted the document (to the second) is much more precise than the filing date
                    # For example TSLA's report was "filed 21 Oct" but the SEC accepted it at 00:41 UTC on the 22nd
                    # (after the market closed on the 21st), so the correct knowledge time is the later one
                    published_at=accepted.replace(".000Z", "+00:00") if accepted
                    else f"{filing_date}T00:00:00+00:00",
                    source=SOURCE,
                    source_type=SOURCE_TYPE,
                    publisher="SEC EDGAR",
                    tickers=[ticker],
                    extra={
                        "form": form,
                        "accession": accession,
                        "filing_date": filing_date,
                        "items": items,
                        "severity": item_severity(items),
                        "report_date": _field(recent, "reportDate", i),
                    },
                )
            )

        # The SEC asks for at most 10 requests per second; keep a margin
        time.sleep(config.SEC_MIN_INTERVAL_SEC)

    return records
