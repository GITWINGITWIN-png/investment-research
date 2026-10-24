"""พอร์ตกระดาษของ PM — จำลองทุกวันที่ราคาปิด เทียบกับ control บนตารางปรับพอร์ตเดียวกัน

## เส้นที่คำนวณ

| เส้น | คืออะไร | ตอบคำถามอะไร |
|---|---|---|
| `llm_pm` | พอร์ตจริงตามแผน (core + satellite ที่ PM เลือก) | พอร์ตทั้งก้อนทำได้เท่าไหร่ |
| `pm_control` | core เดียวกัน + satellite ถือ SPY เฉย ๆ | ถ้าไม่มี PM จะได้เท่าไหร่ |
| `llm_satellite` | เฉพาะส่วน satellite ของ PM | ฝีมือ PM แบบไม่ถูก core เจือจาง |
| `passive_satellite` | เฉพาะ satellite ของ control (SPY) | ตัวเทียบของเส้นบน |

ทั้งสี่เส้นปรับพอร์ต **วันเดียวกัน** และเสียต้นทุนแบบเดียวกัน ความต่างจึงมาจากการเลือกของ PM อย่างเดียว
เส้น baseline เดิม (buy_hold_spy, sixty_forty, mom_12_1) ถูก rebase มาเริ่มวันเดียวกันเพื่อเทียบ

## กติกาการจำลอง (ระมัดระวังโดยตั้งใจ)

- แผนมีผลที่ **ราคาปิดของวันทำการแรกที่ตลาดปิดหลังแผนออก** (market.effective_session)
- ระหว่างแผน น้ำหนักลอยตามราคา (ไม่ปรับรายวันแบบ ledger เดิม ซึ่งไม่สมจริง)
- ต้นทุน = PM_COST_BPS × มูลค่าที่ซื้อขาย · เงินสดได้ผลตอบแทน 0% (ไม่นับดอกเบี้ย)
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
    """ราคาปรับแล้ว (รวมเงินปันผล) เฉพาะวันทำการของตลาดหุ้น — ช่องว่างใช้ราคาล่าสุด"""
    prices = storage.price_matrix("adj_close")
    if prices.empty:
        return prices
    return prices.loc[market.sessions_from_prices(prices)].ffill()


def schedule(records: list[dict[str, Any]], sessions: pd.DatetimeIndex) -> tuple[list[tuple[pd.Timestamp, dict[str, Any]]], list[str]]:
    """(วันที่มีผล, แผน) เรียงตามเวลา + รายชื่อแผนที่ยังรอวันมีผล

    สองแผนมีผลวันเดียวกัน (เช่น replan ตามหลังแผนรายสัปดาห์) → ใช้แผนที่ออกทีหลัง
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
    """เดินทีละวันทำการ: ลอยตามราคา → ถ้าวันนี้มีแผน ปรับพอร์ตที่ราคาปิด

    คืน (ตาราง nav/traded/cost รายวัน, มูลค่าที่ถือ ณ วันสุดท้าย)
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
            current = holdings or {mandate.CASH: nav}      # วันแรก: เริ่มจากเงินสดทั้งก้อน
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
    """คำนวณพอร์ต PM ใหม่ทั้งหมดจากแผนทุกฉบับ (deterministic — รันซ้ำได้ผลเดิม)"""
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
