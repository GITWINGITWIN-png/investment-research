"""Data for research: the universe + 10 years of price history.

## Kept apart from finbg's point-in-time store on purpose

finbg's `data/raw/` is the evidence of "what we saw on that day": never edited, never overwritten.
Research price history **can be downloaded again at any time**, so it is stored separately in `data/research/`,
where it can be deleted and rebuilt with no backup needed.

## ⚠️ Known limitation: survivorship and look-ahead in the universe choice

This universe is the companies that are **largest today** (in the order of SEC's company_tickers.json,
roughly by size), tested 10 years back, which amounts to choosing only "winners we already know".
Companies that failed, were acquired or left the index along the way are not in it at all.

Consequence: factors that favour rising stocks (such as momentum) look better than they are,
so the numbers from this universe are an **upper bound**, not an expected value.
The real fix needs data that includes delisted stocks (CRSP/Sharadar); recorded in notes 09 and 11.
"""

from __future__ import annotations

import json
import time
from typing import Any

import pandas as pd

from finbg import config as fconfig
from finbg import storage

RESEARCH = fconfig.DATA / "research"
UNIVERSE_PATH = RESEARCH / "universe.json"
PRICES_PATH = RESEARCH / "prices.parquet"


def build_universe(size: int = 300) -> dict[str, Any]:
    """Choose the `size` largest companies (dropping extra share classes of the same company) + finbg's ETFs"""
    import requests

    from finbg.collect.sources import sec as sec_source

    response = requests.get(sec_source.TICKER_MAP_URL, headers=sec_source._headers(), timeout=30)
    response.raise_for_status()
    seen_cik: set[int] = set()
    tickers: list[str] = []
    for entry in response.json().values():
        if entry["cik_str"] in seen_cik:
            continue                                  # GOOGL/GOOG, BRK-A/BRK-B -> take the first
        symbol = entry["ticker"].upper().replace(".", "-")
        if not symbol.replace("-", "").isalpha():
            continue
        seen_cik.add(entry["cik_str"])
        tickers.append(symbol)
        if len(tickers) >= size:
            break

    universe = {
        "selected_at": storage.utc_stamp(),
        "method": f"top {size} by SEC company_tickers.json order (≈ market cap today)",
        "warning": "survivorship + look-ahead: เลือกจากผู้รอดชีวิตในวันนี้ ผลย้อนหลังเป็นขอบบน",
        "stocks": tickers,
        "etfs": sorted({*fconfig.SECTORS, *fconfig.PM_BROAD_ETFS}),
    }
    RESEARCH.mkdir(parents=True, exist_ok=True)
    UNIVERSE_PATH.write_text(json.dumps(universe, indent=2), encoding="utf-8")
    return universe


def universe() -> dict[str, Any]:
    if not UNIVERSE_PATH.exists():
        return build_universe()
    return json.loads(UNIVERSE_PATH.read_text(encoding="utf-8"))


def download_prices(years: int = 10, chunk: int = 50, refresh: bool = False) -> dict[str, Any]:
    """Download 10 years of prices for the whole universe in chunks (lowers the chance of a rate limit)"""
    from finbg.datasource import yf
    from finbg.collect.prices import _tidy

    u = universe()
    wanted = sorted({*u["stocks"], *u["etfs"]})
    existing = pd.read_parquet(PRICES_PATH) if PRICES_PATH.exists() and not refresh else pd.DataFrame()
    have = set(existing["ticker"].unique()) if not existing.empty else set()
    todo = [t for t in wanted if t not in have]

    frames = [existing] if not existing.empty else []
    failed: list[str] = []
    for start in range(0, len(todo), chunk):
        batch = todo[start:start + chunk]
        raw = yf.download(batch, period=f"{years}y", auto_adjust=False, progress=False, threads=True)
        tidy = _tidy(raw, batch)
        got = set(tidy["ticker"].unique()) if not tidy.empty else set()
        failed.extend(sorted(set(batch) - got))
        if not tidy.empty:
            frames.append(tidy[["date", "ticker", "close", "adj_close", "volume"]])
        time.sleep(2.0)

    if not frames:
        return {"ok": False, "reason": "ไม่ได้ราคาเลย"}
    prices = pd.concat(frames, ignore_index=True).drop_duplicates(["date", "ticker"], keep="last")
    RESEARCH.mkdir(parents=True, exist_ok=True)
    prices.to_parquet(PRICES_PATH, index=False)
    return {"ok": True, "tickers": prices["ticker"].nunique(), "rows": len(prices),
            "downloaded": len(todo) - len(failed), "failed": failed}


def load_prices(field: str = "adj_close", kind: str = "stocks") -> pd.DataFrame:
    """Wide table (date x ticker); kind = stocks | etfs | all.

    Filtered to stock-market trading days (days on which SPY has a price), dropping stocks with too few prices.
    """
    df = pd.read_parquet(PRICES_PATH)
    df["date"] = pd.to_datetime(df["date"])
    wide = df.pivot(index="date", columns="ticker", values=field).sort_index()
    if "SPY" in wide.columns:
        wide = wide.loc[wide["SPY"].notna()]
    u = universe()
    keep = {"stocks": u["stocks"], "etfs": u["etfs"], "all": list(wide.columns)}[kind]
    wide = wide[[t for t in keep if t in wide.columns]]
    return wide.loc[:, wide.notna().sum() >= 252]


def forward_returns(prices: pd.DataFrame, horizon: int) -> pd.DataFrame:
    """Return from the close on day t to day t+h, placed on row t.

    This is the "answer" a factor on day t tries to predict.
    Trap: never use this value as a factor input; it is the future (see factors.lookahead_cheat).
    """
    return prices.shift(-horizon) / prices - 1.0
