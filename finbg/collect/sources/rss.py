"""ข่าวจาก RSS ของสื่อหลัก

ทำไม RSS ยังคุ้มในปี 2026 ทั้งที่ดูเป็นเทคโนโลยีเก่า:
- **ไม่ต้องมี API key และแทบไม่มี rate limit** — ต่างจาก API ข่าวทุกเจ้า
- เสถียรมาก มาตรฐานไม่เปลี่ยนมา 20 ปี
- ได้ข่าวจาก **สื่อต้นทางโดยตรง** ไม่ผ่าน aggregator ที่อาจตัดทอน

ฟีดของธนาคารกลาง (Fed) มีค่าเป็นพิเศษ — เป็น **แหล่งปฐมภูมิ**
ที่ประกาศนโยบายจริง ไม่ใช่การตีความของนักข่าว
"""

from __future__ import annotations

from typing import Any

from ... import config
from . import iso, normalize, tag_tickers

SOURCE_TYPE_BY_KIND = {"outlet": "outlet", "primary": "primary"}


def fetch(feeds: list[dict[str, str]] | None = None) -> list[dict[str, Any]]:
    """ดึงทุกฟีดที่ตั้งไว้ — ฟีดหนึ่งพังไม่กระทบฟีดอื่น"""
    import feedparser

    targets = feeds if feeds is not None else config.RSS_FEEDS
    records: list[dict[str, Any]] = []

    for feed in targets:
        name, url, kind = feed["name"], feed["url"], feed.get("kind", "outlet")
        try:
            parsed = feedparser.parse(url, agent=config.USER_AGENT)
        except Exception as exc:                      # noqa: BLE001
            print(f"  ⚠️  rss/{name}: {type(exc).__name__}")
            continue

        entries = getattr(parsed, "entries", [])
        if not entries:
            print(f"  ⚠️  rss/{name}: ไม่มีรายการ (ฟีดอาจย้ายที่)")
            continue

        for entry in entries[: config.RSS_MAX_PER_FEED]:
            link = entry.get("link", "")
            title = entry.get("title", "")
            if not link or not title:
                continue

            records.append(
                normalize(
                    url=link,
                    title=title,
                    published_at=iso(entry.get("published") or entry.get("updated")),
                    source=f"rss:{name}",
                    source_type=SOURCE_TYPE_BY_KIND.get(kind, "outlet"),
                    publisher=name,
                    # RSS ไม่บอก ticker ต้องเดาจากพาดหัวเอง
                    tickers=tag_tickers(title),
                    extra={"summary": (entry.get("summary", "") or "")[:400]},
                )
            )

    return records
