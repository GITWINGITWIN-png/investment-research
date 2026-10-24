"""คลังแผน — `data/plans/<plan_id>.json` (ห้ามแก้หลังบันทึก) + หน้าอ่านใน Obsidian

## ทำไมแผนแก้ไม่ได้

แผนคือ "คำทำนายที่บันทึกก่อนรู้ผล" รูปแบบหนึ่ง ถ้าแก้ย้อนหลังได้ track record ก็ไร้ความหมาย
อยากเปลี่ยนใจกลางสัปดาห์ → ออกแผนใหม่ (replan) ซึ่งมีเวลาของตัวเองและมีผลไปข้างหน้าเท่านั้น

plan_id: แผนรายสัปดาห์ = "2026-W44" · แผนฉุกเฉิน = "2026-W44-r1", "-r2", ...
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from .. import config, storage
from . import mandate


def path_for(plan_id: str) -> Path:
    return config.PLANS / f"{plan_id}.json"


def save(record: dict[str, Any]) -> Path:
    storage.ensure_dirs()
    path = path_for(record["plan_id"])
    if path.exists():
        raise FileExistsError(f"มีแผน {record['plan_id']} อยู่แล้ว — แผนแก้ไม่ได้ ให้ออก replan แทน")
    path.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    storage.log_manifest("plan", path, 1, plan_id=record["plan_id"], plan_kind=record["kind"])
    return path


def load_all() -> list[dict[str, Any]]:
    records = []
    for path in sorted(config.PLANS.glob("*.json")):
        try:
            records.append(json.loads(path.read_text(encoding="utf-8")))
        except json.JSONDecodeError:
            continue
    return sorted(records, key=lambda r: r["created_at"])


def latest() -> dict[str, Any] | None:
    records = load_all()
    return records[-1] if records else None


def for_week(week: str) -> list[dict[str, Any]]:
    return [r for r in load_all() if r.get("week") == week]


def current_targets() -> tuple[dict[str, float], float, str | None]:
    """น้ำหนักเป้าหมายของ satellite ตามแผนล่าสุด — ก่อนแผนแรกคือ control"""
    record = latest()
    if record is None:
        satellite, cash = mandate.passive_satellite()
        return satellite, cash, None
    return dict(record["targets"]["satellite"]), float(record["targets"]["cash"]), record["plan_id"]


def next_replan_id(week: str) -> str:
    existing = [r["plan_id"] for r in for_week(week) if r.get("kind") == "replan"]
    return f"{week}-r{len(existing) + 1}"


def _created_local(record: dict[str, Any]) -> str:
    created = datetime.fromisoformat(record["created_at"]).astimezone(config.LOCAL_TZ)
    return created.strftime("%Y-%m-%d %H:%M")


def render(record: dict[str, Any], status: dict[str, Any] | None = None) -> Path:
    """หน้า Obsidian ของแผน — อ่านจบในหน้าเดียวว่าคิดอะไร ถืออะไร จะถอยเมื่อไหร่"""
    plan = record["plan"]
    targets = record["targets"]
    previous = record.get("previous_targets") or {}
    prev_sat = previous.get("satellite", {})
    refs = record.get("references", {})
    sleeve = 1.0 - config.PM_CORE_WEIGHT

    def evidence_links(items: list[str]) -> str:
        """แปลง ID ข่าว (N12) ในหลักฐานเป็นลิงก์ — ข้อความอื่นคงไว้ตามเดิม"""
        import re

        def link(match: re.Match[str]) -> str:
            ref = refs.get(match.group(0))
            return f"[{match.group(0)}]({ref['url']})" if ref and ref.get("url") else match.group(0)

        rendered = [re.sub(r"\bN\d{1,4}\b", link, item.strip()) for item in items]
        return " · ".join(rendered) if rendered else "—"

    regime = plan.get("regime", {})
    stance_icon = {"risk_on": "🟢", "neutral": "⚪", "risk_off": "🔴"}.get(regime.get("stance"), "⚪")
    lines = [
        "---",
        f"plan_id: {record['plan_id']}",
        f"kind: {record['kind']}",
        f"week: {record['week']}",
        f"created_at: {record['created_at']}",
        f"model: {', '.join(record.get('model_used') or []) or '?'}",
        f"stance: {regime.get('stance', '?')}",
        "tags: [plan, portfolio]",
        "---",
        "",
        f"# 🧭 แผน {record['plan_id']}" + (" — วางแผนใหม่ระหว่างสัปดาห์" if record["kind"] == "replan" else ""),
        "",
        f"*ออกเมื่อ {_created_local(record)} (เวลาไทย) · ข้อมูลราคาถึง {record['information']['prices_as_of']} · "
        f"มีผลที่ราคาปิดของวันทำการแรกที่ตลาดปิดหลังเวลานี้*",
        "",
    ]
    if record["kind"] == "replan" and record.get("trigger"):
        lines += ["> [!warning] เหตุที่วางแผนใหม่", *[f"> - {a}" for a in record["trigger"]], ""]
    if record.get("warnings"):
        lines += ["> [!note] หมายเหตุจากตัวตรวจ", *[f"> - {w}" for w in record["warnings"]], ""]

    lines += [
        "## มุมมองหลัก",
        "",
        plan.get("thesis", ""),
        "",
        f"**ระบอบตลาด:** {stance_icon} `{regime.get('stance')}` (มั่นใจ {regime.get('confidence')}%) — {regime.get('summary', '')}",
        "",
        "## ทบทวนสัปดาห์ที่แล้ว",
        "",
        f"- **เกิดอะไรขึ้น:** {plan['review'].get('what_happened', '')}",
        f"- **ถูกตรงไหน:** {plan['review'].get('what_went_right', '')}",
        f"- **พลาดตรงไหน:** {plan['review'].get('what_went_wrong', '')}",
        f"- **บทเรียน:** {plan['review'].get('lesson', '')}",
        "",
        f"## Satellite ({sleeve:.0%} ของพอร์ต) — turnover {targets.get('turnover', 0):.0%}",
        "",
        "| ตัว | น้ำหนักใน satellite | ของพอร์ต | เดิม | เหตุผล | ถอยเมื่อ | หลักฐาน |",
        "|---|---:|---:|---:|---|---|---|",
    ]
    detail = {str(p.get("ticker", "")).upper(): p for p in plan.get("positions") or []}
    for ticker, weight in targets["satellite"].items():
        p = detail.get(ticker, {})
        lines.append(
            f"| **{ticker}** | {weight:.0%} | {weight * sleeve:.1%} | {prev_sat.get(ticker, 0):.0%} | "
            f"{p.get('rationale', '')} | {p.get('exit_if', '')} | {evidence_links(p.get('evidence') or [])} |"
        )
    if targets.get("cash"):
        lines.append(f"| เงินสด | {targets['cash']:.0%} | {targets['cash'] * sleeve:.1%} | "
                     f"{previous.get('cash', 0):.0%} | | | |")
    dropped = [t for t in prev_sat if t not in targets["satellite"]]
    if dropped:
        lines.append("")
        lines.append("**ขายออกทั้งหมด:** " + ", ".join(dropped))

    changes = plan.get("changes") or []
    if changes:
        lines += ["", "### เหตุผลของการเปลี่ยน", ""]
        for change in changes:
            lines.append(f"- **{change.get('ticker')}** {float(change.get('from_weight', 0)):.0%} → "
                         f"{float(change.get('to_weight', 0)):.0%}: {change.get('reason', '')}")

    full = mandate.full_weights(targets["satellite"], targets["cash"])
    lines += ["", "### น้ำหนักทั้งพอร์ต (รวม core)", "",
              " · ".join(f"{t} {w:.1%}" for t, w in sorted(full.items(), key=lambda kv: -kv[1])), ""]

    lines += ["## จุดที่จะทบทวน (trigger)", ""]
    for trigger in targets.get("triggers") or []:
        lines.append(f"- **{trigger['ticker']}** {trigger['kind'].replace('_', ' ')} {trigger['level']:g} → "
                     f"`{trigger['action']}` — {trigger.get('why', '')}")
    if not targets.get("triggers"):
        lines.append("*ไม่มี*")

    lines += ["", "## คำทำนาย (ระบบตัดสินผลเองด้วยราคา)", "",
              "| # | คำทำนาย | เงื่อนไข | ครบกำหนด | มั่นใจ |", "|---|---|---|---|---:|"]
    for i, prediction in enumerate(targets.get("predictions") or [], start=1):
        rule = (f"{prediction['ticker']} {prediction['kind']} {prediction['level']:g}"
                if prediction.get("level") is not None
                else f"{prediction['ticker']} {prediction['kind']} {prediction.get('benchmark')}")
        lines.append(f"| {i} | {prediction['statement']} | `{rule}` | {prediction['resolve_by']} | {prediction['confidence']}% |")

    lines += ["", "## ความเสี่ยง", "", *[f"- {r}" for r in plan.get("risks") or ["—"]],
              "", "## ต้องจับตาสัปดาห์นี้", "", *[f"- {w}" for w in plan.get("watch_next_week") or ["—"]],
              "", "## โน้ตถึงตัวเองสัปดาห์หน้า", "", plan.get("notes_for_next_week", "") or "—", ""]

    if status:
        lines += ["## ผลงานล่าสุด (ณ ตอนออกแผน)", ""]
        lines += status_lines(status)
        lines.append("")

    session = record.get("session", {})
    cost = session.get("cost_usd")
    lines += [
        "---",
        "",
        f"*โมเดล {', '.join(record.get('model_used') or ['?'])} · effort {record.get('effort')} · "
        f"{session.get('duration_s', '?')} วินาที · "
        f"{'เทียบเท่า $' + format(cost, '.2f') if isinstance(cost, (int, float)) else ''} · "
        f"รอบที่ส่ง {session.get('attempts', 1)} · พรอมต์ที่ใช้จริง `{record.get('request_file')}`*",
        "",
        "🔗 [[00 - AI Investing MOC]] · [[11 - Weekly Portfolio Manager]]",
        "",
    ]

    storage.ensure_dirs()
    out = config.JOURNAL_PLANS / f"{record['plan_id']}.md"
    out.write_text("\n".join(lines), encoding="utf-8")
    return out


def status_lines(status: dict[str, Any]) -> list[str]:
    """ตารางผลงานพอร์ต PM เทียบ control และ baseline — ใช้ทั้งในหน้าแผนและ brief"""
    if not status.get("started"):
        pending = status.get("pending_plan")
        if pending:
            return [f"*ยังไม่เริ่ม — แผน {pending} รอมีผลที่ราคาปิดวันทำการถัดไป*"]
        return ["*ยังไม่มีแผนที่มีผล*"]
    rows = [
        f"เริ่ม {status['start']} · {status['days']} วันทำการ · {status['plans_effective']} แผนที่มีผลแล้ว",
        "",
        "| เส้น | ผลตอบแทนตั้งแต่เริ่ม | max drawdown |",
        "|---|---:|---:|",
    ]
    for name, block in status["lines"].items():
        rows.append(f"| `{name}` | {block['return_pct']:+.2f}% | {block['max_drawdown_pct']:+.2f}% |")
    rows += [
        "",
        f"**ส่วนต่างที่มาจาก PM (llm_pm − pm_control):** {status['excess_vs_control_pct']:+.2f} จุด · "
        f"ต้นทุนซื้อขายสะสม {status['costs_pct']:.3f}% ของทุน",
    ]
    return rows
