"""หุ้นไทยใน harness — factor ช้าเฉพาะหุ้นที่ซื้อขายได้จริง หักต้นทุนตาม tick ของ SET (แทร็กไทย ขั้น 2 · โน้ต 13)

    uv run finlab thai-prices     ราคา 10 ปีของหุ้นไทยทุกตัวในคลังรายชื่อของ finbg/th (~2 นาที)
    uv run finlab thai            รายงาน → Journal/Research/thai-factors-<วันที่>.md

## ⚠️ ผลชุดนี้คือ "การสำรวจ" ไม่ใช่ "การพิสูจน์"

ข้อมูลช่วงเดียวกันถูกดูไปแล้วตอนตัดสินใจเปิดแทร็กไทย (2026-11-08) — วัดซ้ำบนข้อมูลเดิมไม่ใช่การทดสอบ
ทุกครั้งที่รันถูกบันทึกลง experiments.jsonl (family "thai") ให้ Deflated Sharpe นับจำนวนครั้งที่ลองได้ถูก
การพิสูจน์จริงต้องใช้ช่วงหลังวันนี้เท่านั้น

## ⚠️ survivorship — แก้ย้อนหลังไม่ได้ด้วยข้อมูลฟรี

หุ้นที่ถูกเพิกถอนหายจาก Yahoo ทั้งประวัติ (ทดสอบ 2026-11-08: STARK, EIC, EARTH, JKN = 0 แถว)
แหล่งที่มีคือ SETSMART (เสียเงิน) · finbg/th เก็บรายชื่อทุกวันตั้งแต่ 2026-11-08 → ช่วงหลังจากนี้ไร้ survivorship
ผลกระทบ: momentum ดูดีเกินจริง (ผู้แพ้ที่ถูกถอดหายไปจากตาราง) · low-vol น่าจะดูแย่กว่าจริงเล็กน้อย
(หุ้นที่ถูกถอดมักผันผวนสูงและขาดทุนหนัก — ถ้ามีอยู่ในตาราง low-vol จะชนะห่างขึ้น)

## สามอย่างที่ต่างจากการวัดฝั่ง US

1. **เฉพาะหุ้นที่ซื้อขายได้จริง** (มัธยฐาน ≥ ฿5M/วัน · ราคา ≥ ฿2) — สัญญาณในหุ้นที่ซื้อไม่ได้ไม่มีความหมาย
2. **ต้นทุนรายตัวตาม tick** (costs.tick_round_trip) แทน bps คงที่
3. **แยกตลาดขึ้น/ลง** — ตลาดไทย 2018–2025 เป็นขาลงยาว low-vol อาจดูดีเพราะ "ลงน้อยกว่า" (beta ต่ำ)
   ไม่ใช่เพราะชนะจริง · ถ้าชนะเฉพาะตอนตลาดลง = เป็นการถือ beta ต่ำ ไม่ใช่ alpha
"""

from __future__ import annotations

import json
from typing import Any

import numpy as np
import pandas as pd

from finbg import config as fconfig
from finbg import storage
from finbg.th import prices as th_prices
from finbg.th import universe as th_universe

from . import costs, experiments, factors, stats, validation
from .data import RESEARCH, forward_returns
from .metrics import ic_summary, rank_ic

PRICES_PATH = RESEARCH / "th_prices.parquet"
REPORTS = fconfig.JOURNAL / "Research"
CLEAN_LIMIT = 0.35          # SET ขยับได้ไม่เกิน ±30%/วัน → ราคาปรับแล้วที่ขยับเกินนี้คือข้อมูลเสีย
REBALANCE = 21              # ปรับพอร์ตเดือนละครั้ง — สัญญาณช้า ต้นทุนจึงพอไหว
HORIZONS = (5, 21, 63)
FACTORS = {"low_vol_60d": factors.low_vol_60d, "momentum_12_1": factors.momentum_12_1,
           "reversal_1m": factors.reversal_1m}   # reversal ใส่ไว้เทียบ: เป็นสัญญาณเร็ว คาดว่าแพ้ต้นทุน


