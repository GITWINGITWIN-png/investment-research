"""ดึงราคารายวันแล้วเก็บเป็น snapshot

ทำไมต้องเก็บเองทั้งที่ yfinance ดึงย้อนหลังได้อยู่แล้ว:
1. ราคาที่ปรับแล้ว (adjusted close) **เปลี่ยนย้อนหลัง** ทุกครั้งที่มีปันผล/แตกพาร์
   ถ้าไม่เก็บ snapshot เราจะไม่มีทางรู้ว่า "ณ วันนั้นเราเห็นราคาเท่าไหร่"
2. yfinance เป็น unofficial API — วันหนึ่งอาจใช้ไม่ได้ ถ้ามีคลังของตัวเองก็ยังทำงานต่อได้
3. ทำให้ backtest ทำซ้ำได้เป๊ะ (reproducible) เพราะข้อมูลไม่เปลี่ยนใต้เท้าเรา
"""

from __future__ import annotations

import pandas as pd
import yfinance as yf

from .. import config, storage


def _tidy(raw: pd.DataFrame, tickers: list[str]) -> pd.DataFrame:
    """แปลงผลจาก yfinance (columns ซ้อนกัน) ให้เป็นตารางยาวที่อ่านง่าย

    yfinance คืน MultiIndex columns เมื่อขอหลาย ticker แต่คืนแบบธรรมดาเมื่อขอตัวเดียว
    ฟังก์ชันนี้ทำให้ทั้งสองกรณีออกมาหน้าตาเหมือนกัน
    """
    if raw.empty:
        return pd.DataFrame()

    if isinstance(raw.columns, pd.MultiIndex):
        # ระดับคอลัมน์คือ (field, ticker) — สลับให้เป็นแถวละ (date, ticker)
        df = raw.stack(level=-1, future_stack=True).reset_index()
        df.columns = [str(c) for c in df.columns]
        df = df.rename(columns={df.columns[0]: "date", df.columns[1]: "ticker"})
    else:
        df = raw.reset_index()
        df.columns = [str(c) for c in df.columns]
        df = df.rename(columns={df.columns[0]: "date"})
        df["ticker"] = tickers[0]

    rename = {
        "Open": "open",
        "High": "high",
        "Low": "low",
        "Close": "close",
        "Adj Close": "adj_close",
        "Volume": "volume",
    }
    df = df.rename(columns=rename)

    # ถ้า yfinance ปรับราคาให้แล้ว จะไม่มีคอลัมน์ adj_close แยก
    if "adj_close" not in df.columns:
        df["adj_close"] = df["close"]

    keep = ["date", "ticker", "open", "high", "low", "close", "adj_close", "volume"]
    df = df[[c for c in keep if c in df.columns]]

    df["date"] = pd.to_datetime(df["date"]).dt.tz_localize(None).dt.normalize()
    return df.dropna(subset=["close"]).sort_values(["ticker", "date"]).reset_index(drop=True)


def collect(period: str | None = None) -> dict[str, object]:
    """ดึงราคาแล้วเขียน snapshot 1 ไฟล์ต่อ 1 การรัน

    `period` = None หมายถึงให้ตัดสินใจอัตโนมัติ:
    - ยังไม่มีข้อมูลเลย → ดึงย้อนหลัง 10 ปี (backfill ครั้งเดียว)
    - มีข้อมูลแล้ว → ดึงแค่ 1 เดือนล่าสุด (จับการปรับราคาย้อนหลังด้วย)
    """
    storage.ensure_dirs()
    tickers = config.all_tickers()

    if period is None:
        already_have = any(config.RAW_PRICES.glob("*.parquet"))
        period = "1mo" if already_have else "10y"

    raw = yf.download(
        tickers,
        period=period,
        auto_adjust=False,
        progress=False,
        threads=True,
    )
    df = _tidy(raw, tickers)

    if df.empty:
        return {"ok": False, "rows": 0, "reason": "yfinance คืนข้อมูลว่าง"}

    stamp = storage.utc_now().strftime("%Y-%m-%d")
    path = storage.write_parquet(
        df,
        config.RAW_PRICES / f"{stamp}.parquet",
        kind="prices",
        period=period,
        tickers=len(tickers),
    )

    return {
        "ok": True,
        "rows": len(df),
        "tickers": df["ticker"].nunique(),
        "date_min": str(df["date"].min().date()),
        "date_max": str(df["date"].max().date()),
        "path": str(path.relative_to(config.ROOT)),
    }
