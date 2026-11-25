"""Whole-market SEC EDGAR: the most official free primary source.

| What | From where | Why it is valuable |
|---|---|---|
| every filing of the day | daily-index/form.YYYYMMDD.idx (1 request) | about 3,400 a day |
| 8-K items | the -index-headers.html file (about 5 KB each) | the topic without opening the filing |
| Form 4 (insider buys/sells) | the filing's .txt file (about 20 KB) | **open-market purchases with own money** (code P) are the signal research supports; shares received as pay (A/M/F) are not |
| 13D | form name in the daily index | activist investors holding more than 5% |
| 8-K text | EX-99.1 in the .txt file | read only for stocks that pass stage 1 (bandwidth) |

Every request sends a User-Agent with an email, as the SEC requires, paced at about 7 per second or less (the cap is 10).
"""

from __future__ import annotations

import html
import json
import re
import time
from datetime import date, timedelta
from typing import Any

import pandas as pd
import requests

from .. import config, storage
from ..collect.sources import sec as sec_source

INDEX_URL = "https://www.sec.gov/Archives/edgar/daily-index/{year}/QTR{quarter}/form.{day}.idx"
ARCHIVE = "https://www.sec.gov/Archives/"
LINE = re.compile(r"^(?P<form>\S.*?)\s{2,}(?P<company>.+?)\s{2,}(?P<cik>\d+)\s+(?P<filed>\d{8})\s+(?P<path>edgar/\S+)\s*$")

# 8-K item names as on the SEC form -> codes (the header file gives names, not codes)
ITEM_NAMES = [
    ("entry into a material definitive agreement", "1.01"), ("termination of a material definitive", "1.02"),
    ("bankruptcy or receivership", "1.03"), ("mine safety", "1.04"), ("cybersecurity", "1.05"),
    ("completion of acquisition or disposition", "2.01"), ("results of operations and financial condition", "2.02"),
    ("creation of a direct financial obligation", "2.03"), ("triggering events that accelerate", "2.04"),
    ("costs associated with exit or disposal", "2.05"), ("material impairments", "2.06"),
    ("notice of delisting", "3.01"), ("unregistered sales of equity", "3.02"),
    ("material modification to rights", "3.03"), ("changes in registrant's certifying accountant", "4.01"),
    ("non-reliance on previously issued", "4.02"), ("changes in control of registrant", "5.01"),
    ("departure of directors or certain officers", "5.02"), ("amendments to articles of incorporation", "5.03"),
    ("submission of matters to a vote", "5.07"), ("regulation fd disclosure", "7.01"),
    ("other events", "8.01"), ("financial statements and exhibits", "9.01"),
]


def _get(url: str, timeout: int = 30) -> requests.Response | None:
    time.sleep(config.SEC_MIN_INTERVAL_SEC)
    try:
        response = requests.get(url, headers=sec_source._headers(), timeout=timeout)
    except requests.RequestException:
        return None
    return response if response.status_code == 200 else None


def daily_index(day: date) -> pd.DataFrame:
    """Every filing made that day; a holiday or a day not yet published returns an empty table"""
    quarter = (day.month - 1) // 3 + 1
    response = _get(INDEX_URL.format(year=day.year, quarter=quarter, day=day.strftime("%Y%m%d")), timeout=60)
    if response is None:
        return pd.DataFrame()
    rows = []
    for line in response.content.decode("latin-1").splitlines():
        match = LINE.match(line)
        if match:
            row = match.groupdict()
            row["cik"] = int(row["cik"])
            row["accession"] = row["path"].rsplit("/", 1)[-1].removesuffix(".txt")
            rows.append(row)
    return pd.DataFrame(rows)


def store_index(day: date, df: pd.DataFrame) -> None:
    if not df.empty:
        storage.write_parquet(df, config.RAW_SEC_INDEX / f"{day.isoformat()}.parquet", kind="sec_index")


