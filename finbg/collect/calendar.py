"""Earnings calendar and analyst estimates (daily snapshot).

## Why collect every day when earnings dates rarely change

The valuable part is the **estimates** (consensus EPS and revenue), which are overwritten every day,
and no free source keeps point-in-time history of them; what is not saved today is gone for good
(see note 10, "History cannot be collected later").

Uses:
- The weekly plan needs to know which stocks report next week (events that can make prices jump)
- Later: "earnings surprise" = actual results against the estimate on the day before the report
"""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from typing import Any

from .. import config, storage


def _jsonable(value: Any) -> Any:
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, list):
        return [_jsonable(v) for v in value]
    if isinstance(value, float) and value != value:     # NaN
        return None
    if hasattr(value, "item"):                          # numpy scalar
        return value.item()
    return value


def collect(tickers: list[str] | None = None) -> dict[str, Any]:
    """Fetch the calendar for every watchlist stock and save it as one snapshot file"""
    from ..datasource import yf
    storage.ensure_dirs()
    targets = tickers or config.WATCHLIST
    rows: list[dict[str, Any]] = []
    failed: list[str] = []

    for ticker in targets:
        try:
            calendar = yf.Ticker(ticker).calendar or {}
        except Exception as exc:                       # noqa: BLE001 (one failure must not spread to the others)
            print(f"  ⚠️  calendar/{ticker}: {type(exc).__name__}")
            failed.append(ticker)
            continue
        if not calendar:
            failed.append(ticker)
            continue
        dates = calendar.get("Earnings Date") or []
        if not isinstance(dates, list):
            dates = [dates]
        rows.append({
            "ticker": ticker,
            "earnings_dates": [_jsonable(d) for d in dates],
            "eps_avg": _jsonable(calendar.get("Earnings Average")),
            "eps_low": _jsonable(calendar.get("Earnings Low")),
            "eps_high": _jsonable(calendar.get("Earnings High")),
            "revenue_avg": _jsonable(calendar.get("Revenue Average")),
            "ex_dividend": _jsonable(calendar.get("Ex-Dividend Date")),
        })

    if not rows:
        return {"ok": False, "reason": "ไม่ได้ปฏิทินของหุ้นตัวไหนเลย"}

    stamp = storage.utc_now().strftime("%Y-%m-%d")
    path = storage.write_jsonl(rows, config.RAW_CALENDAR / f"{stamp}.jsonl",
                               kind="calendar", failed=failed)
    return {"ok": True, "tickers": len(rows), "failed": len(failed),
            "path": str(path.relative_to(config.ROOT))}


def latest() -> dict[str, dict[str, Any]]:
    """Latest snapshot: ticker -> calendar data"""
    files = sorted(config.RAW_CALENDAR.glob("*.jsonl"))
    if not files:
        return {}
    out: dict[str, dict[str, Any]] = {}
    with files[-1].open(encoding="utf-8") as fh:
        for line in fh:
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            out[record["ticker"]] = record
    return out


def upcoming(days: int = 14, today: date | None = None) -> list[dict[str, Any]]:
    """Stocks reporting within the next `days` days, sorted by date"""
    today = today or storage.utc_now().date()
    horizon = today + timedelta(days=days)
    events: list[dict[str, Any]] = []
    for ticker, record in latest().items():
        for raw in record.get("earnings_dates") or []:
            try:
                when = date.fromisoformat(str(raw)[:10])
            except ValueError:
                continue
            if today <= when <= horizon:
                events.append({"ticker": ticker, "date": when.isoformat(),
                               "eps_avg": record.get("eps_avg"),
                               "revenue_avg": record.get("revenue_avg")})
                break
    return sorted(events, key=lambda e: (e["date"], e["ticker"]))
