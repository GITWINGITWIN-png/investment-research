"""Create and track predictions and hypotheses (BG-1 and BG-4).

Why a command creates the files instead of writing them by hand:
**friction kills habits.** Having to remember the frontmatter structure every time
ends the habit within the first month, and BG-1 is the most valuable part of the whole system.

The `falsifier` field in the template matters most: if you cannot write what would prove you wrong,
what you wrote is a feeling, not a prediction.
"""

from __future__ import annotations

import re
from datetime import timedelta
from typing import Any

from . import config, storage


def _slugify(text: str) -> str:
    """Turn a topic into a safe file name; Thai is supported"""
    slug = re.sub(r"[^\w฀-๿\s-]", "", text, flags=re.UNICODE)
    slug = re.sub(r"[\s_]+", "-", slug.strip())
    return slug[:60] or "untitled"


def new_prediction(title: str, horizon_days: int = 90, confidence: int = 50) -> dict[str, Any]:
    """Create a new prediction file with frontmatter the system can read"""
    storage.ensure_dirs()

    today = storage.utc_now().date()
    resolve_by = today + timedelta(days=horizon_days)
    path = config.PREDICTIONS / f"{today}-{_slugify(title)}.md"

    if path.exists():
        return {"ok": False, "reason": f"มีไฟล์นี้อยู่แล้ว: {path.name}"}

    content = f"""---
date: {today}
resolve_by: {resolve_by}
confidence: {confidence}
resolved: null
outcome: null
tags: [prediction]
---

# {title}

## เหตุผล
<!-- ทำไมถึงคิดแบบนี้ อ้างอิงข้อมูลอะไร -->


## อะไรจะพิสูจน์ว่าผมผิด
<!-- ถ้าเขียนช่องนี้ไม่ได้ แสดงว่านี่ไม่ใช่คำทำนาย แต่เป็นความรู้สึก -->


## เกณฑ์ตัดสิน
<!-- ต้องชัดพอที่คนอื่นมาอ่านแล้วตัดสินได้ตรงกัน ไม่ต้องตีความ -->


## ข้อมูล ณ วันที่ทำนาย
<!-- อ้าง brief ที่ใช้ตัดสินใจ เพื่อให้ย้อนกลับมาดูได้ว่าตอนนั้นรู้อะไร -->
- [[{today}]]

---

## ผลลัพธ์
<!-- เติมตอนครบกำหนด แล้วเปลี่ยน resolved: true และ outcome: correct/wrong/partial -->
*(ยังไม่ครบกำหนด)*
"""
    path.write_text(content, encoding="utf-8")
    return {
        "ok": True,
        "path": str(path.relative_to(config.ROOT)),
        "resolve_by": str(resolve_by),
        "confidence": confidence,
    }


def new_hypothesis(title: str, cost: str = "medium") -> dict[str, Any]:
    """Create a new hypothesis card: write it down now, test it later.

    The point is to separate "having an idea" (free, any time) from
    "testing an idea" (expensive, once a month), which makes much better use of tokens.
    """
    storage.ensure_dirs()

    existing = sorted(config.HYPOTHESES.glob("H-*.md"))
    next_id = len(existing) + 1
    slug = _slugify(title)
    path = config.HYPOTHESES / f"H-{next_id:03d}-{slug}.md"

    content = f"""---
id: H-{next_id:03d}
created: {storage.utc_now().date()}
status: backlog
cost_estimate: {cost}
tags: [hypothesis]
---

# H-{next_id:03d}: {title}

## ทำไมถึงคิดแบบนี้
<!-- ที่มาของไอเดีย เจอจากไหน -->


## ทดสอบยังไง
<!-- ต้องใช้ข้อมูลอะไร คำนวณอะไร ใช้เวลาเท่าไหร่ -->


## ผลแบบไหนแปลว่าผิด
<!-- กำหนดล่วงหน้า ก่อนเห็นข้อมูล — กันการหลอกตัวเองภายหลัง -->


## บันทึกการทดสอบ
*(ยังไม่ได้ทดสอบ)*
"""
    path.write_text(content, encoding="utf-8")
    return {"ok": True, "id": f"H-{next_id:03d}", "path": str(path.relative_to(config.ROOT))}


def due_predictions() -> list[dict[str, str]]:
    """Predictions that are due but have no recorded outcome; the brief reminds us to settle them"""
    today = str(storage.utc_now().date())
    due: list[dict[str, str]] = []

    for path in sorted(config.PREDICTIONS.glob("*.md")):
        text = path.read_text(encoding="utf-8")
        if not text.startswith("---"):
            continue
        front = text.split("---", 2)[1]
        meta = {
            key.strip(): value.strip()
            for key, _, value in (line.partition(":") for line in front.splitlines())
            if key.strip()
        }
        resolved = meta.get("resolved", "null")
        resolve_by = meta.get("resolve_by", "")
        if resolved in ("null", "", "false") and resolve_by and resolve_by <= today:
            due.append({"file": path.stem, "resolve_by": resolve_by})

    return due
