"""ข้อมูลสำหรับงานวิจัย — universe + ราคาย้อนหลัง 10 ปี

## แยกจากคลัง PIT ของ finbg โดยตั้งใจ

`data/raw/` ของ finbg คือหลักฐานว่า "ณ วันนั้นเราเห็นอะไร" — ห้ามแก้ ห้ามเขียนทับ
ราคาย้อนหลังสำหรับวิจัยเป็นของที่ **ดาวน์โหลดใหม่ได้ทุกเมื่อ** จึงเก็บแยกที่ `data/research/`
ลบทิ้งแล้วสร้างใหม่ได้ ไม่ต้องสำรอง

## ⚠️ ข้อจำกัดที่รู้ตัว: survivorship + look-ahead ในการเลือก universe

universe นี้คือบริษัทที่ **ใหญ่ที่สุดในวันนี้** (ลำดับจากไฟล์ company_tickers.json ของ SEC
ซึ่งเรียงตามขนาดโดยประมาณ) แล้วย้อนไปทดสอบ 10 ปี — เท่ากับเลือกเฉพาะ "ผู้ชนะที่เรารู้แล้ว"
บริษัทที่เจ๊ง/ถูกซื้อ/หลุดจากดัชนีระหว่างทางไม่อยู่ในนี้เลย

ผลที่ตามมา: factor ที่ชอบหุ้นขาขึ้น (เช่น momentum) จะดูดีเกินจริง
→ ตัวเลขที่ได้จาก universe นี้คือ **ขอบบน** ไม่ใช่ค่าคาดหวัง
ทางแก้จริงต้องใช้ข้อมูลที่มีหุ้นถูกถอด (CRSP/Sharadar) — บันทึกไว้ในโน้ต 09/11
"""

from __future__ import annotations

import json
import time
from typing import Any

import pandas as pd

from finbg import config as fconfig
from finbg import storage

RESEARCH = fconfig.DATA / "research"
UNIVERSE_PATH = RESEARCH / "universe.json"
PRICES_PATH = RESEARCH / "prices.parquet"


def build_universe(size: int = 300) -> dict[str, Any]:
    """เลือกบริษัทใหญ่ที่สุด `size` แห่ง (ตัดชนิดหุ้นซ้ำของบริษัทเดียวกัน) + ETF ของ finbg"""
    import requests

    from finbg.collect.sources import sec as sec_source

    response = requests.get(sec_source.TICKER_MAP_URL, headers=sec_source._headers(), timeout=30)
    response.raise_for_status()
    seen_cik: set[int] = set()
    tickers: list[str] = []
    for entry in response.json().values():
        if entry["cik_str"] in seen_cik:
            continue                                  # GOOGL/GOOG, BRK-A/BRK-B → เอาตัวแรก
        symbol = entry["ticker"].upper().replace(".", "-")
        if not symbol.replace("-", "").isalpha():
            continue
        seen_cik.add(entry["cik_str"])
        tickers.append(symbol)
        if len(tickers) >= size:
            break

    universe = {
        "selected_at": storage.utc_stamp(),
        "method": f"top {size} by SEC company_tickers.json order (≈ market cap today)",
        "warning": "survivorship + look-ahead: เลือกจากผู้รอดชีวิตในวันนี้ ผลย้อนหลังเป็นขอบบน",
        "stocks": tickers,
        "etfs": sorted({*fconfig.SECTORS, *fconfig.PM_BROAD_ETFS}),
    }
    RESEARCH.mkdir(parents=True, exist_ok=True)
    UNIVERSE_PATH.write_text(json.dumps(universe, indent=2), encoding="utf-8")
    return universe


def universe() -> dict[str, Any]:
    if not UNIVERSE_PATH.exists():
        return build_universe()
    return json.loads(UNIVERSE_PATH.read_text(encoding="utf-8"))


def download_prices(years: int = 10, chunk: int = 50, refresh: bool = False) -> dict[str, Any]:
    """ดาวน์โหลดราคา 10 ปีของทั้ง universe ทีละก้อน (ลดโอกาสโดน rate limit)"""
    import yfinance as yf

    from finbg.collect.prices import _tidy

    u = universe()
    wanted = sorted({*u["stocks"], *u["etfs"]})
    existing = pd.read_parquet(PRICES_PATH) if PRICES_PATH.exists() and not refresh else pd.DataFrame()
    have = set(existing["ticker"].unique()) if not existing.empty else set()
    todo = [t for t in wanted if t not in have]

    frames = [existing] if not existing.empty else []
    failed: list[str] = []
    for start in range(0, len(todo), chunk):
        batch = todo[start:start + chunk]
        raw = yf.download(batch, period=f"{years}y", auto_adjust=False, progress=False, threads=True)
        tidy = _tidy(raw, batch)
        got = set(tidy["ticker"].unique()) if not tidy.empty else set()
        failed.extend(sorted(set(batch) - got))
        if not tidy.empty:
            frames.append(tidy[["date", "ticker", "close", "adj_close", "volume"]])
        time.sleep(2.0)

    if not frames:
        return {"ok": False, "reason": "ไม่ได้ราคาเลย"}
    prices = pd.concat(frames, ignore_index=True).drop_duplicates(["date", "ticker"], keep="last")
    RESEARCH.mkdir(parents=True, exist_ok=True)
    prices.to_parquet(PRICES_PATH, index=False)
    return {"ok": True, "tickers": prices["ticker"].nunique(), "rows": len(prices),
            "downloaded": len(todo) - len(failed), "failed": failed}


def load_prices(field: str = "adj_close", kind: str = "stocks") -> pd.DataFrame:
    """ตาราง wide (วันที่ × ticker) — kind = stocks | etfs | all

    กรองให้เหลือวันทำการของตลาดหุ้น (วันที่ SPY มีราคา) และตัดหุ้นที่มีราคาน้อยเกินไปทิ้ง
    """
    df = pd.read_parquet(PRICES_PATH)
    df["date"] = pd.to_datetime(df["date"])
    wide = df.pivot(index="date", columns="ticker", values=field).sort_index()
    if "SPY" in wide.columns:
        wide = wide.loc[wide["SPY"].notna()]
    u = universe()
    keep = {"stocks": u["stocks"], "etfs": u["etfs"], "all": list(wide.columns)}[kind]
    wide = wide[[t for t in keep if t in wide.columns]]
    return wide.loc[:, wide.notna().sum() >= 252]


def forward_returns(prices: pd.DataFrame, horizon: int) -> pd.DataFrame:
    """ผลตอบแทนจากราคาปิดวัน t ถึงวัน t+h — วางไว้ที่แถว t

    นี่คือ "คำตอบ" ที่ factor ณ วัน t พยายามทำนาย
    กับดัก: ห้ามใช้ค่านี้เป็น input ของ factor เด็ดขาด — มันคืออนาคต (ดู factors.lookahead_cheat)
    """
    return prices.shift(-horizon) / prices - 1.0
