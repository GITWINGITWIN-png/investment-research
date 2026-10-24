"""ต้นทุนซื้อขาย — factor ที่ IC ดีแต่ต้องเปลี่ยนพอร์ตบ่อย อาจขาดทุนสุทธิ

net = gross − turnover × (ค่าคอม + ครึ่ง spread + slippage)
ค่าเริ่มต้น 10 bps ต่อหน่วยมูลค่าที่ซื้อขาย = ระดับหุ้นใหญ่/ETF สภาพคล่องสูง แบบระมัดระวัง
หุ้นเล็กควรใช้ 30–100 bps
"""

from __future__ import annotations

import pandas as pd

DEFAULT_BPS = 10.0


def net_returns(gross: pd.Series, turnover: pd.Series | float, bps: float = DEFAULT_BPS) -> pd.Series:
    return gross - turnover * bps / 10_000
