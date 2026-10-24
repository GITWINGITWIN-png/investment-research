"""ชั้นเก็บข้อมูลที่บังคับวินัย point-in-time

หัวใจของไฟล์นี้คือ: **ทุกอย่างที่เขียนลงดิสก์ต้องมี `fetched_at`**

เหตุผล (ดู note "09 - Evaluation Playbook"):
ถ้าไม่รู้ว่าข้อมูลชิ้นหนึ่ง "เป็นที่รับรู้เมื่อไหร่" เราจะเผลอใช้ข้อมูลอนาคต
ในการตัดสินใจย้อนหลังโดยไม่รู้ตัว = look-ahead bias ซึ่งทำให้ผลทดสอบทั้งหมดเป็นโมฆะ

`fetched_at` คือ knowledge_date เวอร์ชันที่ระมัดระวังที่สุด: เรารู้แน่ว่า
ณ เวลานั้นข้อมูลนี้มีอยู่จริงแล้ว (เพราะเราเพิ่งดึงมาได้)
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd

from . import config


def utc_now() -> datetime:
    """เวลาปัจจุบันแบบ timezone-aware เสมอ — naive datetime เป็นบ่อเกิดของบั๊ก"""
    return datetime.now(tz=config.UTC)


def utc_stamp() -> str:
    """ISO string ที่ใช้เป็น fetched_at — วินาทีพอ ไม่ต้องละเอียดกว่านี้"""
    return utc_now().replace(microsecond=0).isoformat()


def ensure_dirs() -> None:
    """สร้างโฟลเดอร์ที่จำเป็นทั้งหมด เรียกซ้ำได้ไม่มีปัญหา"""
    for path in (
        config.RAW_PRICES,
        config.RAW_NEWS,
        config.RAW_CALENDAR,
        config.RAW_FUNDAMENTALS,
        config.DERIVED,
        config.PREDICTIONS,
        config.HYPOTHESES,
        config.BRIEFS,
        config.PLANS,
        config.PLAN_REQUESTS,
        config.JOURNAL_PLANS,
        config.ALERTS,
        config.JOURNAL_ALERTS,
    ):
        path.mkdir(parents=True, exist_ok=True)


def log_manifest(kind: str, path: Path, rows: int, **extra: Any) -> None:
    """เขียน 1 บรรทัดต่อ 1 ไฟล์ที่เก็บ — นี่คือ audit trail ของคลังข้อมูล

    ใช้ JSONL เพราะ append ได้เรื่อย ๆ โดยไม่ต้องอ่านไฟล์เดิม
    และถ้าไฟล์เสียกลางทาง บรรทัดก่อนหน้ายังอ่านได้
    """
    config.MANIFEST.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "fetched_at": utc_stamp(),
        "kind": kind,
        "path": str(path.relative_to(config.ROOT)),
        "rows": rows,
        **extra,
    }
    with config.MANIFEST.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")


def write_parquet(df: pd.DataFrame, path: Path, kind: str, **extra: Any) -> Path:
    """เขียน DataFrame พร้อมแปะ fetched_at และบันทึกลง manifest

    ถ้ามีไฟล์เดิมอยู่แล้วจะ **ไม่เขียนทับ** แต่เติม suffix ใหม่
    เพราะข้อมูลดิบที่เก็บไปแล้วคือหลักฐาน — การเขียนทับทำลาย audit trail
    """
    path.parent.mkdir(parents=True, exist_ok=True)

    if "fetched_at" not in df.columns:
        df = df.assign(fetched_at=utc_stamp())

    final = path
    counter = 1
    while final.exists():
        final = path.with_name(f"{path.stem}__v{counter}{path.suffix}")
        counter += 1

    df.to_parquet(final, index=False)
    log_manifest(kind, final, len(df), **extra)
    return final


def write_jsonl(records: list[dict[str, Any]], path: Path, kind: str, **extra: Any) -> Path:
    """เก็บข้อมูลกึ่งโครงสร้าง (เช่น ข่าว) แบบดิบที่สุด

    เจตนา: เก็บ response ตามที่ได้มาจริง ไม่ตีความ ไม่ตัดฟิลด์
    เพราะเราไม่รู้ว่าอีก 12 เดือนจะอยากใช้ฟิลด์ไหน — แต่ตอนนั้นย้อนไปเก็บใหม่ไม่ได้แล้ว
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    stamp = utc_stamp()

    final = path
    counter = 1
    while final.exists():
        final = path.with_name(f"{path.stem}__v{counter}{path.suffix}")
        counter += 1

    with final.open("w", encoding="utf-8") as fh:
        for record in records:
            fh.write(json.dumps({"fetched_at": stamp, **record}, ensure_ascii=False) + "\n")

    log_manifest(kind, final, len(records), **extra)
    return final


def read_all_prices() -> pd.DataFrame:
    """อ่าน snapshot ราคาทั้งหมดที่เคยเก็บ แล้วรวมเป็นตารางเดียว

    ถ้ามีวันซ้ำ (เช่น รัน 2 รอบในวันเดียว) ให้เก็บแถวที่ fetched_at ใหม่สุด
    เหตุผล: ราคาปิดอาจถูกปรับหลังตลาดปิด ค่าที่ดึงทีหลังจึงถูกต้องกว่า
    """
    files = sorted(config.RAW_PRICES.glob("*.parquet"))
    if not files:
        return pd.DataFrame()

    frames = [pd.read_parquet(f) for f in files]
    df = pd.concat(frames, ignore_index=True)

    df["date"] = pd.to_datetime(df["date"]).dt.tz_localize(None).dt.normalize()
    df = (
        df.sort_values("fetched_at")
        .drop_duplicates(subset=["ticker", "date"], keep="last")
        .sort_values(["ticker", "date"])
        .reset_index(drop=True)
    )
    return df


def price_matrix(field: str = "close") -> pd.DataFrame:
    """แปลงเป็นตาราง wide: index = วันที่, columns = ticker

    รูปแบบนี้คำนวณผลตอบแทน/โมเมนตัมได้ง่ายที่สุด
    """
    df = read_all_prices()
    if df.empty:
        return pd.DataFrame()
    return df.pivot(index="date", columns="ticker", values=field).sort_index()
