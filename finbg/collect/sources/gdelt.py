"""News from the GDELT DOC 2.0 API.

Strength: the widest coverage, world media in many languages, free, no key.

Weaknesses found in real tests, which are why other sources exist:
- **Rate limits are stricter than documented**: requests 6 seconds apart still get HTTP 429
- Once limited, it stays blocked for tens of minutes even after requests stop
- Only metadata and URLs, no article text (and the URLs rot over time)

For large volumes GDELT recommends BigQuery instead of the DOC API.
"""

from __future__ import annotations

import json
import time
from typing import Any

import requests

from ... import config
from . import iso, normalize, tag_tickers

SOURCE = "gdelt"
SOURCE_TYPE = "aggregator"

_last_request_at: float = 0.0


def _throttled_get(params: dict[str, str]) -> requests.Response:
    """Send a request, always keeping the configured spacing"""
    global _last_request_at

    elapsed = time.monotonic() - _last_request_at
    if elapsed < config.GDELT_MIN_INTERVAL_SEC:
        time.sleep(config.GDELT_MIN_INTERVAL_SEC - elapsed)

    response = requests.get(
        config.GDELT_ENDPOINT,
        params=params,
        headers={"User-Agent": config.USER_AGENT},
        timeout=config.GDELT_TIMEOUT_SEC,
    )
    _last_request_at = time.monotonic()
    return response


def _query(search: str, timespan: str, deadline: float) -> list[dict[str, Any]]:
    """Fetch articles for one query; returns an empty list on failure.

    GDELT signals a rate limit in two ways: HTTP 429,
    or HTTP 200 with a warning message instead of JSON in the body; both must be checked.
    """
    params = {
        "query": search,
        "mode": "artlist",
        "maxrecords": str(config.GDELT_MAX_RECORDS),
        "timespan": timespan,
        "format": "json",
        "sort": "datedesc",
    }

    for attempt in range(config.GDELT_RETRY_ON_LIMIT + 1):
        if time.monotonic() > deadline:
            print("  ⏱️  gdelt: หมดเวลาที่จัดสรรให้")
            return []

        try:
            response = _throttled_get(params)
        except requests.RequestException as exc:
            print(f"  ⚠️  gdelt: เชื่อมต่อไม่สำเร็จ ({type(exc).__name__})")
            return []

        body = response.text.strip()
        if response.status_code == 429 or not body.startswith("{"):
            if attempt >= config.GDELT_RETRY_ON_LIMIT:
                print("  ⚠️  gdelt: rate limit — ข้าม (พรุ่งนี้ลองใหม่)")
                return []
            print(f"  ⏳ gdelt: rate limit → รอ {config.GDELT_BACKOFF_SEC:.0f}s")
            time.sleep(config.GDELT_BACKOFF_SEC)
            continue

        if response.status_code != 200:
            print(f"  ⚠️  gdelt: HTTP {response.status_code}")
            return []

        try:
            return json.loads(body).get("articles", [])
        except json.JSONDecodeError:
            print("  ⚠️  gdelt: JSON เสีย")
            return []

    return []


def fetch(timespan: str = "1d") -> list[dict[str, Any]]:
    """Fetch the configured set of queries, with a circuit breaker and a time limit"""
    searches = [*config.NEWS_QUERIES, config.watchlist_news_query()]
    deadline = time.monotonic() + config.GDELT_TIME_BUDGET_SEC
    records: list[dict[str, Any]] = []

    for i, search in enumerate(searches, start=1):
        articles = _query(search, timespan, deadline)

        for article in articles:
            url = article.get("url", "")
            title = article.get("title", "")
            if not url or not title:
                continue
            records.append(
                normalize(
                    url=url,
                    title=title,
                    published_at=iso(article.get("seendate")),
                    source=SOURCE,
                    source_type=SOURCE_TYPE,
                    publisher=article.get("domain", ""),
                    tickers=tag_tickers(title),
                    extra={
                        "query": search,
                        "language": article.get("language", ""),
                        "country": article.get("sourcecountry", ""),
                    },
                )
            )

        # Circuit breaker: if the first query fails we are in the penalty box,
        # and every further request would fail too, so stop at once instead of wasting time
        if i == 1 and not articles:
            print("  🔌 gdelt: คำค้นแรกไม่ผ่าน — หยุดทั้งรอบ")
            break

    return records
