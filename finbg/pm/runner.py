"""วงจรออกแผน: ถึงเวลาไหม → ประกอบข้อมูล → เรียกโมเดล → ตรวจ → (แก้ 1 รอบ) → เก็บ → แจ้ง

## ถึงเวลาออกแผนรายสัปดาห์เมื่อไหร่

แผนผูกกับ "สัปดาห์ของวันทำการถัดไป" (ISO week ตามเวลานิวยอร์ก):
- เช้าวันเสาร์เวลาไทย = เย็นวันศุกร์ที่นิวยอร์ก หลังตลาดปิด → วันทำการถัดไปคือจันทร์ → แผนของสัปดาห์หน้า
- ถ้ายังไม่มีแผนของสัปดาห์นั้น และเลยเวลาเริ่มช่วงออกแผน (เสาร์ 05:00 เวลาไทย) → ถึงเวลา
- เครื่องปิดทั้งสุดสัปดาห์ → ออกตอนเปิดเครื่องครั้งแรก ถ้าเลยวันจันทร์ไปแล้ว ถือเป็น "แผนสาย" (late)
  ยังออกได้ เพราะไม่มีแผนเลยแย่กว่าแผนสาย — และแผนมีผลไปข้างหน้าเท่านั้นอยู่แล้ว

## ทำไมแก้ได้แค่ 1 รอบ

ข้อผิดพลาดส่วนใหญ่คือเลขไม่ลงตัว (ผลรวมไม่เท่ากับ 1, ลืมใส่ changes) ซึ่งแก้ได้ในรอบเดียว
ถ้ารอบสองยังพัง แปลว่ามีอะไรผิดที่ต้องให้คนดู — ไม่ควรเผาโควตาวนต่อ
"""

from __future__ import annotations

from datetime import date, datetime, time
from typing import Any

from .. import config, market, notify, storage
from .. import headless
from . import context, plans as plans_module, portfolio, schema


def target_week(now: datetime | None = None) -> str:
    return market.iso_week(market.next_session_date(now))


def window_start(week: str) -> datetime:
    """เวลาเริ่มช่วงออกแผนของสัปดาห์นั้น (เวลาไทย): เสาร์ก่อนวันจันทร์ของสัปดาห์ เวลา 05:00"""
    year, number = week.split("-W")
    monday = date.fromisocalendar(int(year), int(number), 1)
    saturday = date.fromordinal(monday.toordinal() - (7 - config.PLAN_WINDOW_WEEKDAY))
    return datetime.combine(saturday, time(config.PLAN_WINDOW_HOUR), tzinfo=config.LOCAL_TZ)


def due(now: datetime | None = None) -> dict[str, Any]:
    now = now or storage.utc_now()
    week = target_week(now)
    weekly = [r for r in plans_module.for_week(week) if r.get("kind") == "weekly"]
    if weekly:
        return {"due": False, "week": week, "reason": f"มีแผนรายสัปดาห์ของ {week} แล้ว"}
    start = window_start(week)
    if now < start:
        return {"due": False, "week": week, "reason": f"ยังไม่ถึงช่วงออกแผน (เริ่ม {start:%Y-%m-%d %H:%M} เวลาไทย)"}
    year, number = week.split("-W")
    monday = date.fromisocalendar(int(year), int(number), 1)
    late = market.to_et(now).date() > monday
    return {"due": True, "week": week, "late": late,
            "reason": "แผนสาย (เลยวันจันทร์แล้ว)" if late else "ถึงเวลาออกแผนรายสัปดาห์"}


def _replans_recent(now: datetime) -> list[dict[str, Any]]:
    return [r for r in plans_module.load_all() if r.get("kind") == "replan"
            and (now - datetime.fromisoformat(r["created_at"])).total_seconds() < 7 * 86400]