def items_8k(cik: int, accession: str) -> list[str]:
    nodash = accession.replace("-", "")
    response = _get(f"{ARCHIVE}edgar/data/{cik}/{nodash}/{accession}-index-headers.html")
    if response is None:
        return []
    names = re.findall(r"ITEM INFORMATION:\s*([^<\n]+)", response.text)
    codes = []
    for name in names:
        lowered = name.strip().lower()
        code = next((c for key, c in ITEM_NAMES if key in lowered), None)
        if code and code not in codes:
            codes.append(code)
    return codes


def _tag(block: str, tag: str) -> str:
    match = re.search(rf"<{tag}>\s*(?:<value>)?\s*([^<]*)", block)
    return html.unescape(match.group(1).strip()) if match else ""


def form4(path: str) -> dict[str, Any] | None:
    """Parse a Form 4 into structure: issuer, insider (role), and each transaction"""
    response = _get(ARCHIVE + path)
    if response is None:
        return None
    text = response.text
    xml = text[text.find("<ownershipDocument"): text.find("</ownershipDocument>")]
    if not xml:
        return None
    owners = []
    for block in re.findall(r"<reportingOwner>(.*?)</reportingOwner>", xml, re.S):
        owners.append({
            "name": _tag(block, "rptOwnerName"),
            "director": _tag(block, "isDirector") in ("1", "true"),
            "officer": _tag(block, "isOfficer") in ("1", "true"),
            "ten_percent": _tag(block, "isTenPercentOwner") in ("1", "true"),
            "title": _tag(block, "officerTitle"),
        })
    transactions = []
    for block in re.findall(r"<nonDerivativeTransaction>(.*?)</nonDerivativeTransaction>", xml, re.S):
        try:
            shares = float(_tag(block, "transactionShares") or 0)
            price = float(_tag(block, "transactionPricePerShare") or 0)
        except ValueError:
            continue
        transactions.append({
            "code": _tag(block, "transactionCode"),
            "date": _tag(block, "transactionDate"),
            "shares": shares,
            "price": price,
            "value": shares * price,
            "direction": _tag(block, "transactionAcquiredDisposedCode"),
            "plan_10b5_1": "10b5-1" in block,
        })
    # Pre-planned trading (Rule 10b5-1) is scheduled in advance and does not reflect a decision at that moment
    planned = _tag(xml, "aff10b5One") in ("1", "true")
    for tx in transactions:
        tx["plan_10b5_1"] = tx["plan_10b5_1"] or planned
    return {
        "issuer_ticker": _tag(xml, "issuerTradingSymbol").upper(),
        "issuer_cik": int(_tag(xml, "issuerCik") or 0),
        "owners": owners,
        "transactions": transactions,
    }


def collect_day(day: date, universe_ciks: set[int], seen: set[str]) -> dict[str, Any]:
    """Fetch that day's index + 8-K/Form 4 details for universe companies (skipping filings already fetched)"""
    index = daily_index(day)
    if index.empty:
        return {"filings": 0, "events": [], "insider": []}
    store_index(day, index)
    mine = index[index["cik"].isin(universe_ciks)]
    events: list[dict[str, Any]] = []
    insider_rows: list[dict[str, Any]] = []

    for row in mine.itertuples():
        if row.accession in seen:
            continue
        form = row.form.strip()
        if form in ("8-K", "8-K/A"):
            codes = items_8k(row.cik, row.accession)
            events.append({"kind": "8-K", "cik": row.cik, "accession": row.accession, "filed": row.filed,
                           "items": codes, "path": row.path, "company": row.company})
        elif "13D" in form.upper():
            events.append({"kind": "13D", "cik": row.cik, "accession": row.accession, "filed": row.filed,
                           "form": form, "path": row.path, "company": row.company})
        elif form == "4":
            parsed = form4(row.path)
            if parsed:
                # A Form 4 filed jointly by several entities (fund + parent + manager) reports "the same transaction"
                # -> one row per transaction with the filer names combined (it used to be one row per filer, inflating purchase totals several times
                # as Claude itself noticed in the first round, 2026-10-25: DOLE appeared 5 times)
                owners = parsed["owners"] or [{}]
                for tx in parsed["transactions"]:
                    insider_rows.append({
                        "accession": row.accession, "filed": row.filed, "issuer_cik": parsed["issuer_cik"],
                        "ticker": parsed["issuer_ticker"],
                        "owner": "; ".join(sorted({o.get("name", "") for o in owners if o.get("name")})),
                        "director": any(o.get("director") for o in owners),
                        "officer": any(o.get("officer") for o in owners),
                        "ten_percent": any(o.get("ten_percent") for o in owners),
                        "title": next((o.get("title") for o in owners if o.get("title")), ""), **tx,
                    })
        seen.add(row.accession)

    if insider_rows:
        storage.write_parquet(pd.DataFrame(insider_rows), config.RAW_INSIDER / f"{day.isoformat()}.parquet",
                              kind="insider")
    if events:
        storage.write_jsonl(events, EVENTS / f"{day.isoformat()}.jsonl", kind="sec_events")
    return {"filings": len(index), "universe_filings": len(mine), "events": events, "insider": insider_rows}


