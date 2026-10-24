"""ตัดสินคำทำนายด้วยราคาอัตโนมัติ + วัด calibration

## ทำไมต้องให้โค้ดตัดสิน

prediction journal เดิมตายเพราะต้องเปิดไฟล์มากรอกผลเอง — แรงเสียดทานฆ่านิสัย
คำทำนายในแผนจึงบังคับให้เป็นเงื่อนไขที่ตัดสินด้วยราคาได้ แล้วโค้ดตัดสินเองทุกวัน
ผลที่ได้ป้อนกลับเข้าแผนสัปดาห์ถัดไป (scorecard) — นี่คือวงจร "เรียนรู้จากความผิดพลาด"

## ชนิดคำทำนาย

| kind | ถูกเมื่อ | ตัดสินเมื่อ |
|---|---|---|
| touch_above / touch_below | ราคาปิดวันใดวันหนึ่งในช่วง ทะลุ level | ทันทีที่ทะลุ หรือครบกำหนด |
| end_above / end_below | ราคาปิดวันสุดท้ายก่อนครบกำหนด เหนือ/ใต้ level | ครบกำหนด |
| outperform / underperform | ผลตอบแทนรวมปันผลชนะ/แพ้ benchmark | ครบกำหนด |

ราคาอ้างอิง = ราคาปิดล่าสุดที่รู้ ณ เวลาที่ทำนาย — ไม่มีทางใช้ข้อมูลอนาคต
"""

from __future__ import annotations

import json
import re
from datetime import date, datetime, timedelta
from typing import Any

import pandas as pd

from .. import config, market, storage
from . import plans as plans_module

RESOLUTIONS = config.PLANS / "resolutions.jsonl"
BUCKETS = [(50, 60), (60, 70), (70, 80), (80, 90), (90, 100)]


def _resolved() -> dict[str, dict[str, Any]]:
    if not RESOLUTIONS.exists():
        return {}
    out = {}
    for line in RESOLUTIONS.read_text(encoding="utf-8").splitlines():
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        out[record["id"]] = record
    return out


