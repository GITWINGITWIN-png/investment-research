"""แหล่งข่าวหลายแหล่ง — ออกแบบให้แต่ละแหล่งล้มได้อิสระ

## ปัญหาที่แก้

เดิมใช้ GDELT แหล่งเดียว → เป็น single point of failure
พอโดน rate limit ก็ไม่ได้ข่าวเลยทั้งวัน

## แต่ประโยชน์จริงมากกว่าแค่ "สำรอง"

แต่ละแหล่งมี bias ของตัวเอง การมีหลายแหล่งจึงให้ภาพที่ครบกว่า:

| แหล่ง | ชนิด | จุดแข็ง | จุดอ่อน |
|-------|------|---------|---------|
| GDELT | aggregator | กว้างมาก นานาชาติ หลายภาษา | rate limit เข้ม · ไม่มีเนื้อบทความ |
| Yahoo | outlet | ผูกกับ ticker ตรง ๆ | เน้นสหรัฐฯ · ปนบทความชวนคลิก |
| RSS | outlet | ทันเหตุการณ์ สื่อหลัก | จำนวนจำกัด · ไม่ผูก ticker |
| SEC | **primary** | **เอกสารต้นทาง มีนัยสำคัญตามกฎหมาย** | ไม่ใช่ "ข่าว" · อ่านยาก |

`source_type` ถูกเก็บไว้ในทุก record เพื่อให้ตอนวิเคราะห์แยกน้ำหนักได้ —
8-K จาก SEC เป็นข้อเท็จจริงที่บริษัทต้องแจ้ง ส่วนบทความ aggregator
เป็นการตีความ ไม่ควรนับเท่ากัน

## สัญญาที่ทุก source ต้องทำตาม

- ฟังก์ชัน `fetch()` คืน `list[dict]` ที่ผ่าน `normalize()` แล้ว
- **ห้าม raise** — ล้มเหลวให้คืน list ว่างพร้อมพิมพ์เหตุผล
  เพราะแหล่งหนึ่งล่มต้องไม่ทำให้แหล่งอื่นเสียไปด้วย
"""

from __future__ import annotations

import re
from datetime import datetime
from functools import lru_cache
from typing import Any

from ... import config