EVENTS = config.RAW / "sec_events"


def seen_accessions() -> set[str]:
    """Accessions already parsed (8-K/13D/Form 4), so they are not fetched again"""
    seen: set[str] = set()
    for path in EVENTS.glob("*.jsonl"):
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                seen.add(json.loads(line)["accession"])
            except (json.JSONDecodeError, KeyError):
                continue
    for path in config.RAW_INSIDER.glob("*.parquet"):
        seen.update(pd.read_parquet(path, columns=["accession"])["accession"])
    return seen


def events_since(since: date) -> list[dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for path in sorted(EVENTS.glob("*.jsonl")):
        if path.stem[:10] < since.isoformat():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            out.setdefault(record["accession"], record)
    return list(out.values())


def insider_history(since: date) -> pd.DataFrame:
    frames = [pd.read_parquet(f) for f in sorted(config.RAW_INSIDER.glob("*.parquet")) if f.stem[:10] >= since.isoformat()]
    if not frames:
        return pd.DataFrame()
    df = pd.concat(frames, ignore_index=True)
    if "ten_percent" not in df.columns:
        df["ten_percent"] = False
    df["ten_percent"] = df["ten_percent"].fillna(False).astype(bool)
    # A second dedup on read: the same transaction in the same document = one entry, however many rows were stored
    keys = ["accession", "code", "date", "shares", "price", "direction"]
    return (df.groupby(keys, as_index=False)
              .agg(filed=("filed", "first"), issuer_cik=("issuer_cik", "first"), ticker=("ticker", "first"),
                   owner=("owner", lambda s: "; ".join(sorted(set("; ".join(s).split("; "))))),
                   director=("director", "max"), officer=("officer", "max"), ten_percent=("ten_percent", "max"),
                   title=("title", "first"), value=("value", "first"), plan_10b5_1=("plan_10b5_1", "max")))


def document_text(path: str, max_chars: int = 6000) -> str:
    """The press release attached to an 8-K (EX-99.1); if there is none, the 8-K itself.

    HTML and tables are stripped down to text, because every character is a token.
    """
    response = _get(ARCHIVE + path, timeout=60)
    if response is None:
        return ""
    documents = re.findall(r"<DOCUMENT>(.*?)</DOCUMENT>", response.text, re.S)
    chosen = next((d for d in documents if re.search(r"<TYPE>EX-99\.?1?\b", d)), None) or (documents[0] if documents else "")
    body = re.sub(r"(?is)<(script|style).*?</\1>", " ", chosen)
    body = re.sub(r"(?s)<[^>]+>", " ", body)
    body = html.unescape(re.sub(r"\s+", " ", body)).strip()
    return body[:max_chars]


def cik_to_tickers() -> dict[int, list[str]]:
    mapping: dict[int, list[str]] = {}
    for ticker, cik in sec_source._ticker_to_cik().items():
        mapping.setdefault(int(cik), []).append(ticker)
    return mapping


def recent_sessions_back(day: date, n: int) -> list[date]:
    days, current = [], day
    while len(days) < n:
        if current.weekday() < 5:
            days.append(current)
        current -= timedelta(days=1)
    return days
