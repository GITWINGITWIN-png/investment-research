"""Labeled corpus — แปลงข่าวดิบเป็น label ที่มีโครงสร้าง (L2 บนบันไดการใช้ token)

## ทำไมงานนี้ต้องทำ "ตอนนี้" ไม่ใช่ทีหลัง

ข่าวดิบเก็บฟรีด้วยโค้ด แต่ label ต้องใช้ token — และที่สำคัญกว่านั้น:

**ถ้า label ข่าวเดือน ส.ค. 2026 ในปี 2027 โมเดลรู้ผลลัพธ์ไปแล้ว → label ปนเปื้อน hindsight**

โดยเฉพาะฟิลด์ `novelty` (ข่าวนี้ใหม่จริงหรือย้ำของเดิม) ซึ่งต้องอาศัย
"สิ่งที่รู้ ณ ตอนนั้น" เท่านั้น ทำย้อนหลังไม่ได้เลย

→ labeled corpus จึงเป็น **สินทรัพย์ที่ซื้อไม่ได้และสร้างย้อนหลังไม่ได้**
   และเป็นวัตถุดิบของ text alpha ในเฟส 3

## สถาปัตยกรรม (ออกแบบตามข้อจำกัด Claude Pro)

ผู้ใช้อยู่บน Claude Pro ไม่ได้จ่าย API แยก → cron เรียก LLM เองไม่ได้
จึงแบ่งเป็น 3 ขั้น:

    1. finbg label prepare   → เตรียม batch (โค้ดล้วน ฟรี)
    2. เปิด Claude Code สั่ง label  → ใช้ token ตรงนี้ที่เดียว
    3. finbg label ingest    → ตรวจความถูกต้องแล้วเก็บ (โค้ดล้วน ฟรี)

ข้อดีที่ตามมา: ขั้นที่ 2 จะทำเมื่อไหร่ก็ได้ที่ token เหลือ — ตรงกับโจทย์
"ใช้ token ส่วนเกินก่อนหมดอายุ" พอดี

## หมายเหตุเรื่องที่เก็บไฟล์

labels เก็บที่ `data/labels/` **ไม่ใช่ `data/derived/`** โดยตั้งใจ
เพราะ derived คือของที่ลบแล้วสร้างใหม่ได้ แต่ label สร้างใหม่ไม่ได้อย่างซื่อสัตย์
→ ต้องสำรองพร้อม raw เสมอ
"""

from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

from . import config, storage

SCHEMA_VERSION = 1

# --- นิยาม schema: แหล่งความจริงเดียว ---------------------------------------
# ทั้ง prompt ที่ส่งให้ LLM และ validator ถูกสร้างจากตรงนี้
# เพื่อไม่ให้ทั้งสองฝั่งหลุดจากกันเมื่อแก้ schema ในอนาคต

ENUMS: dict[str, list[str]] = {
    "regime": ["risk_on", "risk_off", "neutral"],
    "novelty": ["new", "developing", "repetition"],
    "topic": [
        "monetary_policy",
        "macro_data",
        "earnings",
        "company_specific",
        "geopolitics",
        "market_structure",
        "other",
    ],
    "horizon": ["intraday", "days", "weeks", "months"],
}

# ระดับ effort ที่ยอมรับ — ไม่บังคับกรอก เพราะบางสภาพแวดล้อมโมเดลไม่รู้ค่านี้
EFFORT_LEVELS = ["low", "medium", "high", "xhigh", "max"]

# ไม่ทำ whitelist ของชื่อโมเดล เพราะโมเดลใหม่ออกเรื่อย ๆ
# การบังคับรายชื่อจะทำให้ระบบพังทุกครั้งที่มีรุ่นใหม่ — ตรวจแค่รูปแบบพอ
MODEL_MAX_LEN = 64

