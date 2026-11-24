"""หุ้นทั้งตลาดสหรัฐจาก Nasdaq screener — 1 request ได้ทุกตัว (~7,000)

snapshot นี้ **ย้อนหลังไม่ได้** (API ให้แค่ค่าล่าสุด) จึงเก็บเป็นข้อมูลดิบ PIT ทุกวัน
universe ที่ใช้สแกน = มูลค่าตลาด ≥ $300M และมูลค่าซื้อขาย ≥ $2M/วัน (~3,100 ตัว)
"""

from __future__ import annotations

import re

import pandas as pd

from .. import config, storage
from ..datasource import SourceNotIncluded

SYMBOL = re.compile(r"^[A-Z]{1,5}([.\-][A-Z])?$")
EXCLUDE_NAME = re.compile(r"\b(Warrants?|Rights|Units?|Acquisition Corp|Preferred|Depositary|Notes due|ETF|Trust Units)\b", re.I)


def _number(text: object) -> float | None:
    cleaned = re.sub(r"[,$%\s]", "", str(text or ""))
    try:
        return float(cleaned)
    except ValueError:
        return None


def _screener_rows() -> list[dict]:
    """Rows in the Nasdaq screener's shape: symbol, name, lastsale, pctchange, volume,
    marketCap, country, sector, industry, ipoyear.

    Public edition: the private version read them from api.nasdaq.com, whose robots.txt
    disallows automated access, so this copy does not. Return rows of this shape from a
    source you are allowed to use.
    """
    raise SourceNotIncluded(
        "Stock universe source not included in the public edition: api.nasdaq.com disallows "
        "automated access in robots.txt. Implement _screener_rows() in finbg/scan/universe.py."
    )


def fetch() -> pd.DataFrame:
    rows = _screener_rows()
    df = pd.DataFrame([{
        "ticker": str(r.get("symbol", "")).strip().upper().replace("/", "-"),
        "name": r.get("name", ""),
        "close": _number(r.get("lastsale")),
        "ret": _number(r.get("pctchange")),
        "volume": _number(r.get("volume")),
        "market_cap": _number(r.get("marketCap")),
        "country": r.get("country", ""),
        "sector": r.get("sector", ""),
        "industry": r.get("industry", ""),
        "ipo_year": r.get("ipoyear", ""),
    } for r in rows])
    return df.dropna(subset=["close"])


def snapshot(session: str) -> tuple[pd.DataFrame, bool]:
    """เก็บ snapshot ของวันทำการ `session` — คืน (ตาราง, เป็นข้อมูลใหม่จริงไหม)

    วันหยุดตลาด API ยังคืนข้อมูลของวันทำการก่อนหน้า ถ้า % เปลี่ยนแปลงเหมือน snapshot
    ล่าสุดเกือบทั้งหมด = ข้อมูลซ้ำ ไม่บันทึกเป็นวันใหม่ (ไม่งั้นวันหยุดจะกลายเป็นวันทำการปลอม)
    """
    df = fetch()
    # เทียบกับวันทำการ "ก่อนหน้า" เท่านั้น — รันซ้ำวันเดิม (เช่น หลัง dry-run) ต้องไม่ถูกมองว่าเป็นวันหยุด
    earlier = [f for f in sorted(config.RAW_SCREENER.glob("*.parquet")) if f.stem[:10] < session]
    previous = pd.read_parquet(earlier[-1]) if earlier else pd.DataFrame()
    if not previous.empty:
        joined = df.merge(previous[["ticker", "ret", "close"]], on="ticker", suffixes=("", "_prev"))
        same = ((joined["ret"] == joined["ret_prev"]) & (joined["close"] == joined["close_prev"])).mean()
        if len(joined) > 1000 and same > 0.95:
            return df.assign(session=session), False
    df = df.assign(session=session)
    storage.write_parquet(df, config.RAW_SCREENER / f"{session}.parquet", kind="screener")
    return df, True


def latest() -> pd.DataFrame:
    files = sorted(config.RAW_SCREENER.glob("*.parquet"))
    return pd.read_parquet(files[-1]) if files else pd.DataFrame()


def tradable(df: pd.DataFrame, median_dollar_volume: pd.Series | None = None) -> pd.DataFrame:
    """กรองเหลือหุ้นที่ซื้อขายได้จริง — ใช้มูลค่าซื้อขายมัธยฐาน 20 วันถ้ามีประวัติพอ"""
    dollar_today = df["close"] * df["volume"]
    dollar = dollar_today
    if median_dollar_volume is not None and not median_dollar_volume.empty:
        dollar = df["ticker"].map(median_dollar_volume).fillna(dollar_today)
    mask = (
        df["ticker"].map(lambda t: bool(SYMBOL.match(t)))
        & ~df["name"].map(lambda n: bool(EXCLUDE_NAME.search(str(n))))
        & (df["market_cap"].fillna(0) >= config.SCAN_MIN_MCAP)
        & (dollar.fillna(0) >= config.SCAN_MIN_DOLLAR_VOLUME)
    )
    return df.loc[mask].reset_index(drop=True)
