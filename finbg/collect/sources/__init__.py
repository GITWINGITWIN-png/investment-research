"""Several news sources, designed so that each can fail independently.

## The problem

GDELT used to be the only source, a single point of failure:
one rate limit meant no news at all for the day.

## The real benefit goes beyond a backup

Each source has its own bias, so several sources give a fuller picture:

| Source | Type | Strength | Weakness |
|-------|------|---------|---------|
| GDELT | aggregator | very broad, international, many languages | strict rate limit, no article text |
| RSS | outlet | timely, major media | limited volume, no ticker |
| SEC | **primary** | **original documents with legal weight** | not "news", hard to read |

`source_type` is stored on every record so analysis can weight sources differently:
an 8-K from the SEC is a fact the company must disclose, while an aggregator article
is interpretation, and the two should not count the same.

## Contract every source follows

- `fetch()` returns a `list[dict]` that has gone through `normalize()`
- **never raise**: on failure, return an empty list and print the reason,
  because one source going down must not take the others with it
"""

from __future__ import annotations

import re
from datetime import datetime
from functools import lru_cache
from typing import Any

from ... import config


def normalize(
    *,
    url: str,
    title: str,
    published_at: str | None,
    source: str,
    source_type: str,
    publisher: str = "",
    tickers: list[str] | None = None,
    tickers_hint: list[str] | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Normalise records from every source into one format.

    This matters a lot for the labeled corpus: if each source had its own structure,
    labels could not be compared across sources and the schema would rot as sources were added.

    ## Why tickers and tickers_hint are separate

    A lesson from real data: Yahoo shows "related news" on each ticker's page,
    which **does not mean the story is about that stock**; a P&G story was tagged NVDA.

    Letting wrong links into the store would spoil any text-alpha work built on it,
    so they are split:
      `tickers`      = confirmed (the name really appears in the headline)
      `tickers_hint` = suggested by the source but unconfirmed; a hint only
    """
    return {
        "url": url.strip(),
        "title": (title or "").strip(),
        "published_at": published_at or "",
        "source": source,
        "source_type": source_type,   # aggregator | outlet | primary
        "publisher": publisher,
        "tickers": tickers or [],
        "tickers_hint": tickers_hint or [],
        "extra": extra or {},
    }


def _boundary(term: str) -> str:
    """Regex that matches `term` as a whole word, counting only ASCII letters and digits as "attached".

    ## Why not `\\b`

    Python's `\\b` also counts Chinese and Japanese characters as word characters,
    so the headline "小心Amazon假平台..." has no word boundary around Amazon and never matches,
    which is a real problem because GDELT routinely sends news in many languages.

    Defining the boundary as `[A-Za-z0-9]` fixes both sides at once:
      "Metal"          -> Meta followed by 'l', which is ASCII: no match ✅
      "小心Amazon假"    -> neighbours are not ASCII: match ✅
      "Apple's"        -> followed by ', which is not ASCII alnum: match ✅
    """
    return rf"(?<![A-Za-z0-9]){re.escape(term)}(?![A-Za-z0-9])"


@lru_cache(maxsize=1)
def _ticker_patterns() -> list[tuple[re.Pattern[str], str]]:
    """Compile the regex once; this function runs for every article (hundreds a day).

    It used to be `if ticker in title` (a plain substring), which made META match "Metal"
    and "metals"; a story "This Overlooked Metals Stock..." really was tagged META in the store.
    Short tickers make it worse, because CAT, KO, GE and LIN are all ordinary words.
    """
    patterns: list[tuple[re.Pattern[str], str]] = []

    for ticker in config.WATCHLIST:
        # Tickers that are ordinary English words are matched by company name only
        # ("CAT" in a headline may be a cat, "KO" a knockout, "AMT" an amount)
        # Note: sources that already know the ticker for sure (such as SEC) pass it in `existing`
        # so this exception does not affect them
        if ticker in config.AMBIGUOUS_SYMBOLS:
            continue
        patterns.append((re.compile(_boundary(ticker), re.IGNORECASE), ticker))

    for name, ticker in config.COMPANY_ALIASES.items():
        patterns.append((re.compile(_boundary(name), re.IGNORECASE), ticker))

    return patterns


def tag_tickers(title: str, existing: list[str] | None = None) -> list[str]:
    """Find tickers in a headline, on our side, without spending API quota.

    Uses both tickers and company names, because stories usually say "Nvidia", not "NVDA".
    The result is **confirmed** `tickers`, unlike the `tickers_hint` a news source guesses.
    """
    found = set(existing or [])

    for pattern, ticker in _ticker_patterns():
        if pattern.search(title):
            found.add(ticker)

    return sorted(found)


def iso(value: Any) -> str:
    """Convert dates in several formats to ISO; returns an empty string if conversion fails.

    Each source sends dates differently (epoch, RFC822, GDELT compact, ISO);
    normalising here means nothing else has to know about it.
    """
    if not value:
        return ""

    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(value, tz=config.UTC).replace(microsecond=0).isoformat()
        except (ValueError, OSError):
            return ""

    text = str(value).strip()

    # GDELT: 20260803T060000Z
    if len(text) == 16 and text[8] == "T" and text.endswith("Z"):
        try:
            return datetime.strptime(text, "%Y%m%dT%H%M%SZ").replace(
                tzinfo=config.UTC
            ).isoformat()
        except ValueError:
            pass

    # Already ISO (may end in Z)
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).isoformat()
    except ValueError:
        pass

    # RFC 822 from RSS
    try:
        from email.utils import parsedate_to_datetime
        return parsedate_to_datetime(text).isoformat()
    except (TypeError, ValueError):
        return ""
