"""ตัวชี้วัดระดับ factor — ส่วนที่ 1 ของโน้ต 09 (Evaluation Playbook)

ทุกฟังก์ชันรับ factor แบบ (วันที่ × ticker) ที่ค่า ณ วัน t ใช้ข้อมูลถึง t เท่านั้น
และผลตอบแทนข้างหน้าจาก data.forward_returns
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd

from .data import forward_returns


def rank_ic(factor: pd.DataFrame, fwd: pd.DataFrame, min_names: int = 20, method: str = "spearman") -> pd.Series:
    """IC รายวัน = correlation ข้ามหุ้น ระหว่างค่า factor วันนั้น กับผลตอบแทนข้างหน้า

    Rank IC (Spearman) = correlation ของ "อันดับ" — ทนต่อหุ้นบางตัวที่พุ่ง 300% ซึ่งจะ
    ลากค่า Pearson ไปทั้งก้อน · ใช้ Rank IC เป็นหลักตามโน้ต 09
    วันที่มีหุ้นให้เทียบน้อยกว่า `min_names` ตัวถูกตัดทิ้ง (correlation จากไม่กี่ตัวคือ noise)
    """
    f, r = factor.align(fwd, join="inner")
    valid = f.notna() & r.notna()
    f, r = f.where(valid), r.where(valid)
    if method == "spearman":
        f, r = f.rank(axis=1), r.rank(axis=1)
    fm = f.sub(f.mean(axis=1), axis=0)
    rm = r.sub(r.mean(axis=1), axis=0)
    ic = (fm * rm).sum(axis=1) / np.sqrt((fm ** 2).sum(axis=1) * (rm ** 2).sum(axis=1))
    return ic[valid.sum(axis=1) >= min_names].dropna()


def ic_summary(ic: pd.Series, horizon: int) -> dict[str, float]:
    """สรุป IC: ค่าเฉลี่ย, ICIR, t-stat, อัตราที่เป็นบวก

    ## กับดักเรื่อง t-stat

    IC ของผลตอบแทน 20 วันที่คำนวณทุกวัน **ซ้อนทับกัน 19 วัน** — วันติดกันแทบเป็นค่าเดิม
    ถ้านับ 2,500 วันเป็น 2,500 ตัวอย่างอิสระ t-stat จะสูงเกินจริงราว √20 ≈ 4.5 เท่า
    จึงคิด t-stat จากตัวอย่างที่ห่างกันทุก h วัน (ไม่ซ้อนกัน) เท่านั้น
    """
    ic = ic.dropna()
    if len(ic) < 2:
        return {"mean_ic": float("nan"), "std_ic": float("nan"), "icir": float("nan"),
                "t_stat": float("nan"), "hit_rate": float("nan"), "n_days": len(ic), "n_independent": 0}
    sampled = ic.iloc[::max(horizon, 1)]
    t_stat = (sampled.mean() / sampled.std() * math.sqrt(len(sampled))) if len(sampled) > 2 and sampled.std() > 0 else float("nan")
    return {
        "mean_ic": float(ic.mean()),
        "std_ic": float(ic.std()),
        "icir": float(ic.mean() / ic.std()) if ic.std() > 0 else float("nan"),
        "t_stat": float(t_stat),
        "hit_rate": float((ic > 0).mean()),
        "n_days": int(len(ic)),
        "n_independent": int(len(sampled)),
    }


def decay_curve(factor: pd.DataFrame, prices: pd.DataFrame, horizons: tuple[int, ...] = (1, 5, 10, 21, 63)) -> pd.DataFrame:
    """IC ที่หลาย horizon — สัญญาณอยู่ได้นานแค่ไหน (decay เร็ว = ต้องเทรดถี่ = ค่าธรรมเนียมกิน)"""
    rows = []
    for horizon in horizons:
        summary = ic_summary(rank_ic(factor, forward_returns(prices, horizon)), horizon)
        rows.append({"horizon": horizon, **summary})
    return pd.DataFrame(rows).set_index("horizon")


def quantile_spread(factor: pd.DataFrame, fwd: pd.DataFrame, quantiles: int = 5) -> pd.Series:
    """ผลตอบแทนข้างหน้าของกลุ่มบนสุด − กลุ่มล่างสุด ต่อวัน (long-short แบบหยาบ)"""
    f, r = factor.align(fwd, join="inner")
    ranks = f.where(r.notna()).rank(axis=1, pct=True)
    top = r.where(ranks > 1 - 1 / quantiles).mean(axis=1)
    bottom = r.where(ranks <= 1 / quantiles).mean(axis=1)
    return (top - bottom).dropna()


def top_quantile_turnover(factor: pd.DataFrame, quantiles: int = 5, step: int = 21) -> float:
    """สัดส่วนสมาชิกกลุ่มบนสุดที่เปลี่ยนไปต่อรอบปรับพอร์ต — ตัวกำหนดต้นทุนซื้อขาย"""
    ranks = factor.rank(axis=1, pct=True).iloc[::step]
    top = ranks > 1 - 1 / quantiles
    changed = (top & ~top.shift(fill_value=False)).sum(axis=1)
    size = top.sum(axis=1).replace(0, np.nan)
    return float((changed / size).iloc[1:].mean())


def lookahead_audit(factor: pd.DataFrame, prices: pd.DataFrame, horizon: int = 5) -> dict[str, object]:
    """ตรวจอาการ "รู้อนาคต" สองแบบ

    1. **IC สูงเกินจริง** — factor รายวันข้ามหุ้นที่ IC เฉลี่ยเกิน 0.15 แทบไม่มีอยู่จริง (โน้ต 09)
    2. **IC หายเมื่อเลื่อนเวลา** — ถ้าใช้ค่า factor ของ h วันก่อน (ข้อมูลเก่าลงนิดเดียว)
       factor จริงที่ช้าจะเสื่อมลงเล็กน้อย แต่ factor ที่แอบใช้อนาคตจะหายเกือบหมด
       เพราะ "อนาคต" ของ h วันก่อน คืออดีตที่ไม่บอกอะไรเกี่ยวกับอนาคตจริง
    """
    fwd = forward_returns(prices, horizon)
    ic_now = float(rank_ic(factor, fwd).mean())
    ic_lagged = float(rank_ic(factor.shift(horizon), fwd).mean())
    flags = []
    if ic_now > 0.15:
        flags.append(f"IC เฉลี่ย {ic_now:.3f} > 0.15 — สูงเกินกว่าที่ factor จริงทำได้")
    if ic_now > 0.05 and ic_lagged < 0.25 * ic_now:
        flags.append(f"IC หายจาก {ic_now:.3f} เหลือ {ic_lagged:.3f} เมื่อใช้ค่าเก่าไป {horizon} วัน")
    return {"ic": ic_now, "ic_lagged": ic_lagged, "suspicious": bool(flags), "flags": flags}