FIELD_HELP: dict[str, str] = {
    "model": "**model ID ที่แน่นอนของคุณเอง** เช่น claude-opus-5 · "
             "จำเป็นต่อการตรวจ drift ของคลัง — ห้ามเดา ห้ามใส่ชื่อเล่น",
    "effort": "ระดับ effort ที่โปรเจกต์ตั้งไว้ — ใส่ตามที่ระบุในหัวข้อ 'สิ่งที่ต้องทำ' ห้ามเดาเอง",
    "regime": "ข่าวนี้ผลักตลาดไปทาง risk_on (กล้าเสี่ยง) หรือ risk_off (หนีความเสี่ยง)",
    "novelty": "new = ข้อมูลใหม่จริง · developing = เรื่องเดิมที่คืบหน้า · repetition = ย้ำของเดิม",
    "topic": "หมวดหลักของข่าว",
    "topic_free": "หัวข้อจริงแบบสั้น ๆ 2-6 คำ (ภาษาไทยหรืออังกฤษก็ได้) "
                  "— ใส่ไว้กันข้อมูลหายเวลาที่ topic ถูกยัดเป็น other",
    "horizon": "คาดว่าผลกระทบกินเวลานานแค่ไหน",
    "materiality": "0-100 · ข่าวส่วนใหญ่ควรต่ำกว่า 30 · เกิน 70 คือข่าวที่ขยับตลาดจริง",
    "entities": "หุ้นที่ข่าวนี้กระทบ พร้อมทิศทาง (positive/negative/neutral) และความมั่นใจ 0-100",
    "rationale": "เหตุผลสั้น ๆ 1 บรรทัด ว่าทำไมถึง label แบบนี้",
}

ENTITY_DIRECTIONS = ["positive", "negative", "neutral"]


def schema_prompt() -> str:
    """สร้างคำอธิบาย schema สำหรับใส่ในคำสั่ง label

    สร้างจาก ENUMS/FIELD_HELP โดยตรง → แก้ schema ที่เดียว prompt เปลี่ยนตาม
    """
    lines = ["ฟิลด์ที่ต้องใส่ให้ครบทุกบทความ:", ""]
    for field, help_text in FIELD_HELP.items():
        if field in ENUMS:
            allowed = " | ".join(ENUMS[field])
            lines.append(f"- `{field}` — เลือก 1 จาก: {allowed}")
            lines.append(f"    {help_text}")
        else:
            lines.append(f"- `{field}` — {help_text}")
    return "\n".join(lines)


# --- การตรวจความถูกต้อง ------------------------------------------------------

def validate(record: dict[str, Any]) -> list[str]:
    """ตรวจ 1 label — คืน list ของข้อผิดพลาด (ว่าง = ผ่าน)

    เข้มไว้ก่อน: label ที่ผิดรูปแบบเข้าคลังไปแล้วเจอทีหลัง แก้ไม่ได้
    (จะ label ใหม่ก็ปนเปื้อน hindsight ไปแล้ว)
    """
    errors: list[str] = []

    if not record.get("url"):
        errors.append("ไม่มี url (ใช้เป็น key เชื่อมกลับไปข่าวดิบ)")

    # provenance: ต้องรู้เสมอว่าโมเดลไหนเป็นคน label
    # ถ้าคลังปนหลายโมเดลโดยไม่รู้ตัว จะเกิด discontinuity ที่หน้าตาเหมือนสัญญาณจริง
    # แต่แท้จริงเป็นแค่การเปลี่ยนโมเดล — และ debug ย้อนหลังไม่ได้เลย
    model = record.get("model")
    if not model or not str(model).strip():
        errors.append("ขาด model (ต้องระบุ model ID ที่ label ข่าวนี้)")
    elif len(str(model)) > MODEL_MAX_LEN:
        errors.append(f"model ยาวเกิน {MODEL_MAX_LEN} ตัวอักษร — น่าจะใส่ผิดช่อง")

    effort = record.get("effort")
    if effort is not None and effort not in EFFORT_LEVELS:
        errors.append(f"effort='{effort}' ไม่อยู่ใน {EFFORT_LEVELS} (เว้นว่างได้ถ้าไม่รู้)")

    for field, allowed in ENUMS.items():
        value = record.get(field)
        if value is None:
            errors.append(f"ขาดฟิลด์ {field}")
        elif value not in allowed:
            errors.append(f"{field}='{value}' ไม่อยู่ใน {allowed}")

    topic_free = record.get("topic_free")
    if not topic_free or not str(topic_free).strip():
        errors.append("ขาด topic_free")
    elif len(str(topic_free)) > 80:
        errors.append("topic_free ยาวเกิน 80 ตัวอักษร (ควรสั้น 2-6 คำ)")

    materiality = record.get("materiality")
    if not isinstance(materiality, (int, float)) or not 0 <= materiality <= 100:
        errors.append("materiality ต้องเป็นตัวเลข 0-100")

    if not record.get("rationale"):
        errors.append("ขาด rationale")

    entities = record.get("entities")
    if not isinstance(entities, list):
        errors.append("entities ต้องเป็น list (ใส่ [] ได้ถ้าไม่เกี่ยวกับหุ้นตัวไหน)")
    else:
        for i, entity in enumerate(entities):
            if not isinstance(entity, dict):
                errors.append(f"entities[{i}] ต้องเป็น object")
                continue
            if not entity.get("ticker"):
                errors.append(f"entities[{i}] ขาด ticker")
            if entity.get("direction") not in ENTITY_DIRECTIONS:
                errors.append(f"entities[{i}].direction ต้องอยู่ใน {ENTITY_DIRECTIONS}")
            confidence = entity.get("confidence")
            if not isinstance(confidence, (int, float)) or not 0 <= confidence <= 100:
                errors.append(f"entities[{i}].confidence ต้องเป็น 0-100")

    return errors


