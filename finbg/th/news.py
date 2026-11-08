"""ข่าวหุ้นไทยจาก RSS ของสื่อ — ดึงวันละครั้ง แล้วย้อนหน้า feed จนครบ 30 ชั่วโมง

## ทำไมต้องย้อนหน้า feed

RSS ส่งแค่ 10–30 ข่าวล่าสุดต่อหน้า แต่สื่อหุ้นไทยออกข่าววันละหลายร้อยชิ้น
(ข่าวหุ้นออก ~10 ชิ้น/ชั่วโมงในวันทำการ) — ดึงวันละครั้งแค่หน้าแรกจะได้ไม่ถึง 10%
ทางแก้ที่ง่ายคือดึงทุกชั่วโมง แต่ผู้ใช้ไม่เอางานรายชั่วโมง · feed ของ WordPress รองรับ ?paged=N
จึงดึงวันละครั้งแล้วย้อนหน้าไปจนเจอข่าวเก่ากว่า 30 ชม. ได้ข่าวครบเท่ากัน
(ซ้อนกับรอบก่อน ~6 ชม. เผื่อเครื่องเปิดไม่ตรงเวลา — ข่าวซ้ำข้ามไฟล์ตัดที่ฝั่งผู้อ่าน ตามหลักของคลัง)

## ติดป้าย ticker จากพาดหัว

สื่อหุ้นไทยเขียนชื่อย่อหุ้นเป็นอักษรอังกฤษตัวพิมพ์ใหญ่ปนในประโยคไทย ("ADVANC กำไรโต...")
- ใช้ `_boundary` ตัวเดียวกับฝั่ง US — อักษรไทยนับเป็นขอบคำ จึงจับชื่อย่อที่ติดกับคำไทยได้
- **ตรงตัวพิมพ์ใหญ่** (ต่างจากฝั่ง US) — "true" ในประโยคอังกฤษไม่ใช่หุ้น TRUE
- ชื่อย่อที่เป็นคำทั่วไปด้วย (AI, CPI, META...) เก็บเป็น `tickers_hint` แทน `tickers`
  แบบเดียวกับฝั่ง US: `tickers` = ยืนยันแล้ว · `tickers_hint` = อาจใช่ ต้องยืนยันก่อนใช้ — ดู config.TH_AMBIGUOUS_SYMBOLS
"""

from __future__ import annotations

import re
import time
from datetime import datetime, timedelta
from typing import Any

from .. import config, storage
from ..collect.sources import _boundary, iso, normalize


Patterns = list[tuple[re.Pattern[str], str, bool]]      # (regex, symbol, ยืนยันได้ไหม)


def ticker_patterns(symbols: list[str]) -> Patterns:
    patterns = []
    for symbol in symbols:
        base = symbol.removesuffix(".BK")
        if len(base) < 2:
            continue                                     # ชื่อย่อตัวเดียว (เช่น M) จับผิดแทบทุกพาดหัว
        patterns.append((re.compile(_boundary(base)), symbol, base not in config.TH_AMBIGUOUS_SYMBOLS))
    return patterns


def tag(title: str, patterns: Patterns) -> tuple[list[str], list[str]]:
    """คืน (tickers ที่ยืนยันแล้ว, tickers_hint ที่อาจใช่)"""
    confirmed, hints = set(), set()
    for pattern, symbol, sure in patterns:
        if pattern.search(title):
            (confirmed if sure else hints).add(symbol)
    return sorted(confirmed), sorted(hints)


def _parse_time(value: str) -> datetime | None:
    try:
        return datetime.fromisoformat(value) if value else None
    except ValueError:
        return None


def fetch_feed(feed: dict[str, str], cutoff: datetime,
               patterns: Patterns) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """ย้อนหน้า feed จนข่าวเก่าสุดของหน้าเก่ากว่า cutoff — คืน (ข่าว, สถิติของ feed นี้)"""
    import feedparser

    found: dict[str, dict[str, Any]] = {}                 # key = URL · หน้าเลื่อนได้ถ้ามีข่าวใหม่ระหว่างดึง
    pages, reached = 0, False
    for page in range(1, config.TH_NEWS_MAX_PAGES + 1):
        url = feed["url"] if page == 1 else f"{feed['url']}?paged={page}"
        try:
            parsed = feedparser.parse(url, agent=config.USER_AGENT)
        except Exception as exc:                          # noqa: BLE001 — feed หนึ่งพังต้องไม่กระทบ feed อื่น
            print(f"  ⚠️  th-rss/{feed['name']} หน้า {page}: {type(exc).__name__}")
            break
        entries = getattr(parsed, "entries", [])
        if not entries:
            if page == 1:
                print(f"  ⚠️  th-rss/{feed['name']}: ไม่มีรายการ (feed อาจย้ายที่)")
            break
        pages += 1
        oldest = None
        for entry in entries:
            link, title = entry.get("link", ""), (entry.get("title", "") or "").strip()
            if not link or not title:
                continue
            published = iso(entry.get("published") or entry.get("updated"))
            when = _parse_time(published)
            if when is not None:
                oldest = when if oldest is None else min(oldest, when)
            confirmed, hints = tag(title, patterns)
            found.setdefault(link, normalize(
                url=link, title=title, published_at=published, source=f"th-rss:{feed['name']}",
                source_type="outlet", publisher=feed["name"], tickers=confirmed, tickers_hint=hints,
                extra={"summary": (entry.get("summary", "") or "")[:400],
                       "categories": [t.get("term", "") for t in entry.get("tags", []) if t.get("term")]},
            ))
        if oldest is not None and oldest < cutoff:
            reached = True
            break
        time.sleep(config.TH_NEWS_PAGE_DELAY_SEC)
    return list(found.values()), {"pages": pages, "items": len(found), "reached_cutoff": reached}


def collect(symbols: list[str]) -> dict[str, Any]:
    """ทุก feed → 1 ไฟล์ JSONL ต่อการรัน (ข้อมูลดิบตามที่เห็น ณ เวลานั้น)"""
    cutoff = storage.utc_now() - timedelta(hours=config.TH_NEWS_LOOKBACK_HOURS)
    patterns = ticker_patterns(symbols)
    records: list[dict[str, Any]] = []
    stats: dict[str, Any] = {}
    for feed in config.TH_NEWS_FEEDS:
        items, stats[feed["name"]] = fetch_feed(feed, cutoff, patterns)
        records += items
    if not records:
        return {"ok": False, "reason": "ไม่ได้ข่าวจาก feed ใดเลย", "feeds": stats}
    day = storage.utc_now().astimezone(config.LOCAL_TZ).date().isoformat()
    path = storage.write_jsonl(records, config.RAW_TH_NEWS / f"{day}.jsonl", kind="th_news", feeds=stats)
    tagged = sum(1 for r in records if r["tickers"])
    return {"ok": True, "items": len(records), "tagged": tagged, "feeds": stats, "path": str(path.relative_to(config.ROOT))}