def replan_allowed(now: datetime | None = None) -> tuple[bool, str]:
    """เพดานกันวนลูป: ไม่เกิน N ครั้ง/7 วัน และห่างกันอย่างน้อย M ชั่วโมง (นับแผนทุกชนิด)"""
    now = now or storage.utc_now()
    recent = _replans_recent(now)
    if len(recent) >= config.REPLAN_MAX_PER_WEEK:
        return False, f"วางแผนใหม่ไปแล้ว {len(recent)} ครั้งใน 7 วัน (เพดาน {config.REPLAN_MAX_PER_WEEK})"
    last = plans_module.latest()
    if last:
        hours = (now - datetime.fromisoformat(last["created_at"])).total_seconds() / 3600
        if hours < config.REPLAN_MIN_HOURS_APART:
            return False, f"แผนล่าสุดเพิ่งออกเมื่อ {hours:.1f} ชม.ก่อน (ต้องห่าง ≥ {config.REPLAN_MIN_HOURS_APART} ชม.)"
    return True, "ok"


def _feedback(errors: list[str]) -> str:
    return "\n".join([
        "",
        "## ⚠️ คำตอบรอบก่อนไม่ผ่านการตรวจ — แก้ตามนี้แล้วตอบใหม่ทั้งแผน",
        "",
        *[f"- {e}" for e in errors],
        "",
        "คงมุมมองเดิมได้ ถ้ายังเห็นแบบเดิม — แก้เฉพาะส่วนที่ผิดกติกา",
    ])


