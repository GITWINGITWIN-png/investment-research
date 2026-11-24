"""ประวัติราคาของ universe กว้าง — ใช้คิด "ความผันผวนปกติ" และ "ปริมาณซื้อขายปกติ"

ที่มา: ดึงย้อนหลังครั้งแรกด้วย yfinance (~9 เดือน) แล้วต่อด้วย snapshot ของ screener ทุกวัน
ผลตอบแทนรายวันใช้ % เปลี่ยนแปลงที่ Nasdaq คำนวณมา (ปรับการแตกพาร์แล้ว) ไม่ใช่ราคาปิดหารกันเอง

เก็บที่ data/research/ (ไม่ใช่ raw) เพราะสร้างใหม่ได้จาก raw/screener + yfinance เสมอ
"""

from __future__ import annotations

import time
from typing import Any

import numpy as np
import pandas as pd

from .. import config

PANEL = config.DATA / "research" / "broad_panel.parquet"


def load() -> pd.DataFrame:
    if not PANEL.exists():
        return pd.DataFrame(columns=["date", "ticker", "close", "ret", "volume"])
    df = pd.read_parquet(PANEL)
    df["date"] = pd.to_datetime(df["date"])
    return df


def _save(df: pd.DataFrame) -> None:
    PANEL.parent.mkdir(parents=True, exist_ok=True)
    df = df.sort_values(["ticker", "date"]).drop_duplicates(["ticker", "date"], keep="last")
    df.to_parquet(PANEL, index=False)


def _download(batch: list[str], period: str) -> tuple[pd.DataFrame, list[str]]:
    """ดึงหนึ่งก้อน คืน (ตารางยาว, ticker ที่ไม่ได้ข้อมูล)"""
    from ..datasource import yf
    raw = yf.download(batch, period=period, auto_adjust=True, progress=False, threads=True)
    if raw.empty:
        return pd.DataFrame(), list(batch)
    close = raw["Close"] if isinstance(raw.columns, pd.MultiIndex) else raw[["Close"]].set_axis(batch[:1], axis=1)
    volume = raw["Volume"] if isinstance(raw.columns, pd.MultiIndex) else raw[["Volume"]].set_axis(batch[:1], axis=1)
    close.index = volume.index = pd.to_datetime(close.index).tz_localize(None).normalize()
    long = pd.DataFrame({"close": close.stack(), "ret": (close.pct_change() * 100).stack(),
                         "volume": volume.stack()}).reset_index()
    long.columns = ["date", "ticker", "close", "ret", "volume"]
    long = long.dropna(subset=["close", "ret"])
    return long, sorted(set(batch) - set(long["ticker"]))


def update_recent(tickers: list[str], period: str = "10d", chunk: int = 150) -> dict[str, Any]:
    """ราคา/ปริมาณล่าสุดของทั้ง universe จาก yfinance — แหล่งราคาหลักของการสแกน

    เดิมใช้ราคาจาก Nasdaq screener แต่พบ (2026-10-25) ว่า screener คืนข้อมูลของวันพฤหัสทั้งที่
    วันศุกร์ปิดไปแล้ว — Claude สังเกตเห็นเองว่าแถววันศุกร์ซ้ำกับวันพฤหัสทุกตัว
    screener จึงเหลือหน้าที่ให้รายชื่อหุ้น มูลค่าตลาด และหมวดเท่านั้น

    ยิงทีละก้อนเล็ก เว้นจังหวะ และลองใหม่ให้ตัวที่ถูกปฏิเสธ (Yahoo จำกัดความถี่ — ยิงทีเดียว
    7,000 ตัวเคยโดนปฏิเสธ ~900 ตัว แล้วตัวเหล่านั้นค้างเป็นข้อมูลเก่า)
    """
    from .. import market

    frames, failed = [], []
    for start in range(0, len(tickers), chunk):
        long, missing = _download(tickers[start:start + chunk], period)
        frames.append(long)
        failed.extend(missing)
        time.sleep(2.0)
    if failed:
        time.sleep(20.0)
        retry_failed = []
        for start in range(0, len(failed), 50):
            long, missing = _download(failed[start:start + 50], period)
            frames.append(long)
            retry_failed.extend(missing)
            time.sleep(4.0)
        failed = retry_failed
    fresh = pd.concat([f for f in frames if not f.empty], ignore_index=True) if frames else pd.DataFrame()
    if fresh.empty:
        return {"ok": False, "latest": None, "failed": len(failed)}
    if market.is_session_open():
        today = pd.Timestamp(market.to_et(market.utc_now()).date())
        fresh = fresh[fresh["date"] != today]
    _save(pd.concat([load(), fresh], ignore_index=True))
    latest = fresh["date"].max()
    covered = fresh[fresh["date"] == latest]["ticker"].nunique()
    return {"ok": True, "rows": len(fresh), "latest": str(latest.date()), "failed": len(failed),
            "coverage": round(covered / max(len(tickers), 1), 3)}


