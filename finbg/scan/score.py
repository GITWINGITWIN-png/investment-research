"""ชั้น 1 — ให้คะแนนความผิดปกติทั้งตลาดด้วยโค้ด (0 token) แล้วคัดเหลือ 30 ตัว

## คะแนนมาจากอะไร

| ส่วน | เงื่อนไข | คะแนน |
|---|---|---|
| ราคาผิดปกติ | วันเดียว ≥ 2.5σ และ ≥ 3% · หรือ 5 วัน ≥ 2.5σ และ ≥ 8% | 1–4 ตามความแรง |
| ปริมาณผิดปกติ | ≥ 2.5 เท่าของมัธยฐาน 20 วัน | 0.3–2 |
| 8-K | ตามหัวข้อ: critical 3 · high 2 · สัญญา/ซื้อขายกิจการ/เปลี่ยนผู้ควบคุม 1.5 · งบ 0.8 · อื่น 0.4 | รวมสูงสุด 3.5 |
| 13D | นักลงทุนเคลื่อนไหวถือเกิน 5% (ฉบับแก้ 1.2) | 2.5 |
| คนในซื้อ | ≥ 2 คนซื้อในตลาดรวม ≥ $100k ใน 10 วัน · หรือผู้บริหารคนเดียว ≥ $250k | 1.5–3.5 |
| งบ | surprise จากที่คาด (ใน 3 วัน) | 0–3 |

แล้วคูณด้วย **ตัวคูณ "คนมองข้าม"**: นักวิเคราะห์ ≤ 3 คน ×1.5 · ≤ 8 คน ×1.2 ·
มูลค่า < $2B ×1.3 · < $10B ×1.15 · ≥ $200B ×0.5 (หุ้นที่ทั้งโลกดูอยู่ โอกาสที่คนพลาดแทบไม่มี)

น้ำหนักพวกนี้ **ตั้งจากเหตุผล ยังไม่ได้ปรับจากผลลัพธ์** — พอร์ตไอเดียจะบอกเองว่าส่วนไหนมีค่า
(book.py เก็บผลของผู้เข้ารอบทั้ง 30 ตัวทุกวัน ไม่ใช่แค่ตัวที่ถูกเลือก)
"""

from __future__ import annotations

import math
from typing import Any

import pandas as pd

from .. import config

EIGHT_K_POINTS = {"critical": 3.0, "high": 2.0}
EIGHT_K_SPECIAL = {"1.01": 1.5, "2.01": 1.5, "5.01": 1.5, "2.02": 0.8, "7.01": 0.4, "8.01": 0.4}


def _item_points(items: list[str]) -> float:
    best = 0.0
    for code in items:
        severity = config.SEC_8K_ITEMS.get(code, ("", "info"))[1]
        best = max(best, EIGHT_K_POINTS.get(severity, 0.0), EIGHT_K_SPECIAL.get(code, 0.0))
    return best


def insider_summary(insider: pd.DataFrame) -> dict[str, dict[str, Any]]:
    """การซื้อในตลาด (code P) ของคนใน — ไม่นับการรับหุ้นเป็นค่าตอบแทน/การใช้สิทธิ์/แผน 10b5-1"""
    if insider.empty:
        return {}
    buys = insider[(insider["code"] == "P") & (insider["direction"] == "A") & (~insider["plan_10b5_1"].astype(bool))]
    out = {}
    for ticker, group in buys.groupby("ticker"):
        executives = group[group["officer"] | group["director"]]
        holders = group[group["ten_percent"]] if "ten_percent" in group else group.iloc[0:0]
        out[ticker] = {
            "buyers": int(group["owner"].nunique()),       # 1 คน = 1 กลุ่มผู้ยื่น (นิติบุคคลในเครือเดียวกันนับรวม)
            "value": float(group["value"].sum()),
            "executive_value": float(executives["value"].sum()),
            "holder_value": float(holders["value"].sum()),
            "detail": [f"{r.owner} ({r.title or ('director' if r.director else 'owner')}) {r.shares:,.0f} หุ้น @ {r.price:.2f}"
                       for r in group.itertuples()][:6],
        }
    return out


