"""Fetch daily prices and store them as a snapshot.

Why keep our own copy when yfinance can fetch history anyway:
1. Adjusted close **changes retroactively** with every dividend or split;
   without snapshots there is no way to know "what price we saw on that day".
2. yfinance is an unofficial API and may stop working one day; our own store keeps working.
3. It makes backtests exactly reproducible, because the data does not change underneath us.
"""

from __future__ import annotations

import pandas as pd
from ..datasource import yf
from .. import config, market, storage


def _tidy(raw: pd.DataFrame, tickers: list[str]) -> pd.DataFrame:
    """Turn the yfinance result (nested columns) into a long, readable table.

    yfinance returns MultiIndex columns for several tickers but plain columns for one;
    this function makes both cases look the same.
    """
    if raw.empty:
        return pd.DataFrame()

    if isinstance(raw.columns, pd.MultiIndex):
        # The column levels are (field, ticker); reshape to one row per (date, ticker)
        df = raw.stack(level=-1, future_stack=True).reset_index()
        df.columns = [str(c) for c in df.columns]
        df = df.rename(columns={df.columns[0]: "date", df.columns[1]: "ticker"})
    else:
        df = raw.reset_index()
        df.columns = [str(c) for c in df.columns]
        df = df.rename(columns={df.columns[0]: "date"})
        df["ticker"] = tickers[0]

    rename = {
        "Open": "open",
        "High": "high",
        "Low": "low",
        "Close": "close",
        "Adj Close": "adj_close",
        "Volume": "volume",
    }
    df = df.rename(columns=rename)

    # If yfinance already adjusted the prices, there is no separate adj_close column
    if "adj_close" not in df.columns:
        df["adj_close"] = df["close"]

    keep = ["date", "ticker", "open", "high", "low", "close", "adj_close", "volume"]
    df = df[[c for c in keep if c in df.columns]]

    df["date"] = pd.to_datetime(df["date"]).dt.tz_localize(None).dt.normalize()
    return df.dropna(subset=["close"]).sort_values(["ticker", "date"]).reset_index(drop=True)


def _history_rows() -> dict[str, int]:
    """Number of stored price rows per ticker; used to decide which tickers were never backfilled"""
    df = storage.read_all_prices()
    if df.empty:
        return {}
    return df.groupby("ticker").size().to_dict()


def _backfill_attempted() -> set[str]:
    """Tickers already backfilled (read from the manifest).

    Prevents a daily re-fetch for stocks whose history really is short (for example a recent IPO),
    which return the same rows however often they are fetched.
    """
    done: set[str] = set()
    if not config.MANIFEST.exists():
        return done
    import json
    for line in config.MANIFEST.read_text(encoding="utf-8").splitlines():
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if record.get("kind") == "prices_backfill":
            done.update(record.get("tickers_list") or [])
    return done


def _download(tickers: list[str], period: str) -> pd.DataFrame:
    raw = yf.download(
        tickers,
        period=period,
        auto_adjust=False,
        progress=False,
        threads=True,
    )
    return _tidy(raw, tickers)


def backfill_short_history(min_rows: int = config.PRICE_BACKFILL_MIN_ROWS) -> dict[str, object]:
    """Backfill 10 years for tickers with an unusually short history.

    ## The problem

    The 10-year fetch happens once, when the store is empty; after that only the latest month is fetched.
    Stocks added to the watchlist later (16 of them on 6 August) therefore had only a few months of history,
    so the brief showed 1y and MA200 as "—" and backtests could not use them.

    Price history can always be fetched again later, so this is always repairable, unlike news.
    """
    rows = _history_rows()
    attempted = _backfill_attempted()
    short = [
        t for t in config.all_tickers()
        if rows.get(t, 0) < min_rows and t not in attempted
    ]
    if not short:
        return {"ok": True, "backfilled": 0}

    df = _download(short, "10y")
    df, dropped = market.drop_incomplete_session(df)
    if df.empty:
        return {"ok": False, "reason": f"yfinance คืนข้อมูลว่างสำหรับ {', '.join(short)}"}

    stamp = storage.utc_now().strftime("%Y-%m-%d")
    path = storage.write_parquet(
        df,
        config.RAW_PRICES / f"{stamp}.parquet",
        kind="prices_backfill",
        period="10y",
        tickers=len(short),
        tickers_list=short,
    )
    return {
        "ok": True,
        "backfilled": len(short),
        "rows": len(df),
        "path": str(path.relative_to(config.ROOT)),
    }


def collect(period: str | None = None) -> dict[str, object]:
    """Fetch prices and write one snapshot file per run.

    `period` = None means decide automatically:
    - no data yet: fetch 10 years back (a one-time backfill)
    - data exists: fetch only the latest month (which also catches retroactive adjustments)

    The bar for a session that is still open is always dropped; see market.drop_incomplete_session.
    Then tickers with an unusually short history are backfilled (if any).
    """
    storage.ensure_dirs()
    tickers = config.all_tickers()

    if period is None:
        already_have = any(config.RAW_PRICES.glob("*.parquet"))
        period = "1mo" if already_have else "10y"

    df = _download(tickers, period)
    df, dropped = market.drop_incomplete_session(df)

    if df.empty:
        return {"ok": False, "rows": 0, "reason": "yfinance คืนข้อมูลว่าง"}

    stamp = storage.utc_now().strftime("%Y-%m-%d")
    path = storage.write_parquet(
        df,
        config.RAW_PRICES / f"{stamp}.parquet",
        kind="prices",
        period=period,
        tickers=len(tickers),
        dropped_incomplete=dropped,
    )

    result: dict[str, object] = {
        "ok": True,
        "rows": len(df),
        "tickers": df["ticker"].nunique(),
        "date_min": str(df["date"].min().date()),
        "date_max": str(df["date"].max().date()),
        "path": str(path.relative_to(config.ROOT)),
    }
    if dropped:
        result["dropped_incomplete"] = dropped

    backfill = backfill_short_history()
    if backfill.get("backfilled"):
        result["backfilled"] = backfill["backfilled"]
    elif not backfill.get("ok"):
        result["backfill_note"] = backfill.get("reason")
    return result
