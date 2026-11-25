"""The PM's paper portfolio: simulated daily at the close, against a control on the same rebalancing schedule.

## Lines computed

| Line | What it is | Question it answers |
|---|---|---|
| `llm_pm` | the real portfolio per plan (core + the PM's satellite) | what the whole portfolio achieved |
| `pm_control` | the same core + a satellite that just holds SPY | what we would get without the PM |
| `llm_satellite` | the PM's satellite only | the PM's skill, not diluted by the core |
| `passive_satellite` | the control's satellite only (SPY) | the benchmark for the line above |

All four lines rebalance **on the same day** and pay the same costs, so any difference comes only from the PM's choices.
The older baselines (buy_hold_spy, sixty_forty, mom_12_1) are rebased to the same start date for comparison.

## Simulation rules (deliberately conservative)

- A plan takes effect at **the close of the first trading day that closes after the plan is issued** (market.effective_session)
- Within a plan, weights drift with prices (no daily rebalancing as in the old ledger, which is unrealistic)
- Cost = PM_COST_BPS x traded value; cash earns 0% (interest is not counted)
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

import pandas as pd

from .. import config, ledger, market, storage
from . import mandate
from . import plans as plans_module

LEDGER_PATH = config.DERIVED / "pm_ledger.parquet"


def price_table() -> pd.DataFrame:
    """Adjusted prices (dividends included) on stock-market trading days only; gaps use the latest price"""
    prices = storage.price_matrix("adj_close")
    if prices.empty:
        return prices
    return prices.loc[market.sessions_from_prices(prices)].ffill()


def schedule(records: list[dict[str, Any]], sessions: pd.DatetimeIndex) -> tuple[list[tuple[pd.Timestamp, dict[str, Any]]], list[str]]:
    """(effective date, plan) in time order, plus the plans still waiting for their effective date.

    When two plans take effect on the same day (for example a replan right after a weekly plan), the later one is used.
    """
    by_day: dict[pd.Timestamp, dict[str, Any]] = {}
    pending: list[str] = []
    for record in sorted(records, key=lambda r: r["created_at"]):
        day = market.effective_session(datetime.fromisoformat(record["created_at"]), sessions)
        if day is None:
            pending.append(record["plan_id"])
            continue
        by_day[day] = record
    return sorted(by_day.items()), pending


def simulate(
    prices: pd.DataFrame,
    targets_by_day: dict[pd.Timestamp, dict[str, float]],
    capital: float = config.LEDGER_START_CAPITAL,
    cost_bps: float = config.PM_COST_BPS,
) -> tuple[pd.DataFrame, dict[str, float]]:
    """Step one trading day at a time: drift with prices, and if a plan takes effect today, rebalance at the close.

    Returns (daily nav/traded/cost table, holdings value on the last day)
    """
    start = min(targets_by_day)
    days = prices.index[prices.index >= start]
    returns = prices.pct_change()
    holdings: dict[str, float] = {}
    nav = capital
    rows = []

    for day in days:
        if holdings:
            for ticker in list(holdings):
                if ticker == mandate.CASH:
                    continue
                change = returns.at[day, ticker] if ticker in returns.columns else 0.0
                if pd.notna(change):
                    holdings[ticker] *= 1.0 + float(change)
            nav = sum(holdings.values())

        traded = cost = 0.0
        target = targets_by_day.get(day)
        if target is not None:
            current = holdings or {mandate.CASH: nav}      # First day: start fully in cash
            wanted = {ticker: weight * nav for ticker, weight in target.items()}
            traded = sum(
                abs(wanted.get(t, 0.0) - current.get(t, 0.0))
                for t in set(wanted) | set(current) if t != mandate.CASH
            )
            cost = traded * cost_bps / 10_000
            nav -= cost
            holdings = {ticker: weight * nav for ticker, weight in target.items()}

        rows.append({"date": day, "nav": nav, "traded": traded, "cost": cost})

    return pd.DataFrame(rows).set_index("date"), holdings


def _stats(series: pd.Series) -> dict[str, float]:
    clean = series.dropna()
    if len(clean) < 1:
        return {"return_pct": 0.0, "max_drawdown_pct": 0.0}
    return {
        "return_pct": round((clean.iloc[-1] / clean.iloc[0] - 1.0) * 100, 2),
        "max_drawdown_pct": round(float((clean / clean.cummax() - 1.0).min()) * 100, 2),
    }


def build() -> dict[str, Any]:
    """Recompute the whole PM portfolio from every plan (deterministic: re-running gives the same result)"""
    records = plans_module.load_all()
    if not records:
        return {"ok": True, "started": False}

    prices = price_table()
    if prices.empty:
        return {"ok": False, "reason": "ไม่มีราคา"}
    timeline, pending = schedule(records, prices.index)
    if not timeline:
        return {"ok": True, "started": False, "pending_plan": pending[-1] if pending else None}

    passive_sat, passive_cash = mandate.passive_satellite()

    def sleeve(satellite: dict[str, float], cash: float) -> dict[str, float]:
        return {**satellite, **({mandate.CASH: cash} if cash > 0 else {})}

    lines = {
        "llm_pm": {d: mandate.full_weights(r["targets"]["satellite"], r["targets"]["cash"]) for d, r in timeline},
        "pm_control": {d: mandate.full_weights(passive_sat, passive_cash) for d, _ in timeline},
        "llm_satellite": {d: sleeve(r["targets"]["satellite"], r["targets"]["cash"]) for d, r in timeline},
        "passive_satellite": {d: sleeve(passive_sat, passive_cash) for d, _ in timeline},
    }
    results = {name: simulate(prices, targets) for name, targets in lines.items()}
    table = pd.DataFrame({name: frame["nav"] for name, (frame, _) in results.items()})

    start = table.index[0]
    baselines = ledger.load()
    for column in baselines.columns:
        series = baselines[column].loc[start:].dropna()
        if not series.empty:
            table[column] = config.LEDGER_START_CAPITAL * series / series.iloc[0]

    llm_frame, _ = results["llm_pm"]
    table["llm_pm_traded"] = llm_frame["traded"]
    table["llm_pm_cost"] = llm_frame["cost"]
    table.index.name = "date"
    table.reset_index().to_parquet(LEDGER_PATH, index=False)

    _, sat_holdings = results["llm_satellite"]
    sat_total = sum(sat_holdings.values()) or 1.0
    stats = {name: _stats(table[name]) for name in ("llm_pm", "pm_control", "llm_satellite",
                                                     "passive_satellite", *baselines.columns)
             if name in table}
    return {
        "ok": True,
        "started": True,
        "start": str(start.date()),
        "as_of": str(table.index[-1].date()),
        "days": len(table),
        "plans_effective": len(timeline),
        "current_plan": timeline[-1][1]["plan_id"],
        "pending_plan": pending[-1] if pending else None,
        "lines": stats,
        "excess_vs_control_pct": round(stats["llm_pm"]["return_pct"] - stats["pm_control"]["return_pct"], 2),
        "satellite_excess_pct": round(stats["llm_satellite"]["return_pct"] - stats["passive_satellite"]["return_pct"], 2),
        "costs_pct": round(float(llm_frame["cost"].sum()) / config.LEDGER_START_CAPITAL * 100, 4),
        "drifted_satellite": {t: round(v / sat_total, 4) for t, v in sorted(sat_holdings.items(), key=lambda kv: -kv[1])},
        "path": str(LEDGER_PATH.relative_to(config.ROOT)),
    }


def load() -> pd.DataFrame:
    if not LEDGER_PATH.exists():
        return pd.DataFrame()
    return pd.read_parquet(LEDGER_PATH).set_index("date").sort_index()