# --- คลัง label --------------------------------------------------------------

def _labels_dir() -> Path:
    path = config.DATA / "labels"
    path.mkdir(parents=True, exist_ok=True)
    return path


def published_at_index() -> dict[str, str]:
    """แผนที่ url → published_at จากคลังข่าวดิบ **ทั้งหมด** (รวมที่ label ไปแล้ว)

    ต้องสแกนทั้งคลัง ไม่ใช่แค่ข่าวที่ยังไม่ label เพราะ:
    - ตอน ingest ซ้ำ ข่าวนั้นถูก label ไปแล้วจึงไม่อยู่ในรายการ "ยังไม่ label"
    - ตอน backfill ต้องหาวันที่ของข่าวที่ label ไปนานแล้ว
    ถ้าใช้รายการที่ยังไม่ label จะได้ค่าว่างและ days_after_publish กลายเป็น null เงียบ ๆ
    """
    index: dict[str, str] = {}
    for path in sorted(config.RAW_NEWS.glob("*.jsonl")):
        with path.open(encoding="utf-8") as fh:
            for line in fh:
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                url = record.get("url", "")
                published = record.get("published_at", "") or record.get("seendate", "")
                if url and published:
                    index.setdefault(url, published)
    return index


def days_between(published_raw: str, labeled_raw: str) -> int | None:
    """จำนวนวันระหว่างข่าวออกกับตอน label — คืน None ถ้าแปลงวันที่ไม่ได้

    แยกออกมาเป็นฟังก์ชันเพื่อให้ ingest กับ backfill ใช้ตรรกะเดียวกันเป๊ะ
    ไม่งั้นค่าที่เติมย้อนหลังอาจไม่ตรงกับค่าที่คำนวณตอนแรก
    """
    if not published_raw or not labeled_raw:
        return None
    try:
        published = datetime.fromisoformat(published_raw)
        labeled = datetime.fromisoformat(labeled_raw)
    except ValueError:
        return None
    if published.tzinfo is None:
        published = published.replace(tzinfo=config.UTC)
    if labeled.tzinfo is None:
        labeled = labeled.replace(tzinfo=config.UTC)
    return (labeled - published).days


def labeled_urls() -> set[str]:
    """URL ที่ label ไปแล้ว — ใช้กันการ label ซ้ำ (เปลือง token เปล่า)"""
    urls: set[str] = set()
    for path in _labels_dir().glob("*.jsonl"):
        with path.open(encoding="utf-8") as fh:
            for line in fh:
                try:
                    urls.add(json.loads(line).get("url", ""))
                except json.JSONDecodeError:
                    continue
    urls.discard("")
    return urls


def is_high_signal(record: dict[str, Any]) -> bool:
    """ข่าวนี้คุ้มค่า label ไหม

    เกณฑ์: เป็นเอกสารต้นทาง **หรือ** ยืนยันได้ว่าเกี่ยวกับหุ้นใน watchlist

    เหตุผลที่ต้องกรอง (ดู config.LABEL_HIGH_SIGNAL_ONLY): ปริมาณข่าวที่ cron เก็บ
    มากกว่าที่ label ไหวหลายเท่า และส่วนที่ตัดออกส่วนใหญ่เป็นบทความชวนคลิก
    กับประกาศที่ไม่เกี่ยวกับ universe ของเรา — ซึ่งจะได้ materiality ต่ำอยู่ดี
    การกรองจึงทำให้คลัง **ดีขึ้น** ไม่ใช่แย่ลง
    """
    if record.get("source_type") in config.LABEL_PRIMARY_TYPES:
        return True
    return bool(record.get("tickers"))


_SOURCE_TYPE_RANK = {"primary": 0, "outlet": 1, "aggregator": 2}