def backfill(tickers: list[str], period: str = "9mo", chunk: int = 150) -> dict[str, int]:
    """ดึงประวัติให้ ticker ที่ยังมีน้อยกว่า 60 วัน — ทีละก้อน เว้นจังหวะกัน rate limit"""
    from ..datasource import yf
    panel = load()
    counts = panel.groupby("ticker").size() if not panel.empty else pd.Series(dtype=int)
    todo = [t for t in tickers if counts.get(t, 0) < 60][: config.SCAN_BACKFILL_PER_RUN]
    frames, got = [panel], 0
    for start in range(0, len(todo), chunk):
        batch = todo[start:start + chunk]
        raw = yf.download(batch, period=period, auto_adjust=True, progress=False, threads=True)
        if raw.empty:
            continue
        close = raw["Close"] if isinstance(raw.columns, pd.MultiIndex) else raw[["Close"]].set_axis(batch[:1], axis=1)
        volume = raw["Volume"] if isinstance(raw.columns, pd.MultiIndex) else raw[["Volume"]].set_axis(batch[:1], axis=1)
        long = pd.DataFrame({
            "close": close.stack(),
            "ret": (close.pct_change() * 100).stack(),
            "volume": volume.stack(),
        }).reset_index()
        long.columns = ["date", "ticker", "close", "ret", "volume"]
        long["date"] = pd.to_datetime(long["date"]).dt.tz_localize(None).dt.normalize()
        frames.append(long.dropna(subset=["close"]))
        got += long["ticker"].nunique()
        time.sleep(2.0)
    if got:
        _save(pd.concat(frames, ignore_index=True))
    return {"requested": len(todo), "filled": got}


def baselines(session: str) -> pd.DataFrame:
    """ต่อ ticker: ผลตอบแทนวันนั้น, z, 5 วัน, z 5 วัน, ปริมาณเทียบมัธยฐาน 20 วัน, มูลค่าซื้อขายมัธยฐาน"""
    panel = load()
    if panel.empty:
        return pd.DataFrame()
    panel = panel[panel["date"] <= pd.Timestamp(session)]
    ret = panel.pivot(index="date", columns="ticker", values="ret").sort_index()
    vol = panel.pivot(index="date", columns="ticker", values="volume").sort_index()
    close = panel.pivot(index="date", columns="ticker", values="close").sort_index()
    if pd.Timestamp(session) not in ret.index:
        return pd.DataFrame()
    sigma = ret.iloc[-(config.VOL_LOOKBACK + 5):-5].std()
    today = ret.loc[pd.Timestamp(session)]
    growth = (1 + ret.iloc[-5:] / 100).prod() - 1
    out = pd.DataFrame({
        "ret": today,
        "sigma": sigma,
        "z": today / sigma,
        "ret_5d": growth * 100,
        "z_5d": growth * 100 / (sigma * np.sqrt(5)),
        "volume_ratio": vol.loc[pd.Timestamp(session)] / vol.iloc[-21:-1].median(),
        "dollar_volume_median": (close * vol).iloc[-21:-1].median(),
        "history": ret.notna().sum(),
    })
    return out.replace([np.inf, -np.inf], np.nan)
