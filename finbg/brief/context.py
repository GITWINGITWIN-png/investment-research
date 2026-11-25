"""Build the "context pack": a purely computed summary of the situation from past to present.

**This file never calls an LLM**, on purpose.

The work is split into two layers:
  Layer 1 (this file): gather the facts with plain code; free and exactly repeatable
  Layer 2 (the LLM): interpret and connect; spend tokens only where a model beats code

Letting an LLM compute percentage changes itself burns tokens on work pandas does for free,
and risks getting the arithmetic wrong.
"""

from __future__ import annotations

import json
from typing import Any

import pandas as pd

from .. import config, ledger, storage
from ..collect import calendar as calendar_module
from ..collect import fundamentals as fundamentals_module

# Look-back windows (approximate trading days)
HORIZONS: dict[str, int] = {
    "1d": 1,
    "1w": 5,
    "1m": 21,
    "3m": 63,
    "6m": 126,
    "1y": 252,
}


def _pct_change_over(series: pd.Series, periods: int) -> float | None:
    """Percent change over the last N trading days; None if there is not enough data"""
    clean = series.dropna()
    if len(clean) <= periods:
        return None
    return round((clean.iloc[-1] / clean.iloc[-1 - periods] - 1.0) * 100, 2)


def _ticker_snapshot(series: pd.Series) -> dict[str, Any]:
    """Snapshot of one ticker: last price, returns over several windows, position in the 52-week range"""
    clean = series.dropna()
    if clean.empty:
        return {}

    last = float(clean.iloc[-1])
    snapshot: dict[str, Any] = {
        "last": round(last, 2),
        "as_of": str(clean.index[-1].date()),
        "returns_pct": {
            label: _pct_change_over(clean, periods) for label, periods in HORIZONS.items()
        },
    }

    # Position within the 52-week range shows "cheap or expensive" faster than the raw price
    year = clean.tail(252)
    if len(year) > 20:
        high, low = float(year.max()), float(year.min())
        snapshot["week52"] = {
            "high": round(high, 2),
            "low": round(low, 2),
            "pct_from_high": round((last / high - 1.0) * 100, 2),
            # 0 = low of the year, 100 = high of the year
            "position_pct": round((last - low) / (high - low) * 100, 1) if high > low else None,
        }

    # Above or below the 200-day moving average: the simplest and most widely used trend signal
    if len(clean) >= 200:
        ma200 = float(clean.tail(200).mean())
        snapshot["vs_ma200_pct"] = round((last / ma200 - 1.0) * 100, 2)

    snapshot.update(_quant_signals(clean))
    return snapshot


def _quant_signals(clean: pd.Series) -> dict[str, Any]:
    """Basic quantitative signals, computed in code without tokens.

    - `mom_12_1_pct`: 12-month return skipping the latest month (same definition as ledger.mom_12_1)
    - `vol_60d_pct`: annualised volatility from 60 days of daily returns

    ⚠️ Not yet shown in the harness (finlab) to have predictive power on this universe.
    Treat them as context, not as reliable signals; the weekly plan is told this explicitly.
    """
    out: dict[str, Any] = {}
    if len(clean) > 252:
        out["mom_12_1_pct"] = round((clean.iloc[-22] / clean.iloc[-253] - 1.0) * 100, 2)
    returns = clean.pct_change().dropna()
    if len(returns) >= 60:
        out["vol_60d_pct"] = round(float(returns.tail(60).std() * (252 ** 0.5) * 100), 1)
    return out


def _breadth(prices: pd.DataFrame, tickers: list[str]) -> dict[str, Any]:
    """Market breadth: the percentage of stocks above their 200-day moving average.

    It shows what an index hides: the index can rise on a few large stocks
    while most stocks fall (poor breadth), which is often a warning sign.
    """
    above = 0
    counted = 0
    for ticker in tickers:
        if ticker not in prices.columns:
            continue
        clean = prices[ticker].dropna()
        if len(clean) < 200:
            continue
        counted += 1
        if clean.iloc[-1] > clean.tail(200).mean():
            above += 1

    if counted == 0:
        return {}
    return {
        "above_ma200": above,
        "total": counted,
        "pct": round(above / counted * 100, 1),
    }


def _vix_regime(prices: pd.DataFrame) -> dict[str, Any]:
    """VIX level with its percentile over the past year.

    A raw VIX value is hard to read (is 20 high?); the percentile answers at once.
    """
    if "^VIX" not in prices.columns:
        return {}
    clean = prices["^VIX"].dropna()
    if clean.empty:
        return {}

    last = float(clean.iloc[-1])
    year = clean.tail(252)
    percentile = round((year < last).mean() * 100, 1) if len(year) > 20 else None

    if last < 15:
        regime = "สงบ"
    elif last < 20:
        regime = "ปกติ"
    elif last < 30:
        regime = "ตึงตัว"
    else:
        regime = "ตื่นตระหนก"

    return {"last": round(last, 2), "percentile_1y": percentile, "regime": regime}


