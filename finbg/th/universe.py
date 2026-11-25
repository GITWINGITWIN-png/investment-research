"""Thai stock list: a daily snapshot from Yahoo's screener.

## Why store it daily when the list barely changes

Every free source knows only "stocks listed today"; delisted stocks disappear together with their price history,
so Thai-market backtests are always an upper bound (survivorship), and worse than in the US because small stocks are delisted often.
Storing the list and prices daily fixes this **from today on**; the past cannot be fixed, so it has to start early.

## Why not the list from set.or.th

SET's company list files sit under /dat/, which SET's robots.txt closes to programs.

## Picking "common shares of Thai companies" out of 2,300+ securities

The screener returns everything traded on SET: NVDRs (-R), the foreign board (-F), preferred shares (-P),
warrants (-W), DRs of foreign stocks (such as NVDA80 = NVIDIA) and trusts/REITs.
The rule used: the full name must be "Public Company Limited" (a Thai public company) and not a fund;
DRs carry the foreign company's name, so they drop out without guessing from the digits in the symbol.
"""

from __future__ import annotations

import json
import re
import time
from typing import Any

import pandas as pd

from .. import config, storage

_SUFFIX = re.compile(r"-(R|F|P|W\d*)$")
_PCL = re.compile(r"public company|public co\b|\bpcl\b|public limited", re.I)
_FUND = re.compile(r"\bfund\b|reit|real estate investment trust|leasehold|infrastructure|\btrust\b", re.I)


def fetch_quotes() -> list[dict[str, Any]]:
    """Every security Yahoo lists as traded on SET (including mai), 250 at a time as the screener allows"""
    from ..datasource import yf
    from ..datasource import EquityQuery
    query = EquityQuery("eq", ["exchange", "SET"])
    quotes: list[dict[str, Any]] = []
    offset = 0
    while True:
        page = yf.screen(query, size=250, offset=offset, sortField="intradaymarketcap", sortAsc=False)
        quotes += page.get("quotes") or []
        offset += 250
        if offset >= page.get("total", 0) or not page.get("quotes"):
            break
        time.sleep(1.5)
    return quotes


def is_common_stock(quote: dict[str, Any]) -> bool:
    symbol = str(quote.get("symbol", ""))
    if not symbol.endswith(".BK") or _SUFFIX.search(symbol[:-3]):
        return False
    name = quote.get("longName") or quote.get("shortName") or ""
    return bool(_PCL.search(name)) and not _FUND.search(name)


def snapshot() -> dict[str, Any]:
    """Store the whole screener response (every security, not only common shares) as one file per day.

    Every field is kept as received; list and dict fields are converted to JSON text so they fit in parquet.
    """
    quotes = fetch_quotes()
    if not quotes:
        return {"ok": False, "reason": "screener คืนรายชื่อว่าง"}
    df = pd.DataFrame.from_records(quotes)
    for column in df.columns:
        if df[column].map(lambda v: isinstance(v, (list, dict))).any():
            df[column] = df[column].map(lambda v: json.dumps(v, ensure_ascii=False) if isinstance(v, (list, dict)) else v)
    df["is_common_stock"] = [is_common_stock(q) for q in quotes]
    day = storage.utc_now().astimezone(config.LOCAL_TZ).date().isoformat()
    path = storage.write_parquet(df, config.RAW_TH_UNIVERSE / f"{day}.parquet", kind="th_universe",
                                 source="yahoo_screener")
    return {"ok": True, "instruments": len(df), "common_stocks": int(df["is_common_stock"].sum()),
            "path": str(path.relative_to(config.ROOT))}


def latest() -> pd.DataFrame:
    """Thai common shares from the latest snapshot (symbol, name, market cap in baht)"""
    files = sorted(config.RAW_TH_UNIVERSE.glob("*.parquet"))
    if not files:
        return pd.DataFrame(columns=["symbol", "name", "market_cap"])
    df = pd.read_parquet(files[-1])
    df = df[df["is_common_stock"]]
    name = df["longName"].fillna(df.get("shortName"))
    return pd.DataFrame({"symbol": df["symbol"], "name": name, "market_cap": df.get("marketCap")}).reset_index(drop=True)


def ever_listed() -> list[str]:
    """Every common share that appeared in any snapshot, used to keep fetching prices for stocks already delisted"""
    symbols: set[str] = set()
    for path in sorted(config.RAW_TH_UNIVERSE.glob("*.parquet")):
        df = pd.read_parquet(path, columns=["symbol", "is_common_stock"])
        symbols |= set(df.loc[df["is_common_stock"], "symbol"])
    return sorted(symbols)
