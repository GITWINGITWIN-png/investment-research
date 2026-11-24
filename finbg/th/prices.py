"""ราคารายวันของหุ้นไทย — snapshot ทุกวันแบบ point-in-time (แบบเดียวกับฝั่ง US ใน collect/prices.py)

## เก็บเฉพาะแท่งที่ปิดแล้ว

SET ปิดราว 16:40 หลัง closing auction — ถ้ารันก่อน 17:00 เวลาไทย แท่งของวันนี้คือราคาระหว่างวัน
ต้องตัดทิ้ง ไม่งั้นงานที่อ่านคลังนี้จะใช้ "ราคาปิด" ที่ยังไม่ใช่ราคาปิดโดยไม่รู้ตัว

## ดึงให้ทุกตัวที่ "เคยจดทะเบียน" ไม่ใช่แค่รายชื่อวันนี้

หุ้นที่เพิ่งถูกพักการซื้อขายหรือเพิกถอนยังควรมีราคาช่วงสุดท้ายในคลัง — นี่คือข้อมูลที่แหล่งฟรี
ทำหายไปและทำให้ผลย้อนหลังสวยเกินจริง ถ้า Yahoo ไม่คืนราคาแล้วก็แค่บันทึกว่าดึงไม่ได้
"""

from __future__ import annotations

import time
from datetime import datetime
from typing import Any

import pandas as pd

from .. import config, storage
from ..collect.prices import _tidy


def drop_incomplete(df: pd.DataFrame, now: datetime | None = None) -> tuple[pd.DataFrame, int]:
    """ตัดแท่งของวันนี้ถ้า SET ยังไม่ปิด (ก่อน 17:00 เวลาไทย) — คืน (ตาราง, จำนวนแถวที่ตัด)"""
    local = (now or storage.utc_now()).astimezone(config.LOCAL_TZ)
    today = pd.Timestamp(local.date())
    if local.hour >= config.TH_BAR_FINAL_HOUR:
        keep = df["date"] <= today
    else:
        keep = df["date"] < today
    return df[keep].reset_index(drop=True), int((~keep).sum())


def download(symbols: list[str], period: str) -> tuple[pd.DataFrame, list[str]]:
    """ดึงทีละก้อน (TH_CHUNK ตัว) เว้นจังหวะระหว่างก้อน — คืน (ตารางยาว, ตัวที่ดึงไม่ได้)"""
    from ..datasource import yf
    frames, failed = [], []
    for start in range(0, len(symbols), config.TH_CHUNK):
        batch = symbols[start:start + config.TH_CHUNK]
        raw = yf.download(batch, period=period, auto_adjust=False, progress=False, threads=True)
        tidy = _tidy(raw, batch)
        got = set(tidy["ticker"].unique()) if not tidy.empty else set()
        failed += sorted(set(batch) - got)
        if not tidy.empty:
            frames.append(tidy)
        time.sleep(2.0)
    return (pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()), failed


def snapshot(symbols: list[str]) -> dict[str, Any]:
    """แท่งย้อน ~1 เดือนของทุกตัว → 1 ไฟล์ต่อการรัน (snapshot ซ้อนกันได้ ตัวอ่านเก็บค่าที่ดึงทีหลังสุด)"""
    df, failed = download(symbols, config.TH_PRICE_PERIOD)
    if df.empty:
        return {"ok": False, "reason": "ไม่ได้ราคาเลย", "failed": len(failed)}
    df, dropped = drop_incomplete(df)
    day = storage.utc_now().astimezone(config.LOCAL_TZ).date().isoformat()
    path = storage.write_parquet(df, config.RAW_TH_PRICES / f"{day}.parquet", kind="th_prices",
                                 source="yfinance", tickers=int(df["ticker"].nunique()),
                                 failed=failed, dropped_incomplete=dropped)
    return {"ok": True, "tickers": int(df["ticker"].nunique()), "rows": len(df), "last_bar": str(df["date"].max().date()),
            "failed": len(failed), "dropped_incomplete": dropped, "path": str(path.relative_to(config.ROOT))}


def read_all() -> pd.DataFrame:
    """รวม snapshot ทั้งหมด — วันซ้ำเก็บแถวที่ดึงทีหลังสุด (ราคาปิดอาจถูกปรับหลังตลาดปิด)"""
    files = sorted(config.RAW_TH_PRICES.glob("*.parquet"))
    if not files:
        return pd.DataFrame()
    df = pd.concat([pd.read_parquet(f) for f in files], ignore_index=True)
    df["date"] = pd.to_datetime(df["date"]).dt.normalize()
    return (df.sort_values("fetched_at").drop_duplicates(["ticker", "date"], keep="last")
              .sort_values(["ticker", "date"]).reset_index(drop=True))