def sector_of(record: dict[str, Any]) -> str:
    """sector ของข่าว — จาก ticker ที่ยืนยันแล้วตัวแรก

    `tickers` ถูกเรียงไว้แล้วใน normalize() ผลจึงเหมือนเดิมทุกครั้งที่รัน
    ข่าวที่พูดถึงหลาย sector จะถูกนับที่ sector แรกตามลำดับตัวอักษร ซึ่งเป็นการตัดสิน
    ที่ไม่สมบูรณ์แต่คงเส้นคงวา — บันทึกไว้เป็นข้อจำกัดที่รู้ตัว

    ข่าวที่ไม่ผูกหุ้นตัวไหน (Fed, ตัวเลขเศรษฐกิจ, SEC ของบริษัทนอก watchlist)
    ไปอยู่ถัง "macro" ซึ่งมีโควตาของตัวเอง — กันไม่ให้ประกาศ Fed ท่วมจนเบียดข่าวหุ้น
    """
    for ticker in record.get("tickers") or []:
        sector = config.TICKER_SECTOR.get(ticker)
        if sector:
            return sector
    return "macro"


def _label_priority(record: dict[str, Any]) -> tuple[int, str, str]:
    """ลำดับความคุ้มค่าในการ label ภายในกลุ่มเดียวกัน

    ตอนเลือกเรายัง **ไม่รู้ materiality** เพราะนั่นคือผลลัพธ์ของการ label เอง
    จึงต้องใช้ตัวแทนที่รู้ได้ล่วงหน้า: เอกสารต้นทาง > สื่อ > สำนักข่าวรวม
    แล้วค่อยเรียงตามเวลา และปิดท้ายด้วย url เพื่อให้ผลเหมือนเดิมทุกครั้งที่รัน
    """
    return (
        _SOURCE_TYPE_RANK.get(record.get("source_type", ""), 9),
        record.get("published_at", ""),
        record.get("url", ""),
    )


def _quota_eligible(records: list[dict[str, Any]], quota: int) -> set[str]:
    """url ที่ผ่านโควตา — สูงสุด `quota` ชิ้น ต่อ (วันที่ข่าวออก, sector)

    ## ทำไมต้องเป็นเพดานถาวร ไม่ใช่แค่จัดลำดับ

    ถ้าแค่จัดลำดับให้สมดุลในแต่ละ batch loop จะวนจน label ครบทุกชิ้นอยู่ดี
    ผลคือ (ก) ค่าใช้จ่ายโตตามจำนวน ticker และ (ข) สัดส่วนกลับไปเอียงหา big tech
    เหมือนเดิม เพราะข่าวกลุ่มนั้นมีมากกว่ากลุ่มอื่นหลายเท่าโดยธรรมชาติ

    ## ทำไมต้องจัดอันดับรวมข่าวที่ label ไปแล้ว

    ถ้าจัดอันดับเฉพาะข่าวที่ยังไม่ label เพดานจะขยายตัวเองทุกรอบ —
    label ไป 6 ชิ้น อีก 6 ชิ้นถัดไปก็เลื่อนขึ้นมาอยู่ใน 6 อันดับแรกแทน ไม่มีวันจบ
    """
    buckets: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        day = (record.get("published_at") or "")[:10]
        buckets[(day, sector_of(record))].append(record)

    eligible: set[str] = set()
    for group in buckets.values():
        group.sort(key=_label_priority)
        eligible.update(r["url"] for r in group[:quota])
    return eligible


