"""อ่านเนื้อข่าวเต็มจากหน้าบทความ — ผู้ใช้อนุญาต 2026-11-08 ("ดึงบทความมาอ่านลองก่อนก็ได้")

ใช้แค่สองที่: ตาราง 59 รายวันของ InfoQuest (วันละ ~1 หน้า) และข่าวของหุ้นที่ Opus ดำลึก (≤ 2 ตัว × 3 ข่าว/วัน)
— เป็นการอ่านแบบคนเปิดลิงก์ ไม่ใช่ crawler ไล่เก็บทั้งเว็บ

## กติกาที่โค้ดบังคับเอง (ไม่พึ่งว่าเคยตรวจมือไว้)

- ถาม robots.txt ของเว็บนั้นทุกครั้งก่อนดึง — ถ้าเจ้าของเว็บเปลี่ยนใจห้ามวันไหน ระบบหยุดเองวันนั้น
- เว้นอย่างน้อย 2 วินาทีระหว่างหน้า · ระบุตัวตนด้วย USER_AGENT ของโปรเจกต์ (ไม่ปลอมเป็นเบราว์เซอร์)
- เก็บเนื้อหาที่อ่านไว้ในคลัง PIT (`data/raw/th/articles/`) ไว้ใช้ส่วนตัว ไม่เผยแพร่ต่อ
"""

from __future__ import annotations

import re
import time
from functools import lru_cache
from typing import Any
from urllib import robotparser
from urllib.parse import urlsplit

import requests

from .. import config, storage

# คลาสที่ธีม WordPress ใช้ห่อเนื้อข่าว — ถ้าไม่เจอเลย ค่อยใช้ทุก <p> ในหน้า
_CONTENT = re.compile(r"entry-content|post-content|article-content|td-post-content|single-content|content-detail|news-content")
_last_fetch = 0.0


@lru_cache(maxsize=32)
def _robots(host: str) -> robotparser.RobotFileParser | None:
    """อ่าน robots.txt ด้วย USER_AGENT เดียวกับที่ใช้ดึงบทความ

    ทำไมไม่ใช้ `RobotFileParser.read()` ตรง ๆ: มันขอด้วย User-Agent ปริยายของ Python ซึ่งหลายเว็บข่าวไทย
    ตอบ 403 (พบจริง 2026-11-08: InfoQuest, ข่าวหุ้น) แล้วมันตีความว่า "ห้ามทั้งเว็บ" — ทั้งที่ robots.txt
    อนุญาตเรา · การถามด้วยตัวตนจริงของเราคือคำตอบที่ถูกสำหรับคำถาม "เว็บนี้ให้ *เรา* ดึงไหม"
    """
    parser = robotparser.RobotFileParser(f"https://{host}/robots.txt")
    try:
        response = requests.get(f"https://{host}/robots.txt", headers={"User-Agent": config.USER_AGENT},
                                timeout=config.TH_ARTICLE_TIMEOUT_SEC)
    except requests.RequestException:
        return None                                       # อ่าน robots ไม่ได้ = รอบนี้ไม่ดึง (ระวังไว้ก่อน)
    if response.status_code == 200:
        parser.parse(response.text.splitlines())
    elif response.status_code in (401, 403):
        parser.disallow_all = True                        # เว็บปฏิเสธเราจริง → เคารพ
    elif 400 <= response.status_code < 500:
        parser.allow_all = True                           # ไม่มี robots.txt = ไม่มีข้อจำกัด (ตามมาตรฐาน)
    else:
        return None
    return parser


def allowed(url: str) -> bool:
    parser = _robots(urlsplit(url).netloc)
    return bool(parser and parser.can_fetch(config.USER_AGENT, url))


def fetch_html(url: str) -> tuple[str | None, str]:
    """คืน (html, สถานะ) — ห้าม raise: บทความหนึ่งพังต้องไม่ทำให้ทั้งรอบพัง"""
    global _last_fetch
    if not allowed(url):
        return None, "robots_disallow"
    wait = config.TH_ARTICLE_DELAY_SEC - (time.monotonic() - _last_fetch)
    if wait > 0:
        time.sleep(wait)
    try:
        response = requests.get(url, headers={"User-Agent": config.USER_AGENT},
                                timeout=config.TH_ARTICLE_TIMEOUT_SEC)
    except requests.RequestException as exc:
        return None, f"error:{type(exc).__name__}"
    finally:
        _last_fetch = time.monotonic()
    if response.status_code != 200:
        return None, f"http_{response.status_code}"
    response.encoding = response.apparent_encoding or response.encoding
    return response.text, "ok"


def main_text(html: str) -> str:
    """เนื้อข่าวจากหน้า HTML — ตัดเมนู/โฆษณา/สคริปต์ แล้วเอาย่อหน้าในกล่องเนื้อข่าว"""
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "nav", "footer", "aside", "header", "form", "noscript", "iframe"]):
        tag.decompose()
    box = soup.find(class_=_CONTENT) or soup.find("article") or soup.body or soup
    paragraphs = [p.get_text(" ", strip=True) for p in box.find_all(["p", "li", "h2", "h3"])]
    text = "\n".join(p for p in paragraphs if len(p) > 1)
    return re.sub(r"[ \t ]+", " ", text).strip()


def read(items: list[dict[str, Any]], max_chars: int = config.TH_ARTICLE_MAX_CHARS) -> list[dict[str, Any]]:
    """ดึงเนื้อข่าวของรายการข่าว (ต้องมี url, title) → เก็บลงคลัง แล้วคืนพร้อม `text`"""
    out = []
    for item in items:
        html, status = fetch_html(item["url"])
        text = main_text(html)[:max_chars] if html else ""
        out.append({"url": item["url"], "title": item.get("title", ""), "publisher": item.get("publisher", ""),
                    "published_at": item.get("published_at", ""), "status": status, "chars": len(text), "text": text})
    if out:
        day = storage.utc_now().astimezone(config.LOCAL_TZ).date().isoformat()
        storage.write_jsonl(out, config.RAW_TH_ARTICLES / f"{day}.jsonl", kind="th_articles",
                            ok=sum(1 for o in out if o["status"] == "ok"))
    return out
