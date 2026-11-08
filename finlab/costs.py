"""ต้นทุนซื้อขาย — factor ที่ IC ดีแต่ต้องเปลี่ยนพอร์ตบ่อย อาจขาดทุนสุทธิ

net = gross − turnover × (ค่าคอม + ครึ่ง spread + slippage)
ค่าเริ่มต้น 10 bps ต่อหน่วยมูลค่าที่ซื้อขาย = ระดับหุ้นใหญ่/ETF สภาพคล่องสูง แบบระมัดระวัง
หุ้นเล็กควรใช้ 30–100 bps
"""

from __future__ import annotations

import numpy as np
import pandas as pd

DEFAULT_BPS = 10.0


def net_returns(gross: pd.Series, turnover: pd.Series | float, bps: float = DEFAULT_BPS) -> pd.Series:
    return gross - turnover * bps / 10_000


def tick_round_trip(price: pd.DataFrame, ticks: list[tuple[float, float]], commission: float) -> pd.DataFrame:
    """ต้นทุนซื้อแล้วขาย 1 รอบ (สัดส่วนของราคา) ในตลาดที่มีตาราง tick — เช่น SET

    spread ของหุ้นที่คล่องที่สุดก็กว้างอย่างน้อย 1 tick: ซื้อที่ราคาเสนอขาย ขายที่ราคาเสนอซื้อ
    = เสีย 1 tick ต่อรอบ · ใน SET 1 tick คือ 0.3–1% ของราคา (หุ้น ฿25–100 ขยับทีละ ฿0.25)
    ซึ่งใหญ่กว่าค่าคอมมาก — จึงต้องคิดรายตัวตามราคา ไม่ใช่ใช้ bps คงที่แบบหุ้น US

    `ticks` = [(ราคาต่ำกว่า, ขนาด tick), ...] เรียงจากต่ำไปสูง · ค่านี้คือขั้นต่ำ หุ้นไม่คล่อง spread กว้างกว่านี้
    """
    values = price.to_numpy(dtype=float)
    tick = np.full(values.shape, np.nan)
    lower = 0.0
    for upper, size in ticks:
        tick[(values >= lower) & (values < upper)] = size
        lower = upper
    return pd.DataFrame(tick / values + commission, index=price.index, columns=price.columns)