def _candidates(high_signal_only: bool) -> list[dict[str, Any]]:
    """ข่าวทั้งหมดที่ผ่านตัวกรอง — รวมที่ label ไปแล้ว (ต้องใช้ตอนจัดอันดับโควตา)

    คลังข่าวดิบเป็น "บันทึกการดึงข้อมูล" ไม่ใช่ตาราง unique — ตั้งใจให้เป็นแบบนั้น
    เพราะ PIT ห้ามเขียนทับ ทุกรอบที่ cron ดึงจึงได้ snapshot เต็มชุดพร้อม fetched_at ของตัวเอง
    ผลคือข่าวเดิมโผล่ซ้ำข้ามไฟล์ได้ตามปกติ → ผู้อ่านต้องกรองเอง ไม่ใช่ผู้เขียน
    เก็บตัวแรกที่เจอ (ไฟล์เรียงตามชื่อ = ตามเวลา) เพราะ fetched_at แรกสุด
    คือเวลาที่เรารู้เรื่องนี้จริง ๆ ซึ่งเป็นค่าที่ PIT ต้องการ
    """
    seen: set[str] = set()
    out: list[dict[str, Any]] = []

    for path in sorted(config.RAW_NEWS.glob("*.jsonl")):
        with path.open(encoding="utf-8") as fh:
            for line in fh:
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                url = record.get("url", "")
                if not url or url in seen:
                    continue
                seen.add(url)
                if high_signal_only and not is_high_signal(record):
                    continue
                out.append({
                    "url": url,
                    "title": record.get("title", ""),
                    "publisher": record.get("publisher", "") or record.get("domain", ""),
                    "source_type": record.get("source_type", ""),
                    "published_at": record.get("published_at", "") or record.get("seendate", ""),
                    "tickers": record.get("tickers", []),
                    "tickers_hint": record.get("tickers_hint", []),
                    "fetched_at": record.get("fetched_at", ""),
                    "source_file": path.name,
                })
    return out


def unlabeled_articles(
    limit: int | None = None,
    high_signal_only: bool | None = None,
    quota: int | None = None,
) -> list[dict[str, Any]]:
    """ข่าวที่ยังไม่ได้ label ผ่านโควตาต่อ sector แล้ว เรียงจากเก่าไปใหม่

    เรียงจากเก่าก่อนโดยตั้งใจ: ข่าวเก่าที่ยังไม่ label กำลัง "เสื่อม" ลงเรื่อย ๆ
    เพราะยิ่งทิ้งไว้นาน ยิ่งเสี่ยงว่าเราจะรู้ผลลัพธ์ไปแล้ว

    `quota=0` = ปิดโควตา (label ทุกชิ้นที่ผ่านตัวกรอง) — พฤติกรรมเดิมก่อนเพิ่ม sector
    """
    if high_signal_only is None:
        high_signal_only = config.LABEL_HIGH_SIGNAL_ONLY
    if quota is None:
        quota = config.LABEL_SECTOR_QUOTA

    candidates = _candidates(high_signal_only)
    eligible = _quota_eligible(candidates, quota) if quota else None

    done = labeled_urls()
    pending = [
        record for record in candidates
        if record["url"] not in done and (eligible is None or record["url"] in eligible)
    ]
    pending.sort(key=lambda r: (r.get("published_at", ""), r["url"]))

    return pending[:limit] if limit else pending


