"""Stage 1 of the Thai track: collect the stock list, prices and news once a day (0 tokens; called by scripts/morning.sh).

Each step can fail independently (the same principle as collect/news.py): if Yahoo is down we still get news, and if a feed breaks we still get prices.
Only one ordering matters: the stock list must come first, because prices and news tagging use it.
"""

from __future__ import annotations

import collections
import json
from datetime import timedelta
from typing import Any, Callable

import pandas as pd

from .. import config, storage
from . import insider, news, prices, universe


def _guarded(step: Callable[..., dict[str, Any]], *args: Any) -> dict[str, Any]:
    try:
        return step(*args)
    except Exception as exc:                              # noqa: BLE001 (one failing step must not stop the others)
        return {"ok": False, "reason": f"{type(exc).__name__}: {exc}"[:200]}


def run(news_hours: int | None = None) -> dict[str, Any]:
    result: dict[str, Any] = {"universe": _guarded(universe.snapshot)}
    symbols = universe.ever_listed()                      # An older snapshot is acceptable if the screener is down today
    if not symbols:
        reason = {"ok": False, "reason": "ยังไม่มีรายชื่อหุ้นในคลังเลย"}
        return {**result, "prices": reason, "news": reason, "insider": reason, "ok": False}
    result["prices"] = _guarded(prices.snapshot, symbols + config.TH_INDEXES)
    result["news"] = _guarded(news.collect, symbols, news_hours)
    result["insider"] = _guarded(insider.collect)         # Must come after the news, because it uses the Form 59 table links from the feed
    result["ok"] = all(step.get("ok") for step in result.values())
    return result


def tradable_now(table: pd.DataFrame) -> set[str]:
    """Stocks really tradable as of the latest stored bar (20-day median traded value, minimum price)"""
    if table.empty:
        return set()
    stocks = table[~table["ticker"].str.startswith("^")]
    value = (stocks["close"] * stocks["volume"]).groupby(stocks["ticker"]).apply(
        lambda s: s.tail(config.TH_SCAN_TRADABLE_DAYS).median())
    last = stocks.sort_values("date").groupby("ticker")["close"].last()
    ok = (value >= config.TH_TRADABLE_MIN_VALUE) & (last.reindex(value.index) >= config.TH_TRADABLE_MIN_PRICE)
    return set(value[ok].index)


def status(days: int = 7) -> dict[str, Any]:
    """What the store holds, to answer "is there enough data to start the next stage" (for example how many stocks the news covers)"""
    out: dict[str, Any] = {}
    snaps = sorted(config.RAW_TH_UNIVERSE.glob("*.parquet"))
    out["universe"] = {"snapshots": len(snaps), "latest": snaps[-1].stem if snaps else None,
                       "common_stocks_now": len(universe.latest()), "ever_listed": len(universe.ever_listed())}

    table = prices.read_all()
    out["prices"] = {"snapshots": len(list(config.RAW_TH_PRICES.glob("*.parquet"))),
                     "tickers": int(table["ticker"].nunique()) if not table.empty else 0,
                     "last_bar": str(table["date"].max().date()) if not table.empty else None}

    since = storage.utc_now() - timedelta(days=days)
    seen: dict[str, dict[str, Any]] = {}
    for path in sorted(config.RAW_TH_NEWS.glob("*.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            record = json.loads(line)
            seen.setdefault(record["url"], record)         # The same story can be in several files (overlapping fetches); count it once
    recent = [r for r in seen.values()
              if r.get("published_at") and pd.Timestamp(r["published_at"]) >= pd.Timestamp(since)]
    mentions = collections.Counter(t for r in recent for t in r["tickers"])
    # The number that decides whether the Thai scanner has enough material: how many really tradable stocks are mentioned per day
    tradable = tradable_now(table)
    per_day: dict[str, set[str]] = collections.defaultdict(set)
    for r in recent:
        day = pd.Timestamp(r["published_at"]).tz_convert(config.LOCAL_TZ).date().isoformat()
        per_day[day] |= set(r["tickers"]) & tradable
    out["news"] = {"unique_items": len(seen), f"last_{days}d": len(recent),
                   f"last_{days}d_tagged": sum(1 for r in recent if r["tickers"]),
                   f"stocks_mentioned_{days}d": len(mentions), "top": mentions.most_common(10),
                   "by_publisher": dict(collections.Counter(r["publisher"] for r in recent)),
                   "tradable_now": len(tradable),
                   "tradable_mentioned_per_day": {d: len(s) for d, s in sorted(per_day.items())}}
    rows = insider.read_all()
    out["insider"] = {"posts_read": len(insider.processed()), "rows": len(rows),
                      "buys": int((rows["method"] == "ซื้อ").sum()) if not rows.empty else 0}
    return out
