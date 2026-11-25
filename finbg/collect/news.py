"""Coordinates news collection from several sources.

## Principle

**One failing source must not take the others down with it.**

Each source is called separately and wrapped in try/except, so
even if GDELT bans us for a week, news from RSS and SEC still arrives as usual.

## Call order

From **fastest and most reliable** to slowest, so that some data is always
in hand even when the last sources fail:

    rss (fast) -> sec (medium) -> gdelt (slow, may get rate-limited)

## Deduplication

Several sources often report the same story; without deduplication the labelling step wastes tokens.
Duplicates are removed by URL first, then by normalised headline (different sources often
give different URLs for the same article, for example with tracking parameters appended).

When duplicates are found, **the copy with the better source_type is kept** (primary > outlet > aggregator),
because an original document is more reliable than a report about it.
"""

from __future__ import annotations

import re
from typing import Any, Callable

from .. import config, storage
from .sources import gdelt, rss, sec

# Reliability order, used to decide which copy to keep when a story is duplicated
TYPE_RANK = {"primary": 3, "outlet": 2, "aggregator": 1}

SOURCES: dict[str, Callable[[], list[dict[str, Any]]]] = {
    "rss": rss.fetch,
    "sec": sec.fetch,
    "gdelt": gdelt.fetch,
}
# The public edition has no yahoo or gnews source: robots.txt of query1/query2.finance.yahoo.com
# and news.google.com does not allow bots (checked 2026-11-25); see the data sources section of the README


def _title_key(title: str) -> str:
    """Normalise a headline for comparison: strip punctuation and letter case.

    Used to catch the same story from different sources whose URLs do not match.
    """
    cleaned = re.sub(r"[^\w\s]", "", title.lower())
    return " ".join(cleaned.split())[:90]


def _dedupe(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Deduplicate, keeping the record from the more reliable source"""
    best: dict[str, dict[str, Any]] = {}

    for record in records:
        url = record.get("url", "")
        title = record.get("title", "")
        if not url or not title:
            continue

        # The headline is the main key, because the same story can have different URLs on different sources
        # Except primary documents: SEC filing URLs are always unique, but their headlines can repeat
        # (two 8-Ks from the same company in the same week can have identical headlines)
        # With the headline as the key, one of them would silently disappear; this bug existed before 2026-10-24
        key = url if record.get("source_type") == "primary" else (_title_key(title) or url)
        current = best.get(key)

        if current is None:
            best[key] = record
            continue

        # Merge the tickers from both records whichever one is kept, so no data is lost
        # but keep confirmed tickers and hints separate
        merged = sorted(set(record["tickers"]) | set(current["tickers"]))
        merged_hint = sorted(
            (set(record.get("tickers_hint", [])) | set(current.get("tickers_hint", [])))
            - set(merged)                       # Once confirmed, a ticker no longer needs to be a hint
        )

        winner = (
            record
            if TYPE_RANK.get(record["source_type"], 0) > TYPE_RANK.get(current["source_type"], 0)
            else current
        )
        best[key] = {**winner, "tickers": merged, "tickers_hint": merged_hint}

    return list(best.values())


def collect(sources: list[str] | None = None) -> dict[str, object]:
    """Collect news from every enabled source and save it as one file.

    Counts as **success** if at least one source returned news.
    """
    storage.ensure_dirs()
    enabled = sources or config.NEWS_SOURCES

    collected: list[dict[str, Any]] = []
    per_source: dict[str, int] = {}
    failed: list[str] = []

    for name in enabled:
        fetcher = SOURCES.get(name)
        if fetcher is None:
            print(f"  ⚠️  ไม่รู้จักแหล่ง '{name}' — ข้าม")
            continue

        print(f"  ▸ {name}")
        try:
            found = fetcher()
        except Exception as exc:                       # noqa: BLE001 (stops one failing source from bringing down the whole system)
            print(f"  ⚠️  {name} ล้มเหลว: {type(exc).__name__}: {exc}")
            failed.append(name)
            continue

        per_source[name] = len(found)
        collected.extend(found)
        print(f"    → {len(found)} รายการ")

    if not collected:
        return {
            "ok": False,
            "rows": 0,
            "reason": f"ไม่ได้ข่าวจากแหล่งใดเลย (ลองแล้ว: {', '.join(enabled)})",
            "failed": failed,
        }

    unique = _dedupe(collected)

    stamp = storage.utc_now().strftime("%Y-%m-%d")
    path = storage.write_jsonl(
        unique,
        config.RAW_NEWS / f"{stamp}.jsonl",
        kind="news",
        sources=per_source,
        failed_sources=failed,
    )

    by_type: dict[str, int] = {}
    for record in unique:
        by_type[record["source_type"]] = by_type.get(record["source_type"], 0) + 1

    return {
        "ok": True,
        "rows": len(unique),
        "raw_rows": len(collected),
        "sources_ok": f"{len(per_source)}/{len(enabled)}",
        "by_type": by_type,
        "failed": failed,
        "path": str(path.relative_to(config.ROOT)),
    }
