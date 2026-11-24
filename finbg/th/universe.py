"""รายชื่อหุ้นไทย — snapshot ทุกวันจาก screener ของ Yahoo

## ทำไมต้องเก็บทุกวัน ทั้งที่รายชื่อแทบไม่เปลี่ยน

แหล่งฟรีทุกแหล่งรู้แค่ "หุ้นที่ยังจดทะเบียนวันนี้" — หุ้นที่ถูกเพิกถอนหายไปพร้อมประวัติราคา
ผลทดสอบย้อนหลังของตลาดไทยจึงเป็นขอบบนเสมอ (survivorship) และหนักกว่า US เพราะหุ้นเล็กถูกถอดบ่อย
การเก็บรายชื่อ + ราคาทุกวันแก้ปัญหานี้ได้ **นับจากวันนี้ไป** — ย้อนหลังแก้ไม่ได้ จึงต้องเริ่มเร็ว

## ทำไมไม่ใช้รายชื่อจาก set.or.th

ไฟล์รายชื่อบริษัทของ SET อยู่ใต้ /dat/ ซึ่ง robots.txt ของ SET ห้ามโปรแกรมเข้า

## คัด "หุ้นสามัญของบริษัทไทย" ออกจาก 2,300+ หลักทรัพย์

screener คืนทุกอย่างที่ซื้อขายใน SET: NVDR (-R), กระดานต่างประเทศ (-F), หุ้นบุริมสิทธิ (-P),
warrant (-W), DR ของหุ้นต่างประเทศ (เช่น NVDA80 = NVIDIA) และกองทรัสต์/REIT
กฎที่ใช้: ชื่อเต็มต้องเป็น "Public Company Limited" (บริษัทมหาชนไทย) และไม่ใช่กองทุน
— DR ใช้ชื่อบริษัทต่างประเทศ จึงหลุดไปเองโดยไม่ต้องเดาจากตัวเลขท้ายชื่อ
"""

from __future__ import annotations

import json
import re
import time
from typing import Any

import pandas as pd

from .. import config, storage

_SUFFIX = re.compile(r"-(R|F|P|W\d*)$")
_PCL = re.compile(r"public company|public co\b|\bpcl\b|public limited", re.I)
_FUND = re.compile(r"\bfund\b|reit|real estate investment trust|leasehold|infrastructure|\btrust\b", re.I)


def fetch_quotes() -> list[dict[str, Any]]:
    """ทุกหลักทรัพย์ที่ Yahoo ระบุว่าซื้อขายใน SET (รวม mai) — ทีละ 250 ตามที่ screener ยอม"""
    from ..datasource import yf
    from ..datasource import EquityQuery
    query = EquityQuery("eq", ["exchange", "SET"])
    quotes: list[dict[str, Any]] = []
    offset = 0
    while True:
        page = yf.screen(query, size=250, offset=offset, sortField="intradaymarketcap", sortAsc=False)
        quotes += page.get("quotes") or []
        offset += 250
        if offset >= page.get("total", 0) or not page.get("quotes"):
            break
        time.sleep(1.5)
    return quotes


def is_common_stock(quote: dict[str, Any]) -> bool:
    symbol = str(quote.get("symbol", ""))
    if not symbol.endswith(".BK") or _SUFFIX.search(symbol[:-3]):
        return False
    name = quote.get("longName") or quote.get("shortName") or ""
    return bool(_PCL.search(name)) and not _FUND.search(name)


def snapshot() -> dict[str, Any]:
    """เก็บ response ของ screener ทั้งก้อน (ทุกหลักทรัพย์ ไม่ใช่แค่หุ้นสามัญ) เป็น 1 ไฟล์ต่อวัน

    เก็บทุกฟิลด์ตามที่ได้มา — ฟิลด์ที่เป็น list/dict แปลงเป็นข้อความ JSON เพื่อให้ลง parquet ได้
    """
    quotes = fetch_quotes()
    if not quotes:
        return {"ok": False, "reason": "screener คืนรายชื่อว่าง"}
    df = pd.DataFrame.from_records(quotes)
    for column in df.columns:
        if df[column].map(lambda v: isinstance(v, (list, dict))).any():
            df[column] = df[column].map(lambda v: json.dumps(v, ensure_ascii=False) if isinstance(v, (list, dict)) else v)
    df["is_common_stock"] = [is_common_stock(q) for q in quotes]
    day = storage.utc_now().astimezone(config.LOCAL_TZ).date().isoformat()
    path = storage.write_parquet(df, config.RAW_TH_UNIVERSE / f"{day}.parquet", kind="th_universe",
                                 source="yahoo_screener")
    return {"ok": True, "instruments": len(df), "common_stocks": int(df["is_common_stock"].sum()),
            "path": str(path.relative_to(config.ROOT))}


def latest() -> pd.DataFrame:
    """หุ้นสามัญไทยจาก snapshot ล่าสุด (symbol, ชื่อ, มูลค่าตลาดเป็นบาท)"""
    files = sorted(config.RAW_TH_UNIVERSE.glob("*.parquet"))
    if not files:
        return pd.DataFrame(columns=["symbol", "name", "market_cap"])
    df = pd.read_parquet(files[-1])
    df = df[df["is_common_stock"]]
    name = df["longName"].fillna(df.get("shortName"))
    return pd.DataFrame({"symbol": df["symbol"], "name": name, "market_cap": df.get("marketCap")}).reset_index(drop=True)


def ever_listed() -> list[str]:
    """ทุกหุ้นสามัญที่เคยอยู่ใน snapshot ใดก็ตาม — ใช้ดึงราคาต่อให้ตัวที่ถูกถอดไปแล้วด้วย"""
    symbols: set[str] = set()
    for path in sorted(config.RAW_TH_UNIVERSE.glob("*.parquet")):
        df = pd.read_parquet(path, columns=["symbol", "is_common_stock"])
        symbols |= set(df.loc[df["is_common_stock"], "symbol"])
    return sorted(symbols)
