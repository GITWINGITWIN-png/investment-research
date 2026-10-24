"""Work queue — บอกว่า token ที่เหลือควรไปลงตรงไหน

## ปัญหาที่แก้

"เดือนนี้ token เหลือเยอะ แต่ไม่รู้จะใช้ทำอะไร" → สุดท้ายหมดอายุไปเปล่า ๆ

## หลักการจัดลำดับ

    ใช้ token กับสิ่งที่ย้อนหลังทำไม่ได้ก่อน แล้วค่อยลงมาที่สิ่งที่ทำทีหลังได้

| ระดับ | งาน | ย้อนหลังทำได้ไหม |
|-------|-----|-----------------|
| L1 | เติมบทวิเคราะห์ใน brief | ❌ ปนเปื้อน hindsight ทันที |
| L2 | label ข่าว | ❌ ปนเปื้อน hindsight ทันที |
| L3 | ทดสอบสมมติฐาน | ⚠️ ได้ แต่ช้าลง |
| L4 | สรุป paper เป็นโน้ต | ✅ ได้ตลอด |

L1/L2 "เน่า" ตามเวลา — ทิ้งไว้ยิ่งนานยิ่งไร้ค่า เพราะเราเริ่มรู้ผลลัพธ์แล้ว
L4 ไม่เน่า จึงเป็น "ที่ทิ้ง token" ที่ปลอดภัยเมื่อเหลือเยอะ
"""

from __future__ import annotations

import json
import re
from typing import Any

from . import config, labels


def _brief_needs_analysis() -> list[dict[str, str]]:
    """brief ที่ยังไม่ได้เติมบทวิเคราะห์ — เรียงเก่าไปใหม่ (ของเก่าเน่าเร็วกว่า)"""
    pending: list[dict[str, str]] = []
    for path in sorted(config.BRIEFS.glob("*.md")):
        text = path.read_text(encoding="utf-8")
        match = re.search(r"^analysis_status:\s*(\S+)", text, re.MULTILINE)
        if match and match.group(1).strip() == "pending":
            pending.append({"date": path.stem, "file": str(path.relative_to(config.ROOT))})
    return pending


def _hypotheses_backlog() -> list[dict[str, str]]:
    """สมมติฐานที่ยังไม่ได้ทดสอบ"""
    backlog: list[dict[str, str]] = []
    for path in sorted(config.HYPOTHESES.glob("*.md")):
        text = path.read_text(encoding="utf-8")
        status = re.search(r"^status:\s*(\S+)", text, re.MULTILINE)
        cost = re.search(r"^cost_estimate:\s*(\S+)", text, re.MULTILINE)
        if status and status.group(1).strip() == "backlog":
            title = next((l.lstrip("# ").strip() for l in text.splitlines() if l.startswith("# ")), path.stem)
            backlog.append({
                "id": path.stem.split("-")[0] + "-" + path.stem.split("-")[1],
                "title": title,
                "cost": cost.group(1).strip() if cost else "medium",
                "file": str(path.relative_to(config.ROOT)),
            })
    return backlog


def _papers_unread() -> list[dict[str, str]]:
    """paper ที่ยังไม่มีโน้ตสรุป เรียงตาม tier (1 = ควรอ่านก่อน)"""
    config.PAPER_NOTES.mkdir(parents=True, exist_ok=True)
    existing = {p.stem for p in config.PAPER_NOTES.glob("*.md")}
    unread = [p for p in config.PAPERS if p["id"] not in existing]
    return sorted(unread, key=lambda p: p["tier"])