def run(kind: str = "weekly", *, force: bool = False, dry_run: bool = False,
        trigger: list[str] | None = None, now: datetime | None = None) -> dict[str, Any]:
    """ออกแผน 1 ฉบับ — คืนสรุปผล (ok, plan_id, path, …)"""
    storage.ensure_dirs()
    now = now or storage.utc_now()

    if kind == "weekly":
        state = due(now)
        if not state["due"] and not force:
            return {"ok": True, "skipped": True, "reason": state["reason"]}
        week = state["week"]
        plan_id = week
        if plans_module.path_for(plan_id).exists():
            return {"ok": False, "reason": f"มีแผน {plan_id} อยู่แล้ว (แผนแก้ไม่ได้ — ใช้ replan แทน)"}
        late = state.get("late", False)
    else:
        allowed, why = replan_allowed(now)
        if not allowed and not force:
            return {"ok": True, "skipped": True, "reason": why}
        week = target_week(now)
        plan_id = plans_module.next_replan_id(week)
        late = False

    request = context.build_request(kind, plan_id, now=now, trigger=trigger)
    meta = request["meta"]
    request_path = config.PLAN_REQUESTS / f"{plan_id}.md"
    request_path.write_text(
        f"<!-- system prompt -->\n{request['system']}\n\n<!-- user prompt -->\n{request['user']}\n",
        encoding="utf-8",
    )
    if dry_run:
        return {"ok": True, "dry_run": True, "plan_id": plan_id,
                "request": str(request_path.relative_to(config.ROOT)),
                "chars": len(request["user"]), "news_refs": len(meta["references"])}

    previous = (meta["previous"]["satellite"], meta["previous"]["cash"])
    prompt = request["user"]
    attempts = 0
    errors: list[str] = []
    result: dict[str, Any] = {}
    sessions: list[dict[str, Any]] = []
    normalized: dict[str, Any] = {}
    warnings: list[str] = []

    while attempts < config.PLAN_MAX_ATTEMPTS:
        attempts += 1
        result = headless.run_structured(
            job="plan", system_prompt=request["system"], user_prompt=prompt, schema=schema.PLAN_SCHEMA,
            model=config.PLAN_MODEL, effort=config.PLAN_EFFORT,
            timeout_sec=config.PLAN_TIMEOUT_SEC, max_budget_usd=config.PLAN_MAX_BUDGET_USD,
        )
        sessions.append({k: result.get(k) for k in ("audit", "cost_usd", "duration_s", "model_used", "error")})
        if not result.get("ok"):
            errors = [f"เรียกโมเดลไม่สำเร็จ: {result.get('error')}"]
            break
        errors, warnings, normalized = schema.validate(
            result["output"], previous=previous, closes=meta["closes"],
            today=date.fromisoformat(meta["today"]), news_ids=set(meta["references"]),
        )
        if not errors:
            break
        prompt = request["user"] + _feedback(errors)

    if errors:
        failure = {"plan_id": plan_id, "kind": kind, "at": storage.utc_stamp(), "errors": errors,
                   "sessions": sessions, "output": result.get("output")}
        failed_dir = config.PLANS / "failed"
        failed_dir.mkdir(parents=True, exist_ok=True)
        path = failed_dir / f"{plan_id}-{storage.utc_now():%Y%m%dT%H%M%S}.json"
        path.write_text(__import__("json").dumps(failure, ensure_ascii=False, indent=2), encoding="utf-8")
        notify.send(f"❌ แผน {plan_id} ไม่ผ่าน", "; ".join(errors)[:300] + " — พอร์ตคงน้ำหนักเดิม", "high")
        return {"ok": False, "plan_id": plan_id, "reason": "; ".join(errors[:3]),
                "failure": str(path.relative_to(config.ROOT))}

    output = result["output"]
    if not result.get("model_ok"):
        warnings.append(f"โมเดลที่รันจริง {result.get('model_used')} ไม่ตรงกับที่ล็อกไว้ {config.PLAN_MODEL}")
    if late:
        warnings.append("แผนสาย — ออกหลังวันจันทร์ของสัปดาห์ (เครื่องปิดช่วงออกแผน)")

    cited = {e.strip() for p in output.get("positions") or [] for e in p.get("evidence") or []}
    record = {
        "schema_version": schema.SCHEMA_VERSION,
        "plan_id": plan_id,
        "kind": kind,
        "week": week,
        "late": late,
        "created_at": storage.utc_stamp(),
        "information": {"prices_as_of": meta["prices_as_of"], "today": meta["today"]},
        "model_used": result.get("model_used"),
        "model_expected": config.PLAN_MODEL,
        "effort": config.PLAN_EFFORT,
        "request_file": str(request_path.relative_to(config.ROOT)),
        "trigger": trigger,
        "previous_plan_id": meta["previous"]["plan_id"],
        "previous_targets": {"satellite": meta["previous"]["satellite"], "cash": meta["previous"]["cash"]},
        "reference_closes": meta["closes"],
        "plan": output,
        "targets": normalized,
        "warnings": warnings,
        "references": {k: v for k, v in meta["references"].items() if k in cited},
        "session": {
            "attempts": attempts,
            "cost_usd": round(sum(s.get("cost_usd") or 0 for s in sessions), 4),
            "duration_s": round(sum(s.get("duration_s") or 0 for s in sessions), 1),
            "audits": [s.get("audit") for s in sessions],
        },
    }
    path = plans_module.save(record)
    status = portfolio.build()
    page = plans_module.render(record, status)

    satellite = ", ".join(f"{t} {w:.0%}" for t, w in normalized["satellite"].items())
    cash = f", cash {normalized['cash']:.0%}" if normalized["cash"] else ""
    stance = output.get("regime", {}).get("stance")
    notify.send(
        f"🧭 แผน {plan_id} ออกแล้ว ({stance})",
        f"satellite: {satellite}{cash} · turnover {normalized['turnover']:.0%}\n{output.get('thesis', '')[:220]}",
        "info" if kind == "weekly" else "high",
    )
    return {
        "ok": True,
        "plan_id": plan_id,
        "path": str(path.relative_to(config.ROOT)),
        "page": str(page.relative_to(config.ROOT)),
        "satellite": normalized["satellite"],
        "cash": normalized["cash"],
        "turnover": normalized["turnover"],
        "stance": stance,
        "attempts": attempts,
        "warnings": warnings,
        "cost_usd": record["session"]["cost_usd"],
    }
