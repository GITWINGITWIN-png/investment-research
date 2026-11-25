"""Thai stock news from media RSS: fetched once a day, paging back through each feed until 30 hours are covered.

## Why page back through the feed

RSS returns only the latest 10 to 30 items per page, but Thai stock media publish hundreds of items a day
(Kaohoon publishes about 10 an hour on trading days), so fetching only the first page once a day would get under 10%.
The simple fix would be hourly fetching, but the owner did not want hourly jobs; WordPress feeds support ?paged=N,
so we fetch once a day and page back until items older than 30 hours appear, which gives the same coverage
(overlapping the previous round by about 6 hours in case the machine starts late; duplicates across files are removed by the reader, as the store requires).

## Tagging tickers from headlines

Thai stock media write tickers in uppercase Latin letters inside Thai sentences ("ADVANC กำไรโต...").
- Uses the same `_boundary` as the US side: Thai letters count as word boundaries, so tickers attached to Thai words match
- **Case-sensitive** (unlike the US side): "true" in an English sentence is not the stock TRUE
- Tickers that are also common words (AI, CPI, META...) go into `tickers_hint` instead of `tickers`,
  as on the US side: `tickers` = confirmed, `tickers_hint` = maybe, to be confirmed before use; see config.TH_AMBIGUOUS_SYMBOLS
"""

from __future__ import annotations

import re
import time
from datetime import datetime, timedelta
from typing import Any

from .. import config, storage
from ..collect.sources import _boundary, iso, normalize


Patterns = list[tuple[re.Pattern[str], str, bool]]      # (regex, symbol, whether it is confirmable)


def ticker_patterns(symbols: list[str]) -> Patterns:
    patterns = []
    for symbol in symbols:
        base = symbol.removesuffix(".BK")
        if len(base) < 2:
            continue                                     # One-letter tickers (such as M) match wrongly in almost every headline
        patterns.append((re.compile(_boundary(base)), symbol, base not in config.TH_AMBIGUOUS_SYMBOLS))
    return patterns


def tag(title: str, patterns: Patterns) -> tuple[list[str], list[str]]:
    """Return (confirmed tickers, possible tickers_hint)"""
    confirmed, hints = set(), set()
    for pattern, symbol, sure in patterns:
        if pattern.search(title):
            (confirmed if sure else hints).add(symbol)
    return sorted(confirmed), sorted(hints)


def _parse_time(value: str) -> datetime | None:
    try:
        return datetime.fromisoformat(value) if value else None
    except ValueError:
        return None


def fetch_feed(feed: dict[str, str], cutoff: datetime, patterns: Patterns,
               max_pages: int = config.TH_NEWS_MAX_PAGES) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Page back through a feed until the oldest item on a page is older than the cutoff; returns (items, statistics for this feed)"""
    import feedparser

    found: dict[str, dict[str, Any]] = {}                 # key = URL; pages can shift if new items arrive during the fetch
    pages, reached = 0, False
    for page in range(1, max_pages + 1):
        url = feed["url"] if page == 1 else f"{feed['url']}?paged={page}"
        try:
            parsed = feedparser.parse(url, agent=config.USER_AGENT)
        except Exception as exc:                          # noqa: BLE001 (one broken feed must not affect the others)
            print(f"  ⚠️  th-rss/{feed['name']} หน้า {page}: {type(exc).__name__}")
            break
        entries = getattr(parsed, "entries", [])
        if not entries:
            if page == 1:
                print(f"  ⚠️  th-rss/{feed['name']}: ไม่มีรายการ (feed อาจย้ายที่)")
            break
        pages += 1
        oldest = None
        for entry in entries:
            link, title = entry.get("link", ""), (entry.get("title", "") or "").strip()
            if not link or not title:
                continue
            published = iso(entry.get("published") or entry.get("updated"))
            when = _parse_time(published)
            if when is not None:
                oldest = when if oldest is None else min(oldest, when)
            confirmed, hints = tag(title, patterns)
            found.setdefault(link, normalize(
                url=link, title=title, published_at=published, source=f"th-rss:{feed['name']}",
                source_type="outlet", publisher=feed["name"], tickers=confirmed, tickers_hint=hints,
                extra={"summary": (entry.get("summary", "") or "")[:400],
                       "categories": [t.get("term", "") for t in entry.get("tags", []) if t.get("term")]},
            ))
        if oldest is not None and oldest < cutoff:
            reached = True
            break
        time.sleep(config.TH_NEWS_PAGE_DELAY_SEC)
    return list(found.values()), {"pages": pages, "items": len(found), "reached_cutoff": reached}


def collect(symbols: list[str], hours: int | None = None) -> dict[str, Any]:
    """Every feed -> one JSONL file per run (raw data as seen at that time).

    `hours` can reach further back than usual (once, at the start, to measure coverage without waiting two weeks for news to accumulate).
    Backfilled items carry their real publication time, so they can measure coverage, but the scanner uses only news inside each round's window.
    """
    lookback = hours or config.TH_NEWS_LOOKBACK_HOURS
    cutoff = storage.utc_now() - timedelta(hours=lookback)
    max_pages = config.TH_NEWS_MAX_PAGES if hours is None else max(config.TH_NEWS_MAX_PAGES, hours * 2)
    patterns = ticker_patterns(symbols)
    records: list[dict[str, Any]] = []
    stats: dict[str, Any] = {}
    for feed in config.TH_NEWS_FEEDS:
        items, stats[feed["name"]] = fetch_feed(feed, cutoff, patterns, max_pages)
        records += items
    if not records:
        return {"ok": False, "reason": "ไม่ได้ข่าวจาก feed ใดเลย", "feeds": stats}
    day = storage.utc_now().astimezone(config.LOCAL_TZ).date().isoformat()
    path = storage.write_jsonl(records, config.RAW_TH_NEWS / f"{day}.jsonl", kind="th_news", feeds=stats,
                               lookback_hours=lookback)
    tagged = sum(1 for r in records if r["tickers"])
    return {"ok": True, "items": len(records), "tagged": tagged, "feeds": stats, "path": str(path.relative_to(config.ROOT))}
