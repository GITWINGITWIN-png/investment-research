"""factor อ้างอิง — ทุกตัวคืนตาราง (วันที่ × ticker) ที่ค่า ณ วัน t ใช้ข้อมูลถึงวัน t เท่านั้น

ตัวจริง (มีเหตุผลเชิงเศรษฐศาสตร์ในวรรณกรรม):
    momentum_12_1   ผู้ชนะ 12 เดือน (ข้ามเดือนล่าสุด) มักชนะต่อ — Jegadeesh & Titman 1993
    reversal_1m     ผู้แพ้เดือนล่าสุดมักเด้ง (ระยะสั้น) — Jegadeesh 1990
    low_vol_60d     หุ้นผันผวนต่ำให้ผลตอบแทนต่อความเสี่ยงดีกว่า — "low-volatility anomaly"

ตัวปลอม (มีไว้ทดสอบว่า harness จับได้):
    random_noise     ไม่มีสัญญาณเลย → harness ต้องบอกว่าไร้ค่า
    lookahead_cheat  แอบใช้ผลตอบแทนในอนาคต → harness ต้องจับได้ว่า "ดีเกินจริง"
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def momentum_12_1(prices: pd.DataFrame) -> pd.DataFrame:
    return prices.shift(21) / prices.shift(252) - 1.0


def reversal_1m(prices: pd.DataFrame) -> pd.DataFrame:
    return -(prices / prices.shift(21) - 1.0)


def low_vol_60d(prices: pd.DataFrame) -> pd.DataFrame:
    return -prices.pct_change().rolling(60, min_periods=50).std()


def random_noise(prices: pd.DataFrame, seed: int = 0) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    noise = rng.standard_normal(prices.shape)
    return pd.DataFrame(noise, index=prices.index, columns=prices.columns).where(prices.notna())


def lookahead_cheat(prices: pd.DataFrame, horizon: int = 5, noise: float = 3.0, seed: int = 0) -> pd.DataFrame:
    """factor ที่ "โกง": ผลตอบแทน h วันข้างหน้า + noise ก้อนใหญ่

    ใส่ noise เยอะโดยตั้งใจ ให้หน้าตาเหมือน factor จริงมากขึ้น (IC ไม่ถึง 1.0)
    แต่ยังสูงเกินกว่าที่ factor จริงทำได้ — แบบเดียวกับบั๊ก look-ahead ในชีวิตจริง
    ที่มักแอบเข้ามาทีละนิด ไม่ใช่โจ่งแจ้ง
    """
    future = prices.shift(-horizon) / prices - 1.0
    scale = future.stack().std()
    rng = np.random.default_rng(seed)
    jitter = pd.DataFrame(rng.standard_normal(prices.shape) * scale * noise,
                          index=prices.index, columns=prices.columns)
    return (future + jitter).where(prices.notna())


REAL = {"momentum_12_1": momentum_12_1, "reversal_1m": reversal_1m, "low_vol_60d": low_vol_60d}