def score(universe: pd.DataFrame, base: pd.DataFrame, events: list[dict[str, Any]],
          insider: dict[str, dict[str, Any]], reported: list[dict[str, Any]],
          coverage: dict[str, int | None], cik_tickers: dict[int, list[str]]) -> pd.DataFrame:
    """คืนตารางทุกตัวที่ได้คะแนน > 0 เรียงจากมากไปน้อย พร้อมเหตุผล"""
    tradable = set(universe["ticker"])
    per_ticker_events: dict[str, list[dict[str, Any]]] = {}
    for event in events:
        for ticker in cik_tickers.get(int(event["cik"]), []):
            if ticker in tradable:
                per_ticker_events.setdefault(ticker, []).append(event)
    earnings_by = {r["ticker"]: r for r in reported if r["ticker"] in tradable}

    rows = []
    for row in universe.itertuples():
        ticker = row.ticker
        b = base.loc[ticker] if ticker in base.index else None
        reasons, parts = [], {}

        if b is not None and (b["history"] or 0) >= 40:
            if abs(b["z"]) >= 2.5 and abs(b["ret"]) >= 3:
                parts["move"] = min(4.0, abs(b["z"]) - 1.5)
                reasons.append(f"วันนี้ {b['ret']:+.1f}% ({b['z']:+.1f}σ)")
            if abs(b["z_5d"]) >= 2.5 and abs(b["ret_5d"]) >= 8:
                parts["move"] = max(parts.get("move", 0), min(3.0, abs(b["z_5d"]) - 1.5))
                reasons.append(f"5 วัน {b['ret_5d']:+.1f}% ({b['z_5d']:+.1f}σ)")
            ratio = b["volume_ratio"]
            if ratio and not math.isnan(ratio) and ratio >= 2.5:
                parts["volume"] = min(2.0, math.log2(ratio) - 1)
                reasons.append(f"volume {ratio:.1f}x")

        eight_k = [e for e in per_ticker_events.get(ticker, []) if e["kind"] == "8-K"]
        if eight_k:
            parts["8k"] = min(3.5, sum(_item_points(e.get("items") or []) for e in eight_k))
            codes = sorted({c for e in eight_k for c in e.get("items") or [] if c != "9.01"})
            if parts["8k"] > 0:
                reasons.append("8-K " + ", ".join(f"{c} {config.SEC_8K_ITEMS.get(c, ('?',))[0]}" for c in codes))
        thirteen_d = [e for e in per_ticker_events.get(ticker, []) if e["kind"] == "13D"]
        if thirteen_d:
            amended = all("/A" in e.get("form", "") for e in thirteen_d)
            parts["13d"] = 1.2 if amended else 2.5
            reasons.append("13D" + (" (ฉบับแก้)" if amended else " ใหม่ — นักลงทุนถือเกิน 5%"))

        buys = insider.get(ticker)
        if buys:
            if buys["buyers"] >= config.INSIDER_CLUSTER_MIN_BUYERS and buys["value"] >= config.INSIDER_CLUSTER_MIN_VALUE:
                parts["insider"] = 2 + min(1.5, math.log10(buys["value"] / 1e5))
                reasons.append(f"คนใน {buys['buyers']} คนซื้อในตลาดรวม ${buys['value']:,.0f}")
            elif buys["executive_value"] >= config.INSIDER_SINGLE_BIG_BUY:
                parts["insider"] = 1.5 + min(1.0, math.log10(buys["executive_value"] / config.INSIDER_SINGLE_BIG_BUY))
                reasons.append(f"ผู้บริหารซื้อในตลาด ${buys['executive_value']:,.0f}")
            elif buys["holder_value"] >= config.INSIDER_HOLDER_BIG_BUY:
                # ผู้ถือเกิน 10% ซื้อเพิ่มด้วยเงินก้อนใหญ่ — มักเป็นกองทุนที่ทำการบ้านมาแล้ว
                parts["insider"] = 1.5 + min(1.0, math.log10(buys["holder_value"] / config.INSIDER_HOLDER_BIG_BUY))
                reasons.append(f"ผู้ถือเกิน 10% ซื้อในตลาด ${buys['holder_value']:,.0f}")

        report = earnings_by.get(ticker)
        if report and report.get("surprise_pct") is not None:
            surprise = report["surprise_pct"]
            parts["earnings"] = min(3.0, abs(surprise) / 10)
            reasons.append(f"งบ {report['report_date']}: EPS {report.get('eps')} vs คาด {report.get('eps_forecast')} ({surprise:+.1f}%)")

        raw = sum(parts.values())
        if raw <= 0:
            continue
        estimates = coverage.get(ticker)
        coverage_mult = 1.5 if estimates is None or estimates <= 3 else (1.2 if estimates <= 8 else 1.0)
        cap = row.market_cap or 0
        size_mult = 1.3 if cap < 2e9 else 1.15 if cap < 10e9 else 0.5 if cap >= 200e9 else 1.0
        rows.append({
            "ticker": ticker, "name": row.name, "sector": row.sector, "industry": row.industry,
            "market_cap": cap, "close": row.close, "n_estimates": estimates,
            "score": round(raw * coverage_mult * size_mult, 3), "raw": round(raw, 3),
            "parts": {k: round(v, 2) for k, v in parts.items()}, "reasons": reasons,
            "ret": None if b is None else b["ret"], "z": None if b is None else b["z"],
            "ret_5d": None if b is None else b["ret_5d"], "volume_ratio": None if b is None else b["volume_ratio"],
            "events": per_ticker_events.get(ticker, []), "insider": buys, "earnings": report,
        })
    return pd.DataFrame(rows).sort_values("score", ascending=False).reset_index(drop=True) if rows else pd.DataFrame()