def _news_digest(days: int = 3) -> dict[str, Any]:
    """Summary of the latest collected news: counts by query and source, with sample headlines.

    No interpretation here; it only organises the news so layer 2 can read it easily.
    """
    files = sorted(config.RAW_NEWS.glob("*.jsonl"))[-days:]
    if not files:
        return {"available": False}

    records: list[dict[str, Any]] = []
    for path in files:
        with path.open(encoding="utf-8") as fh:
            for line in fh:
                try:
                    records.append(json.loads(line))
                except json.JSONDecodeError:
                    continue

    if not records:
        return {"available": False}

    df = pd.DataFrame(records)
    by_source = df["source"].value_counts().to_dict() if "source" in df else {}
    by_type = df["source_type"].value_counts().to_dict() if "source_type" in df else {}
    by_publisher = df["publisher"].value_counts().head(10).to_dict() if "publisher" in df else {}

    # Headlines put primary sources first: original documents (SEC filings, Fed releases)
    # are legal facts, not interpretation, so they should be read before general news
    headlines: list[dict[str, str]] = []
    if "source_type" in df and "title" in df:
        # Within each type, items tied to the watchlist come first, then by date
        # Reason: the Fed publishes a lot (including notices about small banks that do not concern us)
        # Sorting by date alone would push SEC filings for stocks we hold out of view
        df = df.assign(_relevant=df["tickers"].map(lambda t: 1 if t else 0))
        for source_type in ("primary", "outlet", "aggregator"):
            group = df[df["source_type"] == source_type].sort_values(
                ["_relevant", "published_at"], ascending=[False, False]
            )
            for _, row in group.head(12).iterrows():
                headlines.append({
                    "source_type": source_type,
                    "source": str(row.get("source", "")),
                    "title": str(row.get("title", ""))[:220],
                    "publisher": str(row.get("publisher", "")),
                    "published_at": str(row.get("published_at", ""))[:19],
                    "tickers": list(row.get("tickers") or []),
                    "url": str(row.get("url", "")),
                })

    # Count confirmed tickers only; unconfirmed tickers_hint values are not counted
    ticker_counts: dict[str, int] = {}
    for record in records:
        for ticker in record.get("tickers") or []:
            ticker_counts[ticker] = ticker_counts.get(ticker, 0) + 1

    return {
        "available": True,
        "files_used": [f.name for f in files],
        "total_articles": len(df),
        "by_source": by_source,
        "by_source_type": by_type,
        "top_publishers": by_publisher,
        "ticker_mentions": dict(sorted(ticker_counts.items(), key=lambda kv: -kv[1])),
        "headlines": headlines,
    }


def _ledger_block() -> dict[str, Any]:
    """Status of the baselines that run in parallel: the comparison line for every opinion"""
    nav = ledger.load()
    if nav.empty:
        return {"available": False}

    block: dict[str, Any] = {"available": True, "as_of": str(nav.index[-1].date()), "strategies": {}}
    for column in nav.columns:
        series = nav[column].dropna()
        if series.empty:
            continue
        block["strategies"][column] = {
            "nav": round(float(series.iloc[-1]), 2),
            "returns_pct": {
                label: _pct_change_over(series, periods) for label, periods in HORIZONS.items()
            },
        }
    return block


def _journal_block() -> dict[str, Any]:
    """Open predictions and hypotheses, so the brief reminds us of what we thought earlier"""
    def scan(folder, status_field: str) -> list[dict[str, str]]:
        items: list[dict[str, str]] = []
        for path in sorted(folder.glob("*.md")):
            text = path.read_text(encoding="utf-8")
            meta: dict[str, str] = {}
            if text.startswith("---"):
                _, _, rest = text.partition("---")
                front, _, _ = rest.partition("---")
                for line in front.strip().splitlines():
                    if ":" in line:
                        key, _, value = line.partition(":")
                        meta[key.strip()] = value.strip()
            items.append({
                "file": path.stem,
                "title": next(
                    (l.lstrip("# ").strip() for l in text.splitlines() if l.startswith("# ")),
                    path.stem,
                ),
                status_field: meta.get(status_field, ""),
                "resolve_by": meta.get("resolve_by", ""),
                "confidence": meta.get("confidence", ""),
            })
        return items

    predictions = scan(config.PREDICTIONS, "resolved")
    hypotheses = scan(config.HYPOTHESES, "status")

    return {
        "open_predictions": [p for p in predictions if p.get("resolved", "") in ("", "null", "false")],
        "resolved_predictions": len(predictions) - len(
            [p for p in predictions if p.get("resolved", "") in ("", "null", "false")]
        ),
        "hypotheses_backlog": [h for h in hypotheses if h.get("status", "") == "backlog"],
        "total_hypotheses": len(hypotheses),
    }


