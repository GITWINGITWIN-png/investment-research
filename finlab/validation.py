"""การแบ่งข้อมูลสำหรับข้อมูลการเงิน — ส่วนที่ 3 ของโน้ต 09

K-fold ธรรมดาใช้ไม่ได้: สุ่มแบ่ง = เอาอนาคตมาสอน แล้วทดสอบกับอดีต
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def walk_forward(dates: pd.DatetimeIndex, train_years: int = 3, test_months: int = 12) -> list[dict[str, pd.Timestamp]]:
    """หน้าต่างเลื่อน: [train 3 ปี] → [test 12 เดือนถัดไป] แล้วเลื่อนไปทีละ 12 เดือน

    จำลองสิ่งที่จะทำจริง (ปรับปรุงเป็นระยะ แล้วใช้กับอนาคตที่ไม่รู้) และได้ผลหลายชุด
    → เห็นความแปรปรวนข้ามปี ไม่ใช่ตัวเลขเดียวที่อาจโชคดี
    """
    splits = []
    start = dates[0]
    while True:
        train_end = start + pd.DateOffset(years=train_years)
        test_end = train_end + pd.DateOffset(months=test_months)
        test = dates[(dates >= train_end) & (dates < test_end)]
        if len(test) < 20:
            break
        train = dates[(dates >= start) & (dates < train_end)]
        splits.append({"train_start": train[0], "train_end": train[-1], "test_start": test[0], "test_end": test[-1]})
        start = start + pd.DateOffset(months=test_months)
    return splits


def purged_kfold(n: int, n_splits: int = 5, horizon: int = 20, embargo: int = 5) -> list[dict[str, np.ndarray]]:
    """K ช่วงต่อเนื่อง + purge + embargo (López de Prado, AFML บทที่ 7)

    - **purge**: ตัวอย่าง train ที่ label ใช้ผลตอบแทน `horizon` วันข้างหน้า ซึ่งยื่นเข้าไปในช่วง test
      ต้องตัดทิ้ง ไม่งั้นโมเดล "เห็น" คำตอบของ test ผ่าน label ของ train
    - **embargo**: เว้นช่วงหลัง test อีก `embargo` วันก่อนกลับมาใช้เป็น train
      ตัด autocorrelation ที่ยังหลงเหลือ
    """
    folds = np.array_split(np.arange(n), n_splits)
    out = []
    for test in folds:
        first, last = int(test[0]), int(test[-1])
        train = np.ones(n, dtype=bool)
        train[first:last + 1] = False
        train[max(0, first - horizon):first] = False          # purge
        train[last + 1:min(n, last + 1 + embargo)] = False      # embargo
        out.append({"train": np.flatnonzero(train), "test": test})
    return out


def walk_forward_ic(ic: pd.Series, splits: list[dict[str, pd.Timestamp]]) -> pd.DataFrame:
    """IC เฉลี่ยของแต่ละช่วง test — factor ที่ดีต้องเป็นบวกสม่ำเสมอ ไม่ใช่ดีปีเดียว"""
    rows = []
    for split in splits:
        window = ic.loc[split["test_start"]:split["test_end"]]
        rows.append({"test_start": split["test_start"].date(), "test_end": split["test_end"].date(),
                     "mean_ic": float(window.mean()) if len(window) else float("nan"), "days": len(window)})
    return pd.DataFrame(rows)
