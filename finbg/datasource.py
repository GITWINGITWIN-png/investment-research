"""Where market data comes from in the public edition.

The private version of this project read prices, earnings dates and the Thai
stock list from Yahoo Finance through the yfinance library. Yahoo's
query1/query2.finance.yahoo.com disallow every path for automated clients in
robots.txt (checked 2026-11-25), so this public copy ships without that access.

Every former `import yfinance as yf` now imports `yf` from here, and any use of
it stops with the explanation below until a data source you are allowed to use
is plugged in. To plug one in, replace `yf` (and `EquityQuery`) with an object
that offers the calls the code makes: `download` and `Ticker` for US prices and
earnings dates, and `screen` with `EquityQuery` for the Thai stock list. Prices
are stored the same way whatever the source (see finbg/collect/prices.py), so
nothing else has to change.
"""

from __future__ import annotations

from typing import Any

MESSAGE = (
    "Market data source not included in the public edition: Yahoo Finance "
    "(query1/query2.finance.yahoo.com) disallows automated access in robots.txt. "
    "Plug a data source you are allowed to use into finbg/datasource.py."
)


class SourceNotIncluded(RuntimeError):
    """Raised when code asks for a data source this public copy does not ship."""


class _NotIncluded:
    """Stands in for the yfinance module: any use stops with MESSAGE."""

    def __getattr__(self, name: str) -> Any:
        raise SourceNotIncluded(MESSAGE)


yf: Any = _NotIncluded()


class EquityQuery:
    """Stands in for yfinance.EquityQuery (used by the Thai stock list)."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        raise SourceNotIncluded(MESSAGE)