def build() -> list[dict[str, Any]]:
    """ประกอบคิวงานทั้งหมด เรียงตามความสำคัญ (L1 ก่อน L4)"""
    items: list[dict[str, Any]] = []

    # L1 เดิม = เติมบทวิเคราะห์ใน brief ทุกวัน → ไม่มีใครทำจริง (45 ฉบับค้างหมด)
    # แทนด้วยแผนรายสัปดาห์ที่ออกเองอัตโนมัติ — ขึ้นคิวเฉพาะเมื่อถึงเวลาแล้วยังไม่ออก
    from .pm import runner as pm_runner

    plan_state = pm_runner.due()
    if plan_state["due"]:
        items.append({
            "level": "L1",
            "perishable": True,
            "cost": "กลาง (1 session)",
            "what": f"แผนรายสัปดาห์ {plan_state['week']} ยังไม่ออก ({plan_state['reason']})",
            "why": "แผนลงวันที่ = track record ของ PM · ออกช้าเท่าไหร่ สัปดาห์นั้นยิ่งสั้นลง",
            "how": "uv run finbg plan run",
        })

    for brief in _brief_needs_analysis():
        items.append({
            "level": "L1",
            "perishable": True,
            "cost": "เล็ก",
            "what": f"เติมบทวิเคราะห์ใน brief {brief['date']}",
            "why": "บทวิเคราะห์ลงวันที่ = หลักฐานว่าคิดอะไร ณ ตอนนั้น ทำย้อนหลังไม่ได้",
            "how": f"อ่าน data/derived/context/{brief['date']}.json "
                   f"แล้วเติมหัวข้อ 'การวิเคราะห์' ใน {brief['file']}",
        })

    unlabeled = len(labels.unlabeled_articles())
    if unlabeled:
        batches = (unlabeled + 39) // 40
        items.append({
            "level": "L2",
            "perishable": True,
            "cost": f"กลาง ({batches} batch)",
            "what": f"label ข่าว {unlabeled} ชิ้นที่ค้างอยู่",
            "why": "labeled corpus สร้างย้อนหลังไม่ได้อย่างซื่อสัตย์ — วัตถุดิบของ text alpha เฟส 3",
            "how": "uv run finbg label prepare  →  ให้ Claude ทำตามไฟล์คำสั่ง  →  uv run finbg label ingest",
        })

    for hypothesis in _hypotheses_backlog():
        items.append({
            "level": "L3",
            "perishable": False,
            "cost": hypothesis["cost"],
            # title ในไฟล์ขึ้นต้นด้วย ID อยู่แล้ว (เช่น "H-001: ...") จึงไม่ต้องเติมซ้ำ
            "what": f"ทดสอบ {hypothesis['title'][:70]}",
            "why": "เปลี่ยนไอเดียเป็นผลลัพธ์ที่วัดได้",
            "how": f"เปิด {hypothesis['file']} แล้วทำตามหัวข้อ 'ทดสอบยังไง'",
        })

    for paper in _papers_unread():
        items.append({
            "level": "L4",
            "perishable": False,
            "cost": "กลาง",
            "what": f"[tier {paper['tier']}] สรุป paper: {paper['title'][:60]}",
            "why": "ความรู้ถาวร ไม่เน่า — ที่ทิ้ง token ที่ปลอดภัยเมื่อเหลือเยอะ",
            "how": f"อ่าน {paper['url']} แล้วเขียนโน้ตที่ Journal/Papers/{paper['id']}.md",
        })

    return items


def summary() -> dict[str, Any]:
    """นับงานคงค้างแยกตามระดับ"""
    items = build()
    by_level: dict[str, int] = {}
    for item in items:
        by_level[item["level"]] = by_level.get(item["level"], 0) + 1
    return {
        "total": len(items),
        "by_level": by_level,
        "perishable": len([i for i in items if i["perishable"]]),
    }


def export(path_suffix: str = "") -> str:
    """เขียนคิวเป็น JSON ให้ Claude อ่านได้ทีเดียวจบ"""
    out_dir = config.DATA / "queue"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"workqueue{path_suffix}.json"
    out_path.write_text(
        json.dumps({"items": build(), "summary": summary()}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return str(out_path.relative_to(config.ROOT))
