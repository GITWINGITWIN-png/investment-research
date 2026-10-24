"""เวลาของตลาดหุ้นสหรัฐ — คำถาม "ข้อมูลนี้รู้ได้เมื่อไหร่" ทุกข้อผ่านไฟล์นี้

## ทำไมต้องมีไฟล์นี้

ระบบรันจากประเทศไทย แต่ตลาดอยู่นิวยอร์ก ช่วงซื้อขายปกติคือ 09:30–16:00 ET
ซึ่งตรงกับ **20:30–03:00 เวลาไทย** (ช่วง EDT) หรือ 21:30–04:00 (ช่วง EST)

พอเปลี่ยนจาก cron 06:00 มาเป็น anacron ที่รัน "ตอนเครื่องเปิด" งานอาจรันกลางคืน
ขณะตลาดยังเปิดอยู่ — ถ้าไม่ระวัง เราจะเก็บราคา "ระหว่างวัน" ไว้ในช่อง close
และ brief/แผนจะตีความผิดว่าเป็นราคาปิด ฟังก์ชันที่นี่จึงนิยามกติกาให้ชัด:

1. **เก็บเฉพาะแท่งที่ปิดแล้ว** — ตลาดยังเปิดอยู่ ตัดแท่งของวันนี้ทิ้ง
2. **แผนมีผลที่ราคาปิดของวันทำการแรกที่ปิด *หลัง* เวลาตัดสินใจ**
   (เท่ากับส่งคำสั่ง market-on-close ได้จริง) — กัน look-ahead โดยโครงสร้าง

วันหยุดตลาดไม่ได้ใส่ปฏิทินไว้ตรงนี้โดยตั้งใจ: วันหยุดไม่มีแท่งราคาอยู่แล้ว
ฟังก์ชันที่ต้องรู้ "วันทำการจริง" จึงรับปฏิทินจากข้อมูลราคา (วันที่ SPY มีราคา)
แทนการดูแลรายการวันหยุดเอง ซึ่งผิดได้และต้องอัปเดตทุกปี
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pandas as pd

from . import config

ET = ZoneInfo("America/New_York")
SESSION_OPEN = time(9, 30)
SESSION_CLOSE = time(16, 0)


def utc_now() -> datetime:
    return datetime.now(tz=config.UTC)


def to_et(moment: datetime) -> datetime:
    return moment.astimezone(ET)


def is_session_open(now: datetime | None = None) -> bool:
    """ตลาดสหรัฐเปิดอยู่ตอนนี้ไหม (จันทร์–ศุกร์ 09:30–16:00 ET, ไม่นับวันหยุด)

    การไม่รู้วันหยุดไม่ทำให้ผิด: วันหยุดไม่มีแท่งราคาของวันนั้นให้ตัดอยู่แล้ว
    """
    et = to_et(now or utc_now())
    return et.weekday() < 5 and SESSION_OPEN <= et.time() < SESSION_CLOSE


def session_close_utc(day: date) -> datetime:
    """เวลาปิดตลาดของวันนั้นเป็น UTC — 16:00 ET แปลงตาม DST ของวันนั้นเอง"""
    return datetime.combine(day, SESSION_CLOSE, tzinfo=ET).astimezone(config.UTC)


def drop_incomplete_session(df: pd.DataFrame, now: datetime | None = None) -> tuple[pd.DataFrame, int]:
    """ตัดแท่งราคาของวันที่ตลาดยังซื้อขายอยู่ — คืน (ตารางที่เหลือ, จำนวนแถวที่ตัด)

    yfinance คืนแท่งของวันนี้ทั้งที่ตลาดยังไม่ปิด โดยใส่ราคาล่าสุดไว้ในช่อง close
    ถ้าเก็บไว้ snapshot วันนั้นจะมี "ราคาปิด" ที่ไม่ใช่ราคาปิดจริง
    """
    if df.empty or not is_session_open(now):
        return df, 0
    today_et = to_et(now or utc_now()).date()
    mask = pd.to_datetime(df["date"]).dt.date == today_et
    return df.loc[~mask].reset_index(drop=True), int(mask.sum())


def effective_session(created_at: datetime, sessions: pd.DatetimeIndex) -> pd.Timestamp | None:
    """วันทำการแรกที่ตลาด **ปิดหลัง** เวลาตัดสินใจ = วันที่แผนเริ่มมีผล

    ตัวอย่าง: แผนออกเสาร์ 06:00 ไทย → วันจันทร์คือวันแรกที่ปิดหลังจากนั้น
    → ปรับพอร์ตที่ราคาปิดวันจันทร์ ผลตอบแทนแรกที่นับคือวันอังคาร

    ระมัดระวังกว่าการนับตั้งแต่ราคาเปิดวันจันทร์ (เสียผลตอบแทนช่วงเช้าวันจันทร์ไป)
    แต่ไม่มีทางได้ประโยชน์จากข่าวสุดสัปดาห์ที่แผนอ่านไปแล้ว — ยอมเสียเปรียบดีกว่าโกง
    คืน None ถ้าวันนั้นยังมาไม่ถึง (ยังไม่มีราคาปิดในข้อมูล)
    """
    for day in sessions:
        if session_close_utc(day.date()) > created_at:
            return day
    return None


def last_close_before(created_at: datetime, sessions: pd.DatetimeIndex) -> pd.Timestamp | None:
    """วันทำการล่าสุดที่ปิดไปแล้ว ณ เวลาตัดสินใจ — ใช้เป็นราคาอ้างอิงของคำทำนาย"""
    past = [d for d in sessions if session_close_utc(d.date()) <= created_at]
    return past[-1] if past else None


def next_session_date(now: datetime | None = None) -> date:
    """วันทำการถัดไปที่ยังไม่ปิด (นับจันทร์–ศุกร์ ไม่รู้วันหยุด)

    ใช้ตัดสินว่า "แผนรายสัปดาห์ของสัปดาห์ไหน" — เช้าวันเสาร์เวลาไทย
    (= เย็นวันศุกร์ที่นิวยอร์ก หลังปิดตลาด) วันถัดไปคือจันทร์ → แผนของสัปดาห์หน้า
    """
    et = to_et(now or utc_now())
    day = et.date()
    if et.weekday() < 5 and et.time() < SESSION_CLOSE:
        return day
    day += timedelta(days=1)
    while day.weekday() >= 5:
        day += timedelta(days=1)
    return day


def iso_week(day: date) -> str:
    """ชื่อสัปดาห์แบบ ISO เช่น 2026-W44 — เรียงตามตัวอักษรได้ถูกลำดับเวลา"""
    year, week, _ = day.isocalendar()
    return f"{year}-W{week:02d}"


def sessions_from_prices(prices: pd.DataFrame) -> pd.DatetimeIndex:
    """ปฏิทินวันทำการจริงจากข้อมูล — วันที่ SPY มีราคา (ตัด BTC ที่เทรด 7 วันออก)"""
    if "SPY" in prices.columns:
        return prices["SPY"].dropna().index
    return prices.index