def prepare(batch_size: int = 40, high_signal_only: bool | None = None) -> dict[str, Any]:
    """เตรียมไฟล์คำสั่ง label 1 ชุด — ยังไม่ใช้ token

    ใส่เฉพาะข้อมูลที่จำเป็นต่อการ label (พาดหัว/แหล่ง/วันที่) ไม่ใส่ record ดิบทั้งก้อน
    เพราะทุกตัวอักษรใน batch คือ token ที่ต้องจ่าย
    """
    storage.ensure_dirs()
    articles = unlabeled_articles(limit=batch_size, high_signal_only=high_signal_only)
    if not articles:
        return {"ok": False, "reason": "ไม่มีข่าวที่รอ label"}

    queue_dir = config.DATA / "queue"
    queue_dir.mkdir(parents=True, exist_ok=True)

    stamp = storage.utc_now().strftime("%Y-%m-%dT%H%M")
    request_path = queue_dir / f"label-request-{stamp}.md"
    response_path = queue_dir / f"label-response-{stamp}.jsonl"

    payload = "\n".join(
        json.dumps(
            {k: a[k] for k in
             ("url", "title", "publisher", "source_type", "published_at",
              "tickers", "tickers_hint")},
            ensure_ascii=False,
        )
        for a in articles
    )

    request_path.write_text(
        f"""# คำสั่ง label ข่าว — batch {stamp}

**บทความ {len(articles)} ชิ้น · schema v{SCHEMA_VERSION}**

| ตั้งค่าโปรเจกต์ | ค่า |
|---|---|
| โมเดลที่ตั้งใจใช้ | `{config.LABEL_MODEL}` |
| effort ที่ตั้งไว้ | `{config.LABEL_EFFORT}` ← ใส่ค่านี้ในทุกบรรทัด |

## สิ่งที่ต้องทำ

อ่านบทความข้างล่าง แล้วเขียนไฟล์ `{response_path.name}` ในโฟลเดอร์เดียวกัน
เป็น JSONL — **1 บรรทัดต่อ 1 บทความ** ครบทุกชิ้น ไม่ข้าม

{schema_prompt()}

## กฎสำคัญ

1. **ตัดสินจากสิ่งที่รู้ ณ วันที่ข่าวออกเท่านั้น** ห้ามใช้ความรู้ว่าเกิดอะไรขึ้นภายหลัง
   — นี่คือหัวใจของ labeled corpus ถ้าละเมิดข้อนี้ ข้อมูลทั้งชุดใช้ไม่ได้
2. `materiality` ให้เข้มงวด — ข่าวส่วนใหญ่ไม่สำคัญ ควรต่ำกว่า 30
3. `entities` ใส่เฉพาะหุ้นที่ข่าว**กระทบจริง** ไม่ใช่แค่ถูกเอ่ยชื่อผ่าน ๆ
4. ถ้าไม่แน่ใจ ให้เลือกค่ากลาง (`neutral`) ดีกว่าเดา
5. `rationale` สั้น ๆ 1 บรรทัด อย่ายาว
6. **`tickers_hint` คือคำใบ้ที่ยังไม่ยืนยัน** — แหล่งข่าวบอกมาว่าอาจเกี่ยวข้อง
   แต่มักผิด (เช่น Yahoo แสดงข่าวตลาดทั่วไปบนหน้าของหุ้นแต่ละตัว)
   **อย่าเชื่อโดยอัตโนมัติ** ให้ตัดสินจากพาดหัวเอง ส่วน `tickers` คือที่ยืนยันแล้ว
7. `source_type: primary` = เอกสารต้นทาง (SEC filing, ประกาศธนาคารกลาง)
   เป็นข้อเท็จจริงที่ต้องแจ้งตามกฎหมาย ไม่ใช่การตีความ — น่าเชื่อถือกว่าข่าวทั่วไป
8. **`model` ต้องเป็น model ID จริงของคุณ** และต้องเหมือนกันทุกบรรทัด
   โปรเจกต์นี้ตั้งใจใช้ **`{config.LABEL_MODEL}`** ตลอดทั้งคลัง
   - ถ้าคุณคือโมเดลนั้น → ใส่ค่านั้น
   - **ถ้าคุณเป็นโมเดลอื่น → ใส่ ID จริงของคุณ แล้วบอกผู้ใช้ว่าไม่ตรงกับที่ตั้งใจไว้**
     อย่าคัดลอกค่าข้างบนถ้ามันไม่ใช่คุณ — ทั้งหมดนี้มีไว้เพื่อตรวจ drift
   - ไม่แน่ใจว่า ID ตัวเองคืออะไร → **ถามผู้ใช้ อย่าเดา**

## ตัวอย่าง 1 บรรทัด

```json
{{"url":"https://...","model":"{config.LABEL_MODEL}","effort":"{config.LABEL_EFFORT}","regime":"risk_off","novelty":"new","topic":"monetary_policy","topic_free":"Fed ส่งสัญญาณคงดอกเบี้ยนานขึ้น","horizon":"weeks","materiality":68,"entities":[{{"ticker":"SPY","direction":"negative","confidence":60}}],"rationale":"ถ้อยแถลงเข้มกว่าที่ตลาดคาด"}}
```

> ℹ️ **ความสม่ำเสมอสำคัญกว่าคุณภาพสูงสุด**
> คลัง label ที่ปนหลายโมเดลจะมีรอยต่อที่หน้าตาเหมือนสัญญาณตลาดจริง
> ทั้งที่เป็นแค่การเปลี่ยนโมเดล — ใช้โมเดลเดิมตลอดทั้งคลังถ้าเป็นไปได้

## บทความ

```jsonl
{payload}
```
""",
        encoding="utf-8",
    )

    return {
        "ok": True,
        "articles": len(articles),
        "model_expected": config.LABEL_MODEL,
        "filtered": high_signal_only if high_signal_only is not None
        else config.LABEL_HIGH_SIGNAL_ONLY,
        "request": str(request_path.relative_to(config.ROOT)),
        "response_expected": str(response_path.relative_to(config.ROOT)),
        "remaining_after": len(unlabeled_articles(high_signal_only=high_signal_only))
        - len(articles),
    }