def download_prices(years: int = 10) -> dict[str, Any]:
    symbols = th_universe.ever_listed()
    if not symbols:
        return {"ok": False, "reason": "ยังไม่มีรายชื่อ — รัน `uv run finbg th collect` ก่อน"}
    df, failed = th_prices.download(symbols, f"{years}y")
    df, _ = th_prices.drop_incomplete(df)
    RESEARCH.mkdir(parents=True, exist_ok=True)
    df.to_parquet(PRICES_PATH, index=False)
    return {"ok": True, "tickers": int(df["ticker"].nunique()), "rows": len(df), "failed": failed}


def load() -> dict[str, pd.DataFrame]:
    df = pd.read_parquet(PRICES_PATH)
    df["date"] = pd.to_datetime(df["date"])
    wide = {f: df.pivot(index="date", columns="ticker", values=f).sort_index() for f in ("close", "adj_close", "volume")}
    calendar = wide["adj_close"].index[wide["adj_close"][fconfig.TH_CALENDAR_TICKER].notna()]
    return {f: w.loc[calendar] for f, w in wide.items()}


def clean_price(adj_close: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """ดัชนีราคาที่ตัดวันข้อมูลเสีย (|ผลตอบแทน| > 35%) ออก + ตารางว่าวันไหนถูกตัด

    ข้อมูลฟรีของหุ้นไทยมีจุดเสียมากกว่า US ~10 เท่า (แตกพาร์/เพิ่มทุนที่ปรับราคาผิด)
    ถ้าปล่อยไว้ หุ้นที่ "กระโดด 300%" ปลอม ๆ จะครองอันดับ momentum ทั้งตาราง
    ไม่ตัดหุ้นตัวนั้นทิ้งทั้งตัว (นั่นคือการเลือกจากข้อมูลอนาคต) — แค่ไม่นับวันที่เสีย
    """
    r = adj_close.pct_change(fill_method=None)
    flagged = r.abs() > CLEAN_LIMIT
    price = (1 + r.mask(flagged).fillna(0)).cumprod().where(adj_close.notna())
    return price, flagged


def tradable(w: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """ณ วัน t ซื้อขายได้จริงไหม — ใช้ข้อมูลถึงวัน t เท่านั้น (มัธยฐาน 60 วันย้อนหลัง)"""
    value = (w["close"] * w["volume"]).rolling(60, min_periods=40).median()
    return (value >= fconfig.TH_TRADABLE_MIN_VALUE) & (w["close"] >= fconfig.TH_TRADABLE_MIN_PRICE) & (w["volume"] > 0)


def portfolio(factor: pd.DataFrame, fwd: pd.DataFrame, mask: pd.DataFrame, cost: pd.DataFrame) -> pd.DataFrame:
    """long-only หุ้น 20% บนสุด ถือเท่ากัน ปรับทุก 21 วัน — เทียบกับหุ้นที่ซื้อขายได้ทั้งหมดถือเท่ากัน

    ต้นทุนต่อรอบ = สัดส่วนพอร์ตที่เปลี่ยน × ต้นทุนไป-กลับเฉลี่ยของตัวที่ซื้อเข้า (tick + ค่าคอม)
    รอบแรกเป็นการซื้อเข้าอย่างเดียว จึงคิดครึ่งรอบ
    """
    rows, held = [], set()
    for day in factor.index[::REBALANCE]:
        f = factor.loc[day].where(mask.loc[day]).dropna()
        r = fwd.loc[day].reindex(f.index)
        f = f[r.notna()]
        if len(f) < 30:
            continue
        top = set(f[f.rank(pct=True) > 0.8].index)
        new = top - held
        turnover = len(new) / len(top) if held else 0.5
        trade_cost = float(cost.loc[day, sorted(new)].mean()) if new else 0.0
        excess = float(r[sorted(top)].mean() - r[f.index].mean())
        rows.append({"date": day, "excess_gross": excess, "excess_net": excess - turnover * trade_cost,
                     "turnover": turnover, "trade_cost": trade_cost})
        held = top
    return pd.DataFrame(rows).set_index("date")


def run() -> dict[str, Any]:
    w = load()
    price, flagged = clean_price(w["adj_close"])
    mask = tradable(w)
    cost = costs.tick_round_trip(w["close"], fconfig.TH_TICKS, fconfig.TH_COMMISSION_ROUND_TRIP)
    fwd = {h: forward_returns(price, h) for h in HORIZONS}
    market_21 = fwd[21].where(mask).mean(axis=1)                       # หุ้นที่ซื้อขายได้ทั้งหมดถือเท่ากัน
    up_days = market_21[market_21 > 0].index
    recent_start = price.index[-1] - pd.DateOffset(years=2)
    period = (str(price.index[0].date()), str(price.index[-1].date()))

    result: dict[str, Any] = {
        "at": storage.utc_stamp(), "period": period, "stocks_in_data": int(price.shape[1]),
        "tradable_now": int(mask.iloc[-1].sum()), "tradable_avg": round(float(mask.sum(axis=1).mean()), 1),
        "flagged_moves": int(flagged.sum().sum()), "factors": {},
    }
    nets: dict[str, pd.Series] = {}
    for name, fn in FACTORS.items():
        factor = fn(price).where(mask)
        ic = {h: rank_ic(factor, fwd[h]) for h in HORIZONS}
        ic21 = ic[21]
        book = portfolio(factor, fwd[REBALANCE], mask, cost)
        nets[name] = book["excess_net"]
        block = {
            "ic": {f"{h}d": {k: round(v, 4) if isinstance(v, float) else v for k, v in ic_summary(ic[h], h).items()}
                   for h in HORIZONS},
            "ic_21d_market_up": round(float(ic21[ic21.index.isin(up_days)].mean()), 4),
            "ic_21d_market_down": round(float(ic21[~ic21.index.isin(up_days)].mean()), 4),
            "ic_21d_last_2y": {k: round(v, 4) if isinstance(v, float) else v
                               for k, v in ic_summary(ic21.loc[recent_start:], 21).items()},
            "walk_forward": [{**row, "test_start": str(row["test_start"]), "test_end": str(row["test_end"]),
                              "mean_ic": round(row["mean_ic"], 4)}
                             for row in validation.walk_forward_ic(ic21, validation.walk_forward(price.index)).to_dict("records")],
            "portfolio": {
                "months": len(book),
                "excess_gross_annual": round(float(book["excess_gross"].mean() * 12), 4),
                "excess_net_annual": round(float(book["excess_net"].mean() * 12), 4),
                "t_net": round(float(book["excess_net"].mean() / book["excess_net"].std() * np.sqrt(len(book))), 2),
                "turnover_per_month": round(float(book["turnover"].iloc[1:].mean()), 2),
                "cost_per_trade": round(float(book["trade_cost"].iloc[1:].mean()), 4),
                "sharpe_net": round(stats.sharpe(book["excess_net"], periods=12), 2),
                "psr_net": round(stats.probabilistic_sharpe(book["excess_net"]), 3),
            },
        }
        result["factors"][name] = block
        experiments.log("thai", name, {"horizons": list(HORIZONS), "rebalance": REBALANCE, "universe": "tradable",
                                       "min_value_thb": fconfig.TH_TRADABLE_MIN_VALUE, "min_price": fconfig.TH_TRADABLE_MIN_PRICE,
                                       "cost": "tick+commission"},
                        {**block["ic"]["21d"], **block["portfolio"]}, int(price.shape[1]), period)

    # Deflated Sharpe: หักจำนวนครั้งที่ลองในตระกูล "thai" ทั้งหมด (รวมรอบก่อน ๆ) ไม่ใช่แค่ 3 ตัวในรอบนี้
    per_period = {name: s.mean() / s.std() for name, s in nets.items()}
    best = max(per_period, key=per_period.get)
    trials = max(experiments.count("thai"), len(nets))
    result["best"] = {"factor": best, "trials_counted": trials,
                      "dsr": round(stats.deflated_sharpe(nets[best], n_trials=trials,
                                                         variance_of_sr=float(np.var(list(per_period.values()), ddof=1))), 3)}
    _write_report(result)
    return result


def _write_report(result: dict[str, Any]) -> None:
    day = result["at"][:10]
    (RESEARCH / f"thai-factors-{day}.json").write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str),
                                                        encoding="utf-8")
    pct = lambda x: f"{x * 100:+.1f}%"          # noqa: E731
    lines = [
        "---", f"date: {day}", "tags: [research, thailand, factors]", "---", "",
        f"# 🇹🇭 factor ช้าในหุ้นไทยที่ซื้อขายได้จริง ({day})", "",
        f"ข้อมูล {result['period'][0]} → {result['period'][1]} · หุ้นในข้อมูล {result['stocks_in_data']} ตัว · "
        f"ซื้อขายได้จริง (≥ ฿{fconfig.TH_TRADABLE_MIN_VALUE / 1e6:.0f}M/วัน · ราคา ≥ ฿{fconfig.TH_TRADABLE_MIN_PRICE:.0f}) "
        f"เฉลี่ย {result['tradable_avg']} ตัว/วัน (ตอนนี้ {result['tradable_now']})", "",
        "> [!warning] การสำรวจ ไม่ใช่การพิสูจน์",
        "> ข้อมูลช่วงนี้ถูกดูไปแล้วตอนตัดสินใจเปิดแทร็กไทย · หุ้นที่ถูกเพิกถอนไม่มีในข้อมูลฟรี (ผลเป็นขอบบน โดยเฉพาะ momentum)",
        f"> · ตัดวันข้อมูลเสีย (ขยับ > 35%/วัน ซึ่ง SET ไม่ยอมให้เกิด) {result['flagged_moves']} จุด",
        "", "## Rank IC (เฉพาะหุ้นที่ซื้อขายได้จริง)", "",
        "| factor | 5 วัน | 21 วัน | 63 วัน | 21 วัน · ตลาดขึ้น | 21 วัน · ตลาดลง | 21 วัน · 2 ปีหลัง |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    cell = lambda s: f"{s['mean_ic']:+.4f} (t {s['t_stat']:+.1f})"   # noqa: E731
    for name, b in result["factors"].items():
        lines.append(f"| {name} | {cell(b['ic']['5d'])} | {cell(b['ic']['21d'])} | {cell(b['ic']['63d'])} | "
                     f"{b['ic_21d_market_up']:+.4f} | {b['ic_21d_market_down']:+.4f} | {cell(b['ic_21d_last_2y'])} |")
    lines += [
        "", "*ตลาดขึ้น/ลง = หุ้นที่ซื้อขายได้ทั้งหมดถือเท่ากัน ขึ้นหรือลงใน 21 วันข้างหน้า · ชนะเฉพาะตอนตลาดลง = beta ต่ำ ไม่ใช่ alpha*",
        "", "## พอร์ตจริง: หุ้น 20% บนสุด ปรับเดือนละครั้ง หักต้นทุนตาม tick + ค่าคอม", "",
        "| factor | เดือน | ส่วนเกินก่อนต้นทุน/ปี | หลังต้นทุน/ปี | t | Sharpe | PSR | เปลี่ยนตัว/เดือน | ต้นทุน/ไม้ |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for name, b in result["factors"].items():
        p = b["portfolio"]
        lines.append(f"| {name} | {p['months']} | {pct(p['excess_gross_annual'])} | **{pct(p['excess_net_annual'])}** | "
                     f"{p['t_net']:+.2f} | {p['sharpe_net']:.2f} | {p['psr_net']:.2f} | {p['turnover_per_month']:.0%} | "
                     f"{p['cost_per_trade']:.2%} |")
    best = result["best"]
    lines += [
        "", f"**ตัวที่ดีที่สุด ({best['factor']}) หลังหักจำนวนครั้งที่ลองทั้งหมดในตระกูล thai ({best['trials_counted']} ครั้ง):** "
            f"Deflated Sharpe = **{best['dsr']:.2f}** (≥ 0.95 = น่าจะไม่ใช่โชค)", "",
        "## IC 21 วันรายปี (walk-forward)", "", "| ช่วงทดสอบ | " + " | ".join(result["factors"]) + " |",
        "|---|" + "---:|" * len(result["factors"]),
    ]
    names = list(result["factors"])
    for i, row in enumerate(result["factors"][names[0]]["walk_forward"]):
        cells = " | ".join(f"{result['factors'][n]['walk_forward'][i]['mean_ic']:+.4f}" for n in names)
        lines.append(f"| {row['test_start']} → {row['test_end']} | {cells} |")
    lines += ["", f"*บันทึกการทดลองตระกูล thai แล้ว {best['trials_counted']} รายการใน data/research/experiments.jsonl*", "",
              "🔗 [[13 - Thai Track]] · [[09 - Evaluation Playbook]]", ""]
    REPORTS.mkdir(parents=True, exist_ok=True)
    (REPORTS / f"thai-factors-{day}.md").write_text("\n".join(lines), encoding="utf-8")