def _pm_block() -> dict[str, Any]:
    """Portfolio Manager status: the active plan, performance against the control, latest alerts, predictions"""
    from datetime import timedelta

    from ..pm import plans as plans_module
    from ..pm import portfolio as pm_portfolio
    from ..pm import predictions as pm_predictions

    record = plans_module.latest()
    block: dict[str, Any] = {"status": pm_portfolio.build(), "scorecard": pm_predictions.scorecard(limit=5)}
    if record:
        block["plan"] = {
            "plan_id": record["plan_id"],
            "kind": record["kind"],
            "created_at": record["created_at"],
            "stance": record["plan"].get("regime", {}).get("stance"),
            "thesis": record["plan"].get("thesis", ""),
            "satellite": record["targets"]["satellite"],
            "cash": record["targets"]["cash"],
        }
    cutoff = (storage.utc_now() - timedelta(days=7)).isoformat()
    alerts = []
    for path in sorted(config.ALERTS.glob("alerts-*.jsonl"))[-2:]:
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                alert = json.loads(line)
            except json.JSONDecodeError:
                continue
            if alert.get("at", "") >= cutoff:
                alerts.append({k: alert.get(k) for k in ("at", "severity", "title", "detail")})
    block["alerts_7d"] = alerts[-15:]
    return block


def _opportunities_block() -> dict[str, Any]:
    """Latest whole-market scan, plus the realised results of the idea portfolio against its control group"""
    from ..scan import book as scan_book

    ideas = [json.loads(l) for l in scan_book.IDEAS.read_text(encoding="utf-8").splitlines() if l.strip()] \
        if scan_book.IDEAS.exists() else []
    voided = scan_book.voided_runs()
    ideas = [i for i in ideas if i["created_at"] not in voided]       # Cancelled release rounds are not shown
    latest_run = max((i["created_at"] for i in ideas), default=None)
    latest = [i for i in ideas if i["created_at"] == latest_run]
    return {
        "session": latest[0]["session"] if latest else None,
        "ideas": [{k: i.get(k) for k in ("ticker", "decision", "idea_type", "confidence", "what_happened")}
                  for i in latest],
        "scoreboard": scan_book.scoreboard(),
    }


def _fundamentals_block() -> dict[str, Any]:
    """Latest financials per company (SEC XBRL); P/E uses the latest close"""
    closes = storage.price_matrix("close")
    if closes.empty:
        return {}
    last = closes.ffill().iloc[-1].dropna().to_dict()
    return fundamentals_module.summary(closes=last)


def build() -> dict[str, Any]:
    """Assemble the whole context pack and save it as JSON.

    This file is the *only* input the interpretation layer needs,
    which keeps control over how many tokens go to data.
    """
    storage.ensure_dirs()
    prices = storage.price_matrix("adj_close")
    if prices.empty:
        return {"ok": False, "reason": "ยังไม่มีข้อมูลราคา"}

    as_of = str(prices.index[-1].date())

    pack: dict[str, Any] = {
        "ok": True,
        "as_of": as_of,
        "generated_at": storage.utc_stamp(),
        "benchmarks": {
            ticker: {"label": label, **_ticker_snapshot(prices[ticker])}
            for ticker, label in config.BENCHMARKS.items()
            if ticker in prices.columns
        },
        "sectors": {
            ticker: {"label": label, **_ticker_snapshot(prices[ticker])}
            for ticker, label in config.SECTORS.items()
            if ticker in prices.columns
        },
        "watchlist": {
            ticker: _ticker_snapshot(prices[ticker])
            for ticker in config.WATCHLIST
            if ticker in prices.columns
        },
        "breadth": _breadth(prices, [*config.SECTORS, *config.WATCHLIST]),
        "volatility": _vix_regime(prices),
        "ledger": _ledger_block(),
        "news": _news_digest(),
        "journal": _journal_block(),
        "fundamentals": _fundamentals_block(),
        "earnings_upcoming": calendar_module.upcoming(days=14),
        "pm": _pm_block(),
        "opportunities": _opportunities_block(),
    }

    out_dir = config.DERIVED / "context"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{as_of}.json"
    out_path.write_text(json.dumps(pack, ensure_ascii=False, indent=2), encoding="utf-8")

    pack["path"] = str(out_path.relative_to(config.ROOT))
    return pack


def latest() -> dict[str, Any] | None:
    """Load the most recent context pack"""
    files = sorted((config.DERIVED / "context").glob("*.json"))
    if not files:
        return None
    return json.loads(files[-1].read_text(encoding="utf-8"))