def ingest(response_path: Path | str | None = None) -> dict[str, Any]:
    """อ่านไฟล์คำตอบ ตรวจความถูกต้อง แล้วเก็บเข้าคลัง

    label ที่ไม่ผ่านการตรวจจะ **ไม่ถูกเก็บ** และรายงานเหตุผลออกมา
    ยอมเสีย token ที่ label ไปแล้ว ดีกว่าปล่อยข้อมูลเสียเข้าคลังถาวร
    """
    queue_dir = config.DATA / "queue"

    if response_path is None:
        candidates = sorted(queue_dir.glob("label-response-*.jsonl"))
        if not candidates:
            return {"ok": False, "reason": "ไม่พบไฟล์คำตอบใน data/queue/"}
        target = candidates[-1]
    else:
        target = Path(response_path)
        if not target.is_absolute():
            target = config.ROOT / target

    if not target.exists():
        return {"ok": False, "reason": f"ไม่พบไฟล์ {target}"}

    published_at = published_at_index()

    # ด่านสุดท้ายก่อนเขียนถาวร: กัน label ซ้ำ URL เดิม
    # จำเป็นเพราะ ingest เขียนแบบ append — ถ้าซ้ำเข้าไปแล้วต้องแก้ไฟล์ด้วยมือ
    # ซ้ำได้สองทาง: (ก) ไฟล์คำตอบมี URL เดิมสองบรรทัด (ข) ingest ไฟล์เดิมซ้ำรอบ
    # การย้ายไฟล์เข้า done/ กันได้แค่ (ข) กรณีปกติ ไม่ครอบคลุมตอนสั่ง --path เอง
    already = labeled_urls()
    seen_in_file: set[str] = set()

    accepted: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    now = storage.utc_now()

    with target.open(encoding="utf-8") as fh:
        for line_no, line in enumerate(fh, start=1):
            line = line.strip()
            if not line or line.startswith("```"):
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError as exc:
                rejected.append({"line": line_no, "errors": [f"JSON เสีย: {exc}"]})
                continue

            errors = validate(record)
            if errors:
                rejected.append({"line": line_no, "url": record.get("url", "")[:60], "errors": errors})
                continue

            # validate ผ่านแล้วจึงเช็กซ้ำ — มั่นใจได้ว่า url มีจริงและเป็นสตริง
            url = record["url"]
            if url in already:
                rejected.append({"line": line_no, "url": url[:60], "errors": ["ซ้ำ: URL นี้อยู่ในคลังแล้ว"]})
                continue
            if url in seen_in_file:
                rejected.append({"line": line_no, "url": url[:60], "errors": ["ซ้ำ: URL นี้โผล่มาแล้วในไฟล์เดียวกัน"]})
                continue
            seen_in_file.add(url)

            # คำนวณว่า label ทำหลังข่าวออกกี่วัน — ยิ่งมากยิ่งเสี่ยงปนเปื้อน hindsight
            labeled_at = storage.utc_stamp()
            days_after = days_between(published_at.get(record["url"], ""), labeled_at)
            if days_after is None:
                print(f"  ⚠️  ไม่พบวันที่ของ {record['url'][:60]} → เติมภายหลังด้วย backfill ได้")

            accepted.append({
                "schema_version": SCHEMA_VERSION,
                "labeled_at": labeled_at,
                "days_after_publish": days_after,
                **record,
            })

    if not accepted:
        return {"ok": False, "reason": "ไม่มี label ที่ผ่านการตรวจ", "rejected": rejected[:10]}

    out_path = _labels_dir() / f"{now.strftime('%Y-%m-%d')}.jsonl"
    with out_path.open("a", encoding="utf-8") as fh:
        for record in accepted:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")

    storage.log_manifest("labels", out_path, len(accepted), source=target.name)

    # ย้ายไฟล์ที่ ingest แล้วออกจากคิว กันการ ingest ซ้ำ
    done_dir = queue_dir / "done"
    done_dir.mkdir(exist_ok=True)
    target.rename(done_dir / target.name)

    return {
        "ok": True,
        "accepted": len(accepted),
        "rejected": len(rejected),
        "reject_details": rejected[:10],
        "path": str(out_path.relative_to(config.ROOT)),
        "remaining_unlabeled": len(unlabeled_articles()),
    }