def plan_predictions(records: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    out = []
    for record in records if records is not None else plans_module.load_all():
        for i, prediction in enumerate(record["targets"].get("predictions") or [], start=1):
            out.append({**prediction, "id": f"{record['plan_id']}-p{i}",
                        "plan_id": record["plan_id"], "created_at": record["created_at"]})
    return out


def _last_weekday_on_or_before(day: date) -> date:
    while day.weekday() >= 5:
        day -= timedelta(days=1)
    return day


def evaluate(prediction: dict[str, Any], close: pd.DataFrame, adj: pd.DataFrame,
             now: datetime | None = None) -> tuple[str | None, dict[str, Any]]:
    """คืน ("correct"/"wrong", รายละเอียด) หรือ (None, ...) ถ้ายังตัดสินไม่ได้"""
    sessions = market.sessions_from_prices(close)
    created = datetime.fromisoformat(prediction["created_at"])
    reference = market.last_close_before(created, sessions)
    resolve_by = date.fromisoformat(prediction["resolve_by"])
    ticker = prediction["ticker"]
    if reference is None or ticker not in close.columns:
        return None, {"note": "ไม่มีข้อมูลราคาอ้างอิง"}

    window = sessions[(sessions > reference) & (sessions.date <= resolve_by)]
    now_et = market.to_et(now or storage.utc_now()).date()
    complete = bool((sessions.date > resolve_by).any()) or (
        now_et > resolve_by and len(sessions) and sessions[-1].date() >= _last_weekday_on_or_before(resolve_by)
    )
    kind = prediction["kind"]
    level = prediction.get("level")
    series = close.loc[window, ticker].dropna()
    detail: dict[str, Any] = {"reference": str(reference.date()),
                              "reference_close": round(float(close.at[reference, ticker]), 4)}

    if kind in ("touch_above", "touch_below"):
        hits = series[series > level] if kind == "touch_above" else series[series < level]
        if not hits.empty:
            detail.update({"hit_on": str(hits.index[0].date()), "hit_close": round(float(hits.iloc[0]), 4)})
            return "correct", detail
        if complete:
            detail["extreme"] = round(float(series.max() if kind == "touch_above" else series.min()), 4) if len(series) else None
            return "wrong", detail
        return None, detail

    if not complete or series.empty:
        return None, detail
    end = series.index[-1]
    detail["end"] = str(end.date())
    if kind in ("end_above", "end_below"):
        final = float(series.iloc[-1])
        detail["end_close"] = round(final, 4)
        ok = final > level if kind == "end_above" else final < level
        return ("correct" if ok else "wrong"), detail

    benchmark = prediction.get("benchmark")
    if benchmark not in adj.columns:
        return None, {"note": f"ไม่มีราคา {benchmark}"}
    mine = float(adj.at[end, ticker] / adj.at[reference, ticker] - 1.0)
    theirs = float(adj.at[end, benchmark] / adj.at[reference, benchmark] - 1.0)
    detail.update({"return_pct": round(mine * 100, 2), "benchmark_return_pct": round(theirs * 100, 2)})
    ok = mine > theirs if kind == "outperform" else mine < theirs
    return ("correct" if ok else "wrong"), detail


def _price_tables() -> tuple[pd.DataFrame, pd.DataFrame]:
    close = storage.price_matrix("close")
    adj = storage.price_matrix("adj_close")
    sessions = market.sessions_from_prices(close)
    return close.loc[sessions].ffill(), adj.loc[sessions].ffill()


def resolve_all(now: datetime | None = None) -> dict[str, Any]:
    """ตัดสินทุกข้อที่ถึงเวลา — ของแผน (append ลง resolutions.jsonl) และของ journal เดิม"""
    close, adj = _price_tables()
    if close.empty:
        return {"ok": False, "reason": "ไม่มีราคา"}
    done = _resolved()
    new: list[dict[str, Any]] = []
    for prediction in plan_predictions():
        if prediction["id"] in done:
            continue
        outcome, detail = evaluate(prediction, close, adj, now)
        if outcome is None:
            continue
        new.append({
            "id": prediction["id"], "plan_id": prediction["plan_id"], "outcome": outcome,
            "confidence": prediction["confidence"], "kind": prediction["kind"],
            "ticker": prediction["ticker"], "statement": prediction["statement"],
            "resolved_at": storage.utc_stamp(), "detail": detail,
        })
    if new:
        RESOLUTIONS.parent.mkdir(parents=True, exist_ok=True)
        with RESOLUTIONS.open("a", encoding="utf-8") as fh:
            for record in new:
                fh.write(json.dumps(record, ensure_ascii=False) + "\n")

    journal = _resolve_journal(close, adj, now)
    open_count = len([p for p in plan_predictions() if p["id"] not in done and p["id"] not in {n["id"] for n in new}])
    return {"ok": True, "resolved": len(new), "journal_resolved": journal, "open": open_count}


def scorecard(limit: int = 10) -> dict[str, Any]:
    """ผลคำทำนายของ PM: อัตราถูก, Brier score, calibration ต่อช่วงความมั่นใจ"""
    records = list(_resolved().values())
    if not records:
        return {"n": 0}
    hits = [1.0 if r["outcome"] == "correct" else 0.0 for r in records]
    probs = [r["confidence"] / 100 for r in records]
    buckets = []
    for low, high in BUCKETS:
        inside = [(h, p) for h, p in zip(hits, probs) if low <= p * 100 < high]
        if inside:
            buckets.append({"range": f"{low}-{high - 1}%", "n": len(inside),
                            "hit_rate_pct": round(sum(h for h, _ in inside) / len(inside) * 100, 1),
                            "avg_confidence_pct": round(sum(p for _, p in inside) / len(inside) * 100, 1)})
    recent = sorted(records, key=lambda r: r["resolved_at"])[-limit:]
    return {
        "n": len(records),
        "hit_rate_pct": round(sum(hits) / len(hits) * 100, 1),
        "avg_confidence_pct": round(sum(probs) / len(probs) * 100, 1),
        # Brier: 0 = สมบูรณ์แบบ · 0.25 = เท่ากับเดา 50% ทุกข้อ
        "brier": round(sum((p - h) ** 2 for p, h in zip(probs, hits)) / len(hits), 4),
        "buckets": buckets,
        "recent": recent,
    }


# --- คำทำนายเก่าใน Journal/Predictions (เขียนมือ) ------------------------------
# ตัดสินอัตโนมัติได้ถ้า frontmatter มี resolution_kind / ticker / level (หรือ benchmark)

def _frontmatter(text: str) -> dict[str, str]:
    if not text.startswith("---"):
        return {}
    front = text.split("---", 2)[1]
    return {k.strip(): v.strip() for k, _, v in (line.partition(":") for line in front.splitlines()) if k.strip()}


def _resolve_journal(close: pd.DataFrame, adj: pd.DataFrame, now: datetime | None) -> int:
    resolved = 0
    for path in sorted(config.PREDICTIONS.glob("*.md")):
        text = path.read_text(encoding="utf-8")
        meta = _frontmatter(text)
        if meta.get("resolved", "null") not in ("null", "", "false") or not meta.get("resolution_kind"):
            continue
        level = meta.get("level")
        prediction = {
            "kind": meta["resolution_kind"],
            "ticker": meta.get("ticker", "").upper(),
            "level": float(level) if level not in (None, "", "null") else None,
            "benchmark": (meta.get("benchmark") or "").upper() or None,
            "resolve_by": meta["resolve_by"],
            "created_at": f"{meta['date']}T00:00:00+00:00",
        }
        outcome, detail = evaluate(prediction, close, adj, now)
        if outcome is None:
            continue
        verdict = "ถูก ✅" if outcome == "correct" else "ผิด ❌"
        evidence = detail.get("hit_on") or detail.get("end") or "ครบกำหนด"
        text = re.sub(r"^resolved:.*$", "resolved: true", text, count=1, flags=re.MULTILINE)
        text = re.sub(r"^outcome:.*$", f"outcome: {outcome}", text, count=1, flags=re.MULTILINE)
        result = (f"**{verdict}** — ตัดสินอัตโนมัติ {storage.utc_now().date()} "
                  f"(อ้างอิงราคาปิด {detail.get('reference')} = {detail.get('reference_close')}, "
                  f"ตัดสินจาก {evidence}) · รายละเอียด: `{json.dumps(detail, ensure_ascii=False)}`")
        text = text.replace("*(ยังไม่ครบกำหนด)*", result)
        path.write_text(text, encoding="utf-8")
        resolved += 1
    return resolved
