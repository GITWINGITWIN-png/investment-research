"""Paper ledger: baseline strategies that run in parallel all the time (BG-2).

Purpose: 18 months from now, when you say "my strategy is good",
you will have **a comparison line that really ran alongside it**, not a backtest anyone can dispute.

The most important honesty point in this file:
we can compute 10 years back, but **everything before the day collection really started is a backtest**,
and only what comes after is **live**. The two deserve very different weight, so they are kept apart
(`live_start` in the result); otherwise we would fool ourselves into a longer track record than we have.
"""

from __future__ import annotations

import pandas as pd

from . import config, storage


def _monthly_rebalance_dates(index: pd.DatetimeIndex) -> list[pd.Timestamp]:
    """First trading day of each month, used as the rebalancing schedule.

    Monthly because turnover is low (fees do not eat the return) and it is the standard
    in most momentum research, so results compare with the literature.
    """
    frame = pd.DataFrame(index=index)
    return list(frame.groupby([index.year, index.month]).apply(lambda g: g.index[0]))


def trading_calendar(prices: pd.DataFrame) -> pd.DatetimeIndex:
    """Stock-market trading calendar, taken from the days on which SPY has a price.

    Needed because the price table includes BTC-USD, which trades 7 days a week;
    without this filter weekends would count as trading days, distorting day counts and
    the drawdowns of the stock strategies.
    """
    if "SPY" not in prices.columns:
        return prices.index
    return prices["SPY"].dropna().index


def _nav_from_weights(
    prices: pd.DataFrame,
    weights: pd.DataFrame,
    start_capital: float,
) -> pd.Series:
    """Compute NAV from the portfolio weights set for each day.

    Always uses daily returns times **yesterday's** weights.
    Reason: today's weights were decided with data up to yesterday; multiplying today's weights
    by today's returns would be look-ahead bias straight away.

    An assumption to know: holding the weights constant every day equals daily rebalancing,
    which costs fees in reality. They are not counted here, which is acceptable because this is
    a "comparison line", not a strategy we claim has alpha.
    """
    returns = prices.pct_change().fillna(0.0)
    aligned = weights.reindex(returns.index).ffill().fillna(0.0)
    portfolio_returns = (returns * aligned.shift(1).fillna(0.0)).sum(axis=1)
    return start_capital * (1.0 + portfolio_returns).cumprod()


def _buy_hold(prices: pd.DataFrame, ticker: str) -> pd.Series:
    """Buy and hold a single asset: the line every strategy must beat first"""
    if ticker not in prices.columns:
        return pd.Series(dtype=float)
    subset = prices[[ticker]].dropna()
    weights = pd.DataFrame(1.0, index=subset.index, columns=[ticker])
    return _nav_from_weights(subset, weights, config.LEDGER_START_CAPITAL)


def _sixty_forty(prices: pd.DataFrame) -> pd.Series:
    """60% stocks / 40% bonds, rebalanced monthly: a benchmark with risk control"""
    needed = ["SPY", "AGG"]
    if not all(t in prices.columns for t in needed):
        return pd.Series(dtype=float)

    subset = prices.loc[trading_calendar(prices), needed].dropna()
    weights = pd.DataFrame(index=subset.index, columns=needed, dtype=float)

    # Reset weights at the start of each month, then let them drift with prices during the month (ffill)
    for date in _monthly_rebalance_dates(subset.index):
        weights.loc[date] = [0.6, 0.4]

    return _nav_from_weights(subset, weights.ffill(), config.LEDGER_START_CAPITAL)