def backfill(dry_run: bool = False) -> dict[str, Any]:
    """เติม `days_after_publish` ที่เป็น null ย้อนหลัง

    ## ทำไมทำได้ (ทั้งที่เนื้อหา label ทำย้อนหลังไม่ได้)

    `days_after_publish` ไม่ใช่วิจารณญาณ แต่เป็น **เลขที่คำนวณจากสองค่าที่เก็บไว้แล้ว**:
        days_after_publish = labeled_at − published_at
    ทั้งคู่อยู่ในคลัง (labeled_at อยู่ในตัว label · published_at อยู่ในข่าวดิบ)
    จึงคำนวณใหม่ได้เสมอ ต่างจาก `novelty`/`materiality` ที่ถ้า label ช้าก็ปนเปื้อนไปแล้ว

    ## ความปลอดภัย

    - แตะเฉพาะฟิลด์ `days_after_publish` ที่เป็น null ฟิลด์อื่นไม่ยุ่ง
    - เขียนไฟล์ชั่วคราวแล้ว rename ทับ (atomic) — ไฟล์ไม่พังกลางคัน
    - ใช้ `labeled_at` ของ record นั้น ไม่ใช่เวลาปัจจุบัน
      (ไม่งั้น label ที่ทำไป 3 วันก่อนจะได้ค่าของวันนี้ ซึ่งผิด)
    """
    index = published_at_index()
    total = filled = still_null = 0
    touched_files: list[str] = []

    for path in sorted(_labels_dir().glob("*.jsonl")):
        records: list[dict[str, Any]] = []
        changed = False

        with path.open(encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue

                total += 1
                if record.get("days_after_publish") is None:
                    days = days_between(
                        index.get(record.get("url", ""), ""),
                        record.get("labeled_at", ""),
                    )
                    if days is not None:
                        record["days_after_publish"] = days
                        filled += 1
                        changed = True
                    else:
                        still_null += 1
                records.append(record)

        if changed and not dry_run:
            tmp = path.with_suffix(".jsonl.tmp")
            with tmp.open("w", encoding="utf-8") as fh:
                for record in records:
                    fh.write(json.dumps(record, ensure_ascii=False) + "\n")
            tmp.replace(path)                       # atomic — ไม่มีสถานะไฟล์ครึ่ง ๆ กลาง ๆ
            touched_files.append(path.name)
        elif changed:
            touched_files.append(path.name)

    return {
        "ok": True,
        "dry_run": dry_run,
        "total": total,
        "filled": filled,
        "still_null": still_null,
        "files": touched_files,
    }


def stats() -> dict[str, Any]:
    """สรุปคลัง label — ใช้ดูว่าสินทรัพย์โตแค่ไหนแล้ว"""
    records: list[dict[str, Any]] = []
    for path in sorted(_labels_dir().glob("*.jsonl")):
        with path.open(encoding="utf-8") as fh:
            for line in fh:
                try:
                    records.append(json.loads(line))
                except json.JSONDecodeError:
                    continue

    if not records:
        return {"total": 0, "unlabeled": len(unlabeled_articles())}

    def counts(field: str) -> dict[str, int]:
        out: dict[str, int] = {}
        for record in records:
            value = record.get(field)
            if value is not None:
                out[str(value)] = out.get(str(value), 0) + 1
        return dict(sorted(out.items(), key=lambda kv: -kv[1]))

    fresh = [r for r in records if isinstance(r.get("days_after_publish"), int)
             and r["days_after_publish"] <= 2]

    by_model = counts("model")

    # เตือนเมื่อคลังปนหลายโมเดล — เพราะรอยต่อระหว่างโมเดลจะดูเหมือนสัญญาณตลาดจริง
    # ตอนวิเคราะห์ทีหลัง ทั้งที่ไม่ใช่ ต้องรู้ตัวก่อนจะสร้างอะไรบนคลังนี้
    warnings: list[str] = []
    if len(by_model) > 1:
        share = " · ".join(f"{m} {n}" for m, n in by_model.items())
        warnings.append(f"คลังปน {len(by_model)} โมเดล ({share}) — รอยต่ออาจดูเหมือนสัญญาณปลอม")

    stale = [r for r in records if isinstance(r.get("days_after_publish"), int)
             and r["days_after_publish"] > 7]
    if stale:
        warnings.append(
            f"{len(stale)} label ทำหลังข่าวออกเกิน 7 วัน — เสี่ยงปนเปื้อน hindsight"
        )

    return {
        "total": len(records),
        "unlabeled": len(unlabeled_articles()),
        "labeled_within_2_days": len(fresh),
        "by_model": by_model,
        "by_effort": counts("effort"),
        "schema_versions": counts("schema_version"),
        "warnings": warnings,
        "regime": counts("regime"),
        "novelty": counts("novelty"),
        "topic": counts("topic"),
        "high_materiality": len([r for r in records if (r.get("materiality") or 0) >= 70]),
    }
