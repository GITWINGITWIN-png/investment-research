"""Work queue: says where the remaining tokens should go.

## The problem

"Lots of tokens left this month, but no idea what to use them on", so they expire unused.

## Ordering principle

    Spend tokens on what cannot be done later first, then move down to what can wait.

| Level | Task | Can it be done later? |
|-------|-----|-----------------|
| L1 | add analysis to the brief | ❌ contaminated by hindsight at once |
| L2 | label news | ❌ contaminated by hindsight at once |
| L3 | test hypotheses | ⚠️ yes, but slower |
| L4 | summarise papers into notes | ✅ any time |

L1 and L2 "rot" over time: the longer they wait, the less they are worth, because we start to know the outcome.
L4 does not rot, so it is the safe place to spend tokens when many are left.
"""

from __future__ import annotations

import json
import re
from typing import Any

from . import config, labels


def _brief_needs_analysis() -> list[dict[str, str]]:
    """Briefs still missing their analysis, oldest first (older ones rot faster)"""
    pending: list[dict[str, str]] = []
    for path in sorted(config.BRIEFS.glob("*.md")):
        text = path.read_text(encoding="utf-8")
        match = re.search(r"^analysis_status:\s*(\S+)", text, re.MULTILINE)
        if match and match.group(1).strip() == "pending":
            pending.append({"date": path.stem, "file": str(path.relative_to(config.ROOT))})
    return pending


def _hypotheses_backlog() -> list[dict[str, str]]:
    """Hypotheses not yet tested"""
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
    """Papers without a summary note, by tier (1 = read first)"""
    config.PAPER_NOTES.mkdir(parents=True, exist_ok=True)
    existing = {p.stem for p in config.PAPER_NOTES.glob("*.md")}
    unread = [p for p in config.PAPERS if p["id"] not in existing]
    return sorted(unread, key=lambda p: p["tier"])


def build() -> list[dict[str, Any]]:
    """Assemble the whole work queue in order of importance (L1 before L4)"""
    items: list[dict[str, Any]] = []

    # The old L1 = add analysis to the brief every day, which nobody actually did (all 45 were left undone)
    # Replaced by the automatic weekly plan; it enters the queue only when a plan is due and not yet issued
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
            # Titles in the files already start with the ID (such as "H-001: ..."), so it is not added again
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
    """Count the backlog per level"""
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
    """Write the queue as JSON for Claude to read in one go"""
    out_dir = config.DATA / "queue"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"workqueue{path_suffix}.json"
    out_path.write_text(
        json.dumps({"items": build(), "summary": summary()}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return str(out_path.relative_to(config.ROOT))
