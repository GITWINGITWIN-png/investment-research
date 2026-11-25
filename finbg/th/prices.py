"""Daily prices of Thai stocks: a point-in-time snapshot every day (the same as the US side in collect/prices.py).

## Only closed bars are stored

SET closes around 16:40 after the closing auction; before 17:00 Thai time, today's bar is an intraday price
and must be dropped, or anything reading the store would unknowingly use a "close" that is not final.

## Fetch every stock that was ever listed, not only today's list

Stocks just suspended or delisted should still have their final prices in the store; this is exactly the data free sources
lose, which makes backtests look better than reality. If Yahoo no longer returns a price, the failure is simply recorded.
"""

from __future__ import annotations

import time
from datetime import datetime
from typing import Any

import pandas as pd

from .. import config, storage
from ..collect.prices import _tidy


def drop_incomplete(df: pd.DataFrame, now: datetime | None = None) -> tuple[pd.DataFrame, int]:
    """Drop today's bar if SET has not closed yet (before 17:00 Thai time); returns (table, number of rows dropped)"""
    local = (now or storage.utc_now()).astimezone(config.LOCAL_TZ)
    today = pd.Timestamp(local.date())
    if local.hour >= config.TH_BAR_FINAL_HOUR:
        keep = df["date"] <= today
    else:
        keep = df["date"] < today
    return df[keep].reset_index(drop=True), int((~keep).sum())


def download(symbols: list[str], period: str) -> tuple[pd.DataFrame, list[str]]:
    """Fetch in chunks (TH_CHUNK tickers) with pauses between chunks; returns (long table, tickers that failed)"""
    from ..datasource import yf
    frames, failed = [], []
    for start in range(0, len(symbols), config.TH_CHUNK):
        batch = symbols[start:start + config.TH_CHUNK]
        raw = yf.download(batch, period=period, auto_adjust=False, progress=False, threads=True)
        tidy = _tidy(raw, batch)
        got = set(tidy["ticker"].unique()) if not tidy.empty else set()
        failed += sorted(set(batch) - got)
        if not tidy.empty:
            frames.append(tidy)
        time.sleep(2.0)
    return (pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()), failed


def snapshot(symbols: list[str]) -> dict[str, Any]:
    """About one month of bars for every stock -> one file per run (snapshots may overlap; the reader keeps the latest fetched value)"""
    df, failed = download(symbols, config.TH_PRICE_PERIOD)
    if df.empty:
        return {"ok": False, "reason": "ไม่ได้ราคาเลย", "failed": len(failed)}
    df, dropped = drop_incomplete(df)
    day = storage.utc_now().astimezone(config.LOCAL_TZ).date().isoformat()
    path = storage.write_parquet(df, config.RAW_TH_PRICES / f"{day}.parquet", kind="th_prices",
                                 source="yfinance", tickers=int(df["ticker"].nunique()),
                                 failed=failed, dropped_incomplete=dropped)
    return {"ok": True, "tickers": int(df["ticker"].nunique()), "rows": len(df), "last_bar": str(df["date"].max().date()),
            "failed": len(failed), "dropped_incomplete": dropped, "path": str(path.relative_to(config.ROOT))}


def read_all() -> pd.DataFrame:
    """Merge every snapshot; for repeated dates keep the most recently fetched row (closes can be corrected after the market closes)"""
    files = sorted(config.RAW_TH_PRICES.glob("*.parquet"))
    if not files:
        return pd.DataFrame()
    df = pd.concat([pd.read_parquet(f) for f in files], ignore_index=True)
    df["date"] = pd.to_datetime(df["date"]).dt.normalize()
    return (df.sort_values("fetched_at").drop_duplicates(["ticker", "date"], keep="last")
              .sort_values(["ticker", "date"]).reset_index(drop=True))
