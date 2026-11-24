"""ตัวประสานงานเก็บข่าวจากหลายแหล่ง

## หลักการ

**แหล่งหนึ่งล้ม ต้องไม่ทำให้แหล่งอื่นเสียไปด้วย**

แต่ละ source ถูกเรียกแยกกันและห่อด้วย try/except — ผลลัพธ์คือ
ต่อให้ GDELT โดนแบนทั้งสัปดาห์ เราก็ยังได้ข่าวจาก Yahoo/RSS/SEC ตามปกติ

## ลำดับการเรียก

เรียงจาก **เร็วและเชื่อถือได้มากที่สุด** ไปหาช้าที่สุด เพื่อให้ได้ข้อมูล
ติดมือก่อนเสมอ แม้ตัวท้าย ๆ จะล้มเหลว:

    yahoo (เร็ว ผูก ticker) → rss (เร็ว) → sec (ปานกลาง) → gdelt (ช้า เสี่ยงโดนแบน)

## การตัดข่าวซ้ำ

หลายแหล่งมักรายงานเรื่องเดียวกัน ถ้าไม่ตัดซ้ำจะเปลือง token ตอน label
ตัดด้วย URL ก่อน แล้วตามด้วยพาดหัวที่ normalize แล้ว (แหล่งต่างกันมัก
ให้ URL คนละแบบสำหรับบทความเดียวกัน เช่น มี tracking parameter ต่อท้าย)

เวลาเจอซ้ำ **เก็บอันที่ source_type ดีกว่าไว้** (primary > outlet > aggregator)
เพราะเอกสารต้นทางเชื่อถือได้กว่าการรายงานต่อ
"""

from __future__ import annotations

import re
from typing import Any, Callable

from .. import config, storage
from .sources import gdelt, rss, sec

# ลำดับความน่าเชื่อถือ ใช้ตัดสินว่าเก็บอันไหนเมื่อเจอข่าวซ้ำ
TYPE_RANK = {"primary": 3, "outlet": 2, "aggregator": 1}

SOURCES: dict[str, Callable[[], list[dict[str, Any]]]] = {
    "rss": rss.fetch,
    "sec": sec.fetch,
    "gdelt": gdelt.fetch,
}
# ฉบับสาธารณะไม่มีแหล่ง yahoo และ gnews: robots.txt ของ query1/query2.finance.yahoo.com
# และ news.google.com ไม่อนุญาตให้บอตอ่าน (ตรวจ 2026-11-25) ดู README หัวข้อแหล่งข้อมูล


def _title_key(title: str) -> str:
    """ทำพาดหัวให้เทียบกันได้ — ตัดวรรคตอนและตัวพิมพ์ใหญ่-เล็กออก

    ใช้จับข่าวเดียวกันที่มาจากคนละแหล่งแล้ว URL ไม่ตรงกัน
    """
    cleaned = re.sub(r"[^\w\s]", "", title.lower())
    return " ".join(cleaned.split())[:90]


def _dedupe(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """ตัดซ้ำโดยเก็บ record ที่มาจากแหล่งน่าเชื่อถือกว่าไว้"""
    best: dict[str, dict[str, Any]] = {}

    for record in records:
        url = record.get("url", "")
        title = record.get("title", "")
        if not url or not title:
            continue

        # ใช้พาดหัวเป็น key หลัก เพราะ URL ของข่าวเดียวกันต่างกันได้ตามแหล่ง
        # ยกเว้นเอกสารต้นทาง: URL ของ SEC filing ไม่ซ้ำกันเสมอ แต่พาดหัวซ้ำกันได้
        # (8-K สองฉบับของบริษัทเดียวกันในสัปดาห์เดียว พาดหัวเหมือนกันเป๊ะ)
        # ถ้าใช้พาดหัวเป็น key ฉบับหนึ่งจะหายไปเงียบ ๆ — บั๊กนี้มีอยู่จริงก่อน 2026-10-24
        key = url if record.get("source_type") == "primary" else (_title_key(title) or url)
        current = best.get(key)

        if current is None:
            best[key] = record
            continue

        # รวม ticker จากทั้งสอง record ไม่ว่าจะเก็บอันไหนไว้ — ข้อมูลจะได้ไม่หาย
        # แต่แยก confirmed กับ hint ต่อไป ไม่ปนกัน
        merged = sorted(set(record["tickers"]) | set(current["tickers"]))
        merged_hint = sorted(
            (set(record.get("tickers_hint", [])) | set(current.get("tickers_hint", [])))
            - set(merged)                       # ถ้ายืนยันได้แล้ว ไม่ต้องอยู่ใน hint อีก
        )

        winner = (
            record
            if TYPE_RANK.get(record["source_type"], 0) > TYPE_RANK.get(current["source_type"], 0)
            else current
        )
        best[key] = {**winner, "tickers": merged, "tickers_hint": merged_hint}

    return list(best.values())


def collect(sources: list[str] | None = None) -> dict[str, object]:
    """เก็บข่าวจากทุกแหล่งที่เปิดใช้ แล้วบันทึกเป็นไฟล์เดียว

    ถือว่า **สำเร็จ** ถ้าได้ข่าวจากอย่างน้อย 1 แหล่ง
    """
    storage.ensure_dirs()
    enabled = sources or config.NEWS_SOURCES

    collected: list[dict[str, Any]] = []
    per_source: dict[str, int] = {}
    failed: list[str] = []

    for name in enabled:
        fetcher = SOURCES.get(name)
        if fetcher is None:
            print(f"  ⚠️  ไม่รู้จักแหล่ง '{name}' — ข้าม")
            continue

        print(f"  ▸ {name}")
        try:
            found = fetcher()
        except Exception as exc:                       # noqa: BLE001 — กันแหล่งหนึ่งล้มลามทั้งระบบ
            print(f"  ⚠️  {name} ล้มเหลว: {type(exc).__name__}: {exc}")
            failed.append(name)
            continue

        per_source[name] = len(found)
        collected.extend(found)
        print(f"    → {len(found)} รายการ")

    if not collected:
        return {
            "ok": False,
            "rows": 0,
            "reason": f"ไม่ได้ข่าวจากแหล่งใดเลย (ลองแล้ว: {', '.join(enabled)})",
            "failed": failed,
        }

    unique = _dedupe(collected)

    stamp = storage.utc_now().strftime("%Y-%m-%d")
    path = storage.write_jsonl(
        unique,
        config.RAW_NEWS / f"{stamp}.jsonl",
        kind="news",
        sources=per_source,
        failed_sources=failed,
    )

    by_type: dict[str, int] = {}
    for record in unique:
        by_type[record["source_type"]] = by_type.get(record["source_type"], 0) + 1

    return {
        "ok": True,
        "rows": len(unique),
        "raw_rows": len(collected),
        "sources_ok": f"{len(per_source)}/{len(enabled)}",
        "by_type": by_type,
        "failed": failed,
        "path": str(path.relative_to(config.ROOT)),
    }
