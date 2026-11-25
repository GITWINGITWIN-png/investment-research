"""Stage 1 of the Thai scanner: code scores events for really tradable stocks (0 tokens).

## There must be at least one piece of information

The Thai track's hypothesis is that "Claude reads Thai-language information and sees what prices do not yet reflect"; a stock whose price moves
with no news or insider report at all gives nothing to read, so it is not a finalist (price and volume only add to the score).

| Part | Score | Reason |
|---|---|---|
| New news in the window | 1 per item (max 3) + 0.5 per extra outlet (max 1.5) | several outlets on one story = a real story, not a placed article |
| Insider buying in the market (Form 59, 10 days) | >= 2 insiders or >= ฿1M in total = 3; one insider >= ฿200k = 1.5 | buying with one's own money means more than selling |
| Unusual price move | if abs(z) >= 2.5, abs(z)/2 (max 3) | the price says something happened |
| Volume spike | if >= 2.5 times the 20-day median, log2 (max 3) | |

"Overlooked" multiplier: market cap < ฿5bn x1.3, < ฿20bn x1.15, >= ฿200bn x0.5,
no media mentions in the previous 5 days x1.2, >= 10 mentions x0.8.

**Every weight is set by reasoning, not yet tuned on outcomes** (as in the US scanner); tuning on outcomes later must count as a new experiment.

## News time window

News published after the previous trading day's close (17:00) up to the decision time; part of it is already in the latest close
and the after-hours part is not. Both are on the card with timestamps, so Sonnet and Opus can tell them apart.
"""

from __future__ import annotations

import json
import math
from datetime import datetime, time, timedelta
from typing import Any

import pandas as pd

from .. import config
from . import insider, prices, universe


def _news() -> list[dict[str, Any]]:
    """Every news item in the store; for the same story in several files (overlapping fetches), the first seen is kept"""
    seen: dict[str, dict[str, Any]] = {}
    for path in sorted(config.RAW_TH_NEWS.glob("*.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            item = json.loads(line)
            if item.get("published_at"):
                seen.setdefault(item["url"], item)
    return list(seen.values())


def candidates(session: pd.Timestamp, decided_at: datetime) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    table = prices.read_all()
    stocks = table[~table["ticker"].str.startswith("^")]
    wide = {f: stocks.pivot(index="date", columns="ticker", values=f).sort_index().loc[:session]
            for f in ("close", "adj_close", "volume")}
    close, adj, volume = wide["close"], wide["adj_close"], wide["volume"]
    previous = adj.index[-2]

    value = (close * volume).iloc[-config.TH_SCAN_TRADABLE_DAYS:].median()
    tradable = value[(value >= config.TH_TRADABLE_MIN_VALUE) & (close.iloc[-1] >= config.TH_TRADABLE_MIN_PRICE)
                     & (volume.iloc[-1] > 0)].index
    returns = adj.pct_change(fill_method=None)
    sigma = returns.iloc[-61:-1].std().where(returns.iloc[-61:-1].count() >= 15)
    z = returns.iloc[-1] / sigma
    volume_ratio = volume.iloc[-1] / volume.iloc[-21:-1].median()

    window_start = datetime.combine(previous.date(), time(17, 0), tzinfo=config.LOCAL_TZ)
    attention_start = window_start - timedelta(days=config.TH_ATTENTION_DAYS)
    fresh: dict[str, list[dict[str, Any]]] = {}
    attention: dict[str, int] = {}
    for item in _news():
        when = pd.Timestamp(item["published_at"]).to_pydatetime()
        for ticker in item["tickers"]:
            if window_start < when <= decided_at:
                fresh.setdefault(ticker, []).append(item)
            elif attention_start <= when <= window_start:
                attention[ticker] = attention.get(ticker, 0) + 1

    buys: dict[str, list[dict[str, Any]]] = {}
    rows = insider.read_all()
    if not rows.empty:
        since = decided_at - timedelta(days=config.TH_INSIDER_LOOKBACK_DAYS)
        recent = rows[(rows["method"] == "ซื้อ") & rows["security_type"].str.startswith("หุ้นสามัญ")
                      & (pd.to_datetime(rows["published_at"], utc=True) >= pd.Timestamp(since))]
        for record in recent.to_dict("records"):
            buys.setdefault(record["ticker"], []).append(record)

    names = universe.latest().set_index("symbol")
    out = []
    for ticker in tradable:
        news_items = sorted(fresh.get(ticker, []), key=lambda i: i["published_at"], reverse=True)
        bought = buys.get(ticker, [])
        if not news_items and not bought:
            continue                                     # Nothing to read, so it is outside this track's hypothesis
        parts: dict[str, float] = {}
        reasons: list[str] = []
        if news_items:
            publishers = {i["publisher"] for i in news_items}
            parts["news"] = min(len(news_items), 3) + min(0.5 * (len(publishers) - 1), 1.5)
            reasons.append(f"ข่าว {len(news_items)} ชิ้น ({len(publishers)} สำนัก)")
        if bought:
            buyers = {b["executive"] for b in bought}
            total = sum(b["value_thb"] or 0 for b in bought)
            if len(buyers) >= config.TH_INSIDER_CLUSTER_MIN_BUYERS or total >= config.TH_INSIDER_BIG_BUY_THB:
                parts["insider"] = 3.0
            elif total >= config.TH_INSIDER_SINGLE_BUY_THB:
                parts["insider"] = 1.5
            reasons.append(f"ผู้บริหารซื้อ {len(buyers)} คน ฿{total / 1e6:.2f}M")
        zt, vr = z.get(ticker), volume_ratio.get(ticker)
        if zt is not None and not math.isnan(zt) and abs(zt) >= 2.5:
            parts["price"] = min(abs(zt) / 2, 3.0)
            reasons.append(f"ราคา {returns.iloc[-1][ticker] * 100:+.1f}% (z {zt:+.1f})")
        if vr is not None and not math.isnan(vr) and vr >= 2.5:
            parts["volume"] = min(math.log2(vr), 3.0)
            reasons.append(f"volume {vr:.1f} เท่า")

        cap = names["market_cap"].get(ticker)
        multiplier = 1.0
        if cap and not math.isnan(cap):
            multiplier *= 1.3 if cap < 5e9 else 1.15 if cap < 20e9 else 0.5 if cap >= 200e9 else 1.0
        seen_before = attention.get(ticker, 0)
        multiplier *= 1.2 if seen_before == 0 else 0.8 if seen_before >= 10 else 1.0
        out.append({
            "ticker": ticker, "name": str(names["name"].get(ticker, ticker)), "market_cap": cap,
            "traded_value": float(value[ticker]), "close": float(close.iloc[-1][ticker]),
            "ret_pct": float(returns.iloc[-1][ticker] * 100) if not math.isnan(returns.iloc[-1][ticker]) else None,
            "z": None if zt is None or math.isnan(zt) else float(zt),
            "volume_ratio": None if vr is None or math.isnan(vr) else float(vr),
            "attention_5d": seen_before, "news": news_items[:8], "insider": bought,
            "score": round(sum(parts.values()) * multiplier, 2), "parts": parts, "reasons": reasons,
        })
    out.sort(key=lambda c: -c["score"])
    summary = {"session": str(session.date()), "tradable": len(tradable), "with_news": len(fresh.keys() & set(tradable)),
               "with_insider_buys": len(buys.keys() & set(tradable)), "events": len(out),
               "window": f"{window_start:%Y-%m-%d %H:%M} → {decided_at.astimezone(config.LOCAL_TZ):%Y-%m-%d %H:%M}"}
    return out[:config.TH_SCAN_CANDIDATES], summary
