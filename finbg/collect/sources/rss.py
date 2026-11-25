"""News from the RSS feeds of major media.

Why RSS is still worth it in 2026, old as it looks:
- **No API key and almost no rate limit**, unlike every news API
- Very stable; the standard has not changed in 20 years
- News comes **straight from the original outlet**, not through an aggregator that may cut it

The central bank (Fed) feed is especially valuable: a **primary source**
announcing actual policy, not a journalist's interpretation.
"""

from __future__ import annotations

from typing import Any

from ... import config
from . import iso, normalize, tag_tickers

SOURCE_TYPE_BY_KIND = {"outlet": "outlet", "primary": "primary"}


def fetch(feeds: list[dict[str, str]] | None = None) -> list[dict[str, Any]]:
    """Fetch every configured feed; one broken feed does not affect the others"""
    import feedparser

    targets = feeds if feeds is not None else config.RSS_FEEDS
    records: list[dict[str, Any]] = []

    for feed in targets:
        name, url, kind = feed["name"], feed["url"], feed.get("kind", "outlet")
        try:
            parsed = feedparser.parse(url, agent=config.USER_AGENT)
        except Exception as exc:                      # noqa: BLE001
            print(f"  ⚠️  rss/{name}: {type(exc).__name__}")
            continue

        entries = getattr(parsed, "entries", [])
        if not entries:
            print(f"  ⚠️  rss/{name}: ไม่มีรายการ (ฟีดอาจย้ายที่)")
            continue

        for entry in entries[: config.RSS_MAX_PER_FEED]:
            link = entry.get("link", "")
            title = entry.get("title", "")
            if not link or not title:
                continue

            records.append(
                normalize(
                    url=link,
                    title=title,
                    published_at=iso(entry.get("published") or entry.get("updated")),
                    source=f"rss:{name}",
                    source_type=SOURCE_TYPE_BY_KIND.get(kind, "outlet"),
                    publisher=name,
                    # RSS gives no ticker, so it has to be inferred from the headline
                    tickers=tag_tickers(title),
                    extra={"summary": (entry.get("summary", "") or "")[:400]},
                )
            )

    return records