def normalize(
    *,
    url: str,
    title: str,
    published_at: str | None,
    source: str,
    source_type: str,
    publisher: str = "",
    tickers: list[str] | None = None,
    tickers_hint: list[str] | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """บีบ record จากทุกแหล่งให้เป็นรูปแบบเดียวกัน

    สำคัญมากต่อ labeled corpus: ถ้าแต่ละแหล่งมีโครงสร้างต่างกัน
    label ที่ได้จะเทียบข้ามแหล่งไม่ได้ และ schema จะเน่าเมื่อเพิ่มแหล่งใหม่

    ## ทำไมต้องแยก tickers กับ tickers_hint

    บทเรียนจากข้อมูลจริง: Yahoo แสดง "ข่าวที่เกี่ยวข้อง" บนหน้าของแต่ละ ticker
    ซึ่ง**ไม่ได้แปลว่าข่าวนั้นเกี่ยวกับหุ้นตัวนั้น** — เจอข่าว P&G ติดป้าย NVDA

    ถ้าปล่อยให้การผูกผิดเข้าคลัง งาน text alpha ที่สร้างบนคลังนี้จะเสียทั้งหมด
    จึงแยกเป็น:
      `tickers`      = ยืนยันได้ (ชื่อปรากฏในพาดหัวจริง)
      `tickers_hint` = แหล่งบอกมา แต่ยังไม่ยืนยัน → ใช้เป็นคำใบ้เท่านั้น
    """
    return {
        "url": url.strip(),
        "title": (title or "").strip(),
        "published_at": published_at or "",
        "source": source,
        "source_type": source_type,   # aggregator | outlet | primary
        "publisher": publisher,
        "tickers": tickers or [],
        "tickers_hint": tickers_hint or [],
        "extra": extra or {},
    }


def _boundary(term: str) -> str:
    """regex ที่จับ `term` แบบเป็นคำเต็ม โดยนับเฉพาะอักษร ASCII เป็น "ตัวติดกัน"

    ## ทำไมไม่ใช้ `\\b`

    `\\b` ของ Python นับอักษรจีน/ญี่ปุ่นเป็น word character ด้วย
    พาดหัว "小心Amazon假平台..." จึงไม่มีขอบเขตคำรอบคำว่า Amazon → จับไม่ได้เลย
    ซึ่งเป็นปัญหาใหญ่เพราะ GDELT ส่งข่าวหลายภาษามาให้เป็นปกติ

    การนิยามขอบเขตด้วย `[A-Za-z0-9]` แทน แก้ทั้งสองด้านพร้อมกัน:
      "Metal"          → Meta ตามด้วย 'l' ซึ่งเป็น ASCII → ไม่จับ ✅
      "小心Amazon假"    → รอบข้างไม่ใช่ ASCII → จับได้ ✅
      "Apple's"        → ตามด้วย ' ซึ่งไม่ใช่ ASCII alnum → จับได้ ✅
    """
    return rf"(?<![A-Za-z0-9]){re.escape(term)}(?![A-Za-z0-9])"


@lru_cache(maxsize=1)
def _ticker_patterns() -> list[tuple[re.Pattern[str], str]]:
    """คอมไพล์ regex ครั้งเดียว — ฟังก์ชันนี้ถูกเรียกทุกบทความ (หลายร้อยชิ้น/วัน)

    เดิมใช้ `if ticker in title` (substring ล้วน) ซึ่งทำให้ META จับคำว่า "Metal"
    และ "metals" — พบข่าว "This Overlooked Metals Stock..." ถูกติด META จริงในคลัง
    ยิ่งเพิ่ม ticker สั้น ๆ ยิ่งพัง เพราะ CAT/KO/GE/LIN เป็นคำปกติทั้งหมด
    """
    patterns: list[tuple[re.Pattern[str], str]] = []

    for ticker in config.WATCHLIST:
        # ตัวย่อที่เป็นคำปกติในภาษาอังกฤษ จับจากชื่อบริษัทอย่างเดียว
        # ("CAT" ในข่าวอาจเป็นแมว, "KO" อาจเป็นน็อกเอาต์, "AMT" อาจเป็นภาษี)
        # หมายเหตุ: แหล่งที่รู้ ticker แน่นอนอยู่แล้ว (เช่น SEC) ส่งมาทาง `existing`
        # จึงไม่ได้รับผลจากข้อยกเว้นนี้
        if ticker in config.AMBIGUOUS_SYMBOLS:
            continue
        patterns.append((re.compile(_boundary(ticker), re.IGNORECASE), ticker))

    for name, ticker in config.COMPANY_ALIASES.items():
        patterns.append((re.compile(_boundary(name), re.IGNORECASE), ticker))

    return patterns


def tag_tickers(title: str, existing: list[str] | None = None) -> list[str]:
    """หา ticker จากพาดหัว — ทำฝั่งเราเอง ไม่เปลืองโควตา API

    ใช้ทั้งชื่อย่อและชื่อบริษัท เพราะข่าวมักเขียน "Nvidia" ไม่ใช่ "NVDA"
    ผลลัพธ์คือ `tickers` ที่ **ยืนยันแล้ว** ต่างจาก `tickers_hint` ที่แหล่งข่าวเดามาให้
    """
    found = set(existing or [])

    for pattern, ticker in _ticker_patterns():
        if pattern.search(title):
            found.add(ticker)

    return sorted(found)


def iso(value: Any) -> str:
    """แปลงวันที่หลายรูปแบบให้เป็น ISO — คืนสตริงว่างถ้าแปลงไม่ได้

    แต่ละแหล่งส่งวันที่มาคนละแบบ (epoch, RFC822, GDELT compact, ISO)
    การทำให้เป็นรูปแบบเดียวตรงนี้ทำให้ที่อื่นไม่ต้องรู้เรื่องนี้อีก
    """
    if not value:
        return ""

    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(value, tz=config.UTC).replace(microsecond=0).isoformat()
        except (ValueError, OSError):
            return ""

    text = str(value).strip()

    # GDELT: 20260803T060000Z
    if len(text) == 16 and text[8] == "T" and text.endswith("Z"):
        try:
            return datetime.strptime(text, "%Y%m%dT%H%M%SZ").replace(
                tzinfo=config.UTC
            ).isoformat()
        except ValueError:
            pass

    # ISO อยู่แล้ว (อาจลงท้ายด้วย Z)
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).isoformat()
    except ValueError:
        pass

    # RFC 822 จาก RSS
    try:
        from email.utils import parsedate_to_datetime
        return parsedate_to_datetime(text).isoformat()
    except (TypeError, ValueError):
        return ""
