"""Form 59 table: executives of listed companies trading their own company's shares (the equivalent of the US Form 4).

## Why it matters

Executives who buy shares in the market with their own money often see something the market has not; US research finds the strongest signal
in small stocks and when several insiders buy at once (the US scanner weights this in finbg/scan/score.py).
Sales mean much less (people sell for a hundred reasons), so only purchases of common shares count.

## Source: InfoQuest's daily articles

The Thai SEC offers no automated channel (sec.or.th blocks programs; robots.txt forbids the idisc pages).
InfoQuest publishes the table as a news item every trading day; the feed gives the headline and link (news.py already stores them).
This file opens that link about once a day through articles.fetch_html (robots check + pacing) and parses the table into entries.

## Careful: dates in the table use the Buddhist era

"29/10/2569" = 29 Oct 2026; convert when storing, or anything reading the store will think it really is the year 2569.
"""

from __future__ import annotations

import json
from datetime import date
from typing import Any

import pandas as pd

from .. import config, storage
from . import articles

_HEADERS = {"ชื่อบริษัท": "company", "ชื่อผู้บริหาร": "executive", "ความสัมพันธ์": "relationship",
            "ประเภทหลักทรัพย์": "security_type", "วันที่ได้มา": "trade_date", "จำนวน": "quantity",
            "ราคา": "price", "วิธีการได้มา": "method"}


def thai_date(text: str) -> str | None:
    try:
        day, month, year = (int(part) for part in text.strip().split("/"))
        return date(year - 543 if year > 2400 else year, month, day).isoformat()
    except (ValueError, TypeError):
        return None


def _number(text: str) -> float | None:
    cleaned = text.replace(",", "").strip()
    try:
        return float(cleaned)
    except ValueError:
        return None


def parse(html: str) -> list[dict[str, Any]]:
    """Every row of a table whose column headers match the Form 59 table (headers may end with *, so they are matched by their first words)"""
    from bs4 import BeautifulSoup

    rows: list[dict[str, Any]] = []
    for table in BeautifulSoup(html, "html.parser").find_all("table"):
        lines = table.find_all("tr")
        if not lines:
            continue
        header = [c.get_text(" ", strip=True) for c in lines[0].find_all(["th", "td"])]
        keys = [next((key for prefix, key in _HEADERS.items() if h.startswith(prefix)), None) for h in header]
        if "company" not in keys or "method" not in keys:
            continue
        for line in lines[1:]:
            cells = [c.get_text(" ", strip=True) for c in line.find_all(["td", "th"])]
            record = {key: value for key, value in zip(keys, cells) if key}
            company = (record.get("company") or "").strip().upper()
            if not company:
                continue
            quantity, price = _number(record.get("quantity", "")), _number(record.get("price", ""))
            rows.append({
                "company": company, "ticker": f"{company}.BK",
                "executive": record.get("executive", ""), "relationship": record.get("relationship", ""),
                "security_type": record.get("security_type", ""), "trade_date": thai_date(record.get("trade_date", "")),
                "quantity": quantity, "price": price,
                "value_thb": quantity * price if quantity is not None and price is not None else None,
                "method": record.get("method", ""),
            })
    return rows


def posts() -> list[dict[str, Any]]:
    """Every Form 59 news item ever seen in the feed (from news.py's news store)"""
    found: dict[str, dict[str, Any]] = {}
    for path in sorted(config.RAW_TH_NEWS.glob("*.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            item = json.loads(line)
            if item.get("publisher") == "infoquest" and config.TH_FORM59_TITLE in item.get("title", ""):
                found.setdefault(item["url"], item)
    return sorted(found.values(), key=lambda i: i.get("published_at", ""))


def processed() -> set[str]:
    """Links already read successfully, never fetched again (links that failed because of the network or a site outage are retried next round)"""
    done: set[str] = set()
    for path in sorted(config.RAW_TH_INSIDER.glob("posts-*.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            record = json.loads(line)
            if record.get("status") in ("ok", "no_table"):
                done.add(record["url"])
    return done


def collect(limit: int = config.TH_FORM59_PER_RUN) -> dict[str, Any]:
    pending = [p for p in posts() if p["url"] not in processed()][-limit:]   # Newest first, if there is a large backlog
    if not pending:
        return {"ok": True, "posts": 0, "rows": 0}
    log, frames = [], []
    for post in pending:
        html, status = articles.fetch_html(post["url"])
        rows = parse(html) if html else []
        if html and not rows:
            status = "no_table"
        log.append({"url": post["url"], "title": post["title"], "published_at": post.get("published_at", ""),
                    "status": status, "rows": len(rows)})
        if rows:
            frames.append(pd.DataFrame(rows).assign(report=post["title"], report_url=post["url"],
                                                    published_at=post.get("published_at", "")))
    day = storage.utc_now().astimezone(config.LOCAL_TZ).date().isoformat()
    storage.write_jsonl(log, config.RAW_TH_INSIDER / f"posts-{day}.jsonl", kind="th_form59_posts")
    total = 0
    if frames:
        table = pd.concat(frames, ignore_index=True)
        total = len(table)
        storage.write_parquet(table, config.RAW_TH_INSIDER / f"{day}.parquet", kind="th_form59",
                              source="infoquest", posts=len(frames))
    return {"ok": all(entry["status"] in ("ok", "no_table") for entry in log), "posts": len(log), "rows": total,
            "statuses": [entry["status"] for entry in log]}


def read_all() -> pd.DataFrame:
    files = sorted(config.RAW_TH_INSIDER.glob("*.parquet"))
    if not files:
        return pd.DataFrame()
    df = pd.concat([pd.read_parquet(f) for f in files], ignore_index=True)
    # The same report may be fetched twice (for example a retry after a failure); count identical rows once
    key = ["report_url", "company", "executive", "trade_date", "quantity", "price", "method"]
    return df.drop_duplicates(key).reset_index(drop=True)