def _momentum_12_1(prices: pd.DataFrame) -> pd.Series:
    """12-1 momentum on sector ETFs: the best-known classic factor.

    "12-1" = the 12-month return, **skipping the latest month**.
    Why skip it: the 1-month horizon often shows short-term reversal, which masks the momentum signal.
    """
    pool = [t for t in config.MOMENTUM_POOL if t in prices.columns]
    if len(pool) < config.MOMENTUM_TOP_N:
        return pd.Series(dtype=float)

    # Keep only stock-market trading days, so BTC's weekends do not get in
    subset = prices.loc[trading_calendar(prices), pool].dropna(how="all")
    weights = pd.DataFrame(0.0, index=subset.index, columns=pool)

    for date in _monthly_rebalance_dates(subset.index):
        window_end = date - pd.DateOffset(months=1)     # Skip the latest month
        window_start = date - pd.DateOffset(months=12)

        history = subset.loc[:window_end]
        if history.empty:
            continue
        start_prices = subset.loc[:window_start]
        if start_prices.empty:
            continue

        momentum = (history.iloc[-1] / start_prices.iloc[-1] - 1.0).dropna()
        if len(momentum) < config.MOMENTUM_TOP_N:
            continue

        winners = momentum.nlargest(config.MOMENTUM_TOP_N).index
        weights.loc[date, :] = 0.0
        weights.loc[date, winners] = 1.0 / config.MOMENTUM_TOP_N

    # Before the first rebalance there are no weights yet, so hold cash (flat NAV)
    weights = weights.where(weights.ne(0).any(axis=1), other=pd.NA).ffill().fillna(0.0)
    return _nav_from_weights(subset, weights, config.LEDGER_START_CAPITAL)


def _live_start() -> pd.Timestamp | None:
    """The first day we collected data ourselves: the line between backtest and live"""
    files = sorted(config.RAW_PRICES.glob("*.parquet"))
    if not files:
        return None
    # The file name is the run date, e.g. 2026-08-03.parquet
    return pd.Timestamp(files[0].stem.split("__")[0])


def _summarise(nav: pd.Series, live_start: pd.Timestamp | None) -> dict[str, object]:
    """Summarise the key numbers, keeping live clearly separate from backtest"""
    if nav.empty:
        return {"ok": False}

    def stats(series: pd.Series) -> dict[str, float | None]:
        if len(series) < 2:
            return {"return_pct": None, "max_drawdown_pct": None, "days": len(series)}
        total = series.iloc[-1] / series.iloc[0] - 1.0
        drawdown = (series / series.cummax() - 1.0).min()
        return {
            "return_pct": round(total * 100, 2),
            "max_drawdown_pct": round(drawdown * 100, 2),
            "days": len(series),
        }

    result: dict[str, object] = {"ok": True, "full": stats(nav)}
    if live_start is not None:
        result["live"] = stats(nav.loc[nav.index >= live_start])
    return result


def rebuild() -> dict[str, object]:
    """Recompute the whole ledger from the stored prices.

    **Recomputed every time** on purpose instead of appended, because:
    - it always gives the same result (deterministic)
    - if a day's run was missed, the system repairs itself; no permanent gaps
    """
    storage.ensure_dirs()
    prices = storage.price_matrix("adj_close")
    if prices.empty:
        return {"ok": False, "reason": "ยังไม่มีข้อมูลราคา — รัน collect ก่อน"}

    strategies = {
        "buy_hold_spy": _buy_hold(prices, "SPY"),
        "sixty_forty": _sixty_forty(prices),
        "mom_12_1": _momentum_12_1(prices),
    }
    strategies = {name: nav for name, nav in strategies.items() if not nav.empty}
    if not strategies:
        return {"ok": False, "reason": "คำนวณกลยุทธ์ไม่ได้เลย (ข้อมูลไม่พอ)"}

    nav_table = pd.DataFrame(strategies)
    nav_table.index.name = "date"

    out_path = config.DERIVED / "ledger.parquet"
    nav_table.reset_index().to_parquet(out_path, index=False)

    live_start = _live_start()
    return {
        "ok": True,
        "strategies": {name: _summarise(nav, live_start) for name, nav in strategies.items()},
        "live_start": str(live_start.date()) if live_start is not None else None,
        "rows": len(nav_table),
        "path": str(out_path.relative_to(config.ROOT)),
    }


def load() -> pd.DataFrame:
    """Load the computed ledger (used when building the brief)"""
    path = config.DERIVED / "ledger.parquet"
    if not path.exists():
        return pd.DataFrame()
    df = pd.read_parquet(path)
    return df.set_index("date").sort_index()
