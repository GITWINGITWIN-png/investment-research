"""สถิติกันหลอกตัวเอง — ส่วนที่ 2 ของโน้ต 09 (Multiple testing)

ปัญหา: ลอง 1,000 สูตรที่ไม่มีสัญญาณเลย จะมีราว 50 สูตร "ผ่าน" ที่ระดับ 5% โดยบังเอิญ
แล้วเราเลือกตัวที่ดีที่สุดใน 50 ตัวนั้น = noise ที่โชคดีที่สุด

เครื่องมือ:
- Probabilistic Sharpe (PSR): โอกาสที่ Sharpe จริงสูงกว่าเกณฑ์ เมื่อคิดความยาวข้อมูล
  และรูปร่างการกระจาย (เบ้/หางหนา) — Bailey & López de Prado 2012
- Deflated Sharpe (DSR): PSR ที่ยกเกณฑ์ขึ้นตาม "Sharpe สูงสุดที่คาดว่าจะเจอโดยบังเอิญ"
  จากการลอง N ครั้ง — ยิ่งลองเยอะ ยิ่งต้องชนะมาก — Bailey & López de Prado 2014
- PBO (CSCV): โอกาสที่ตัวที่ดีที่สุดในช่วง in-sample จะแย่กว่าค่ามัธยฐานใน out-of-sample
  — Bailey, Borwein, López de Prado & Zhu 2014
"""

from __future__ import annotations

import itertools
import math
from statistics import NormalDist

import numpy as np
import pandas as pd

NORMAL = NormalDist()
EULER_GAMMA = 0.5772156649015329


def sharpe(returns: pd.Series, periods: int = 252) -> float:
    r = returns.dropna()
    return float(r.mean() / r.std() * math.sqrt(periods)) if len(r) > 2 and r.std() > 0 else float("nan")


def probabilistic_sharpe(returns: pd.Series, benchmark_sr: float = 0.0) -> float:
    """PSR — ใช้ Sharpe "ต่อช่วง" (ไม่ annualize) ทั้ง returns และ benchmark_sr"""
    r = returns.dropna()
    n = len(r)
    if n < 3 or r.std() == 0:
        return float("nan")
    sr = r.mean() / r.std()
    skew = float(r.skew())
    kurt = float(r.kurt()) + 3.0            # pandas ให้ excess kurtosis — สูตรต้องการแบบไม่ excess
    denominator = math.sqrt(max(1e-12, 1 - skew * sr + (kurt - 1) / 4 * sr ** 2))
    return NORMAL.cdf((sr - benchmark_sr) * math.sqrt(n - 1) / denominator)


def expected_max_sharpe(n_trials: int, variance_of_sr: float) -> float:
    """Sharpe สูงสุด (ต่อช่วง) ที่คาดว่าจะได้จากการลอง n_trials ครั้ง ทั้งที่ไม่มีสัญญาณเลย"""
    if n_trials < 2 or variance_of_sr <= 0:
        return 0.0
    return math.sqrt(variance_of_sr) * (
        (1 - EULER_GAMMA) * NORMAL.inv_cdf(1 - 1 / n_trials)
        + EULER_GAMMA * NORMAL.inv_cdf(1 - 1 / (n_trials * math.e))
    )


def deflated_sharpe(returns: pd.Series, n_trials: int, variance_of_sr: float) -> float:
    """DSR > 0.95 = Sharpe นี้น่าจะไม่ใช่ผลของการลองหลายครั้ง (ที่ความมั่นใจ 95%)"""
    return probabilistic_sharpe(returns, expected_max_sharpe(n_trials, variance_of_sr))


def pbo_cscv(performance: pd.DataFrame, blocks: int = 16) -> dict[str, float]:
    """Probability of Backtest Overfitting

    `performance` = ผลตอบแทนต่อช่วง (แถว = เวลา, คอลัมน์ = กลยุทธ์/การตั้งค่าที่ลอง)
    แบ่งเวลาเป็น `blocks` ก้อน · ทุกวิธีเลือกครึ่งหนึ่งเป็น IS อีกครึ่งเป็น OOS
    · หาตัวดีสุดใน IS แล้วดูว่าอยู่อันดับไหนใน OOS
    PBO = สัดส่วนครั้งที่ตัวดีสุดใน IS ตกไปอยู่ครึ่งล่างใน OOS (> 0.5 = น่าจะ overfit)
    """
    data = performance.dropna(how="all").fillna(0.0).to_numpy()
    rows = (len(data) // blocks) * blocks
    chunks = np.array_split(data[:rows], blocks)
    logits = []
    for chosen in itertools.combinations(range(blocks), blocks // 2):
        in_sample = np.vstack([chunks[i] for i in chosen])
        out_sample = np.vstack([chunks[i] for i in range(blocks) if i not in chosen])
        is_sr = in_sample.mean(axis=0) / (in_sample.std(axis=0) + 1e-12)
        oos_sr = out_sample.mean(axis=0) / (out_sample.std(axis=0) + 1e-12)
        best = int(np.argmax(is_sr))
        rank = (oos_sr < oos_sr[best]).sum() + 1                 # 1 = แย่สุด, N = ดีสุด
        omega = rank / (len(oos_sr) + 1)
        logits.append(math.log(omega / (1 - omega)))
    logits_arr = np.array(logits)
    return {"pbo": float((logits_arr <= 0).mean()), "combinations": len(logits),
            "logit_median": float(np.median(logits_arr))}
