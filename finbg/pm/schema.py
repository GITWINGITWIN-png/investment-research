"""รูปแบบของแผนรายสัปดาห์ — ตรวจสองชั้น

ชั้นที่ 1 `PLAN_SCHEMA` ส่งให้ CLI ผ่าน `--json-schema` → โมเดลถูกบังคับให้ตอบรูปแบบนี้
ชั้นที่ 2 `validate()` ตรวจกติกาที่ JSON schema แสดงไม่ได้ (เพดานต่อชนิดหุ้น, turnover,
ผลรวมน้ำหนัก, ทุกการเปลี่ยนต้องมีเหตุผล, คำทำนายต้องตัดสินได้)

ข้อผิดพลาดในชั้นที่ 2 ถูกส่งกลับให้โมเดลแก้ได้อีกครั้ง (ดู runner.py)
ถ้ายังไม่ผ่าน → แผนสัปดาห์นั้นถูกปฏิเสธ และพอร์ตคงน้ำหนักเดิม ("ไม่เปลี่ยน" คือค่าปลอดภัย)
"""

from __future__ import annotations

import re
from datetime import date, timedelta
from typing import Any

from .. import config
from . import mandate

SCHEMA_VERSION = 1

_STR = {"type": "string"}

PLAN_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "review", "regime", "thesis", "positions", "cash", "changes",
        "triggers", "predictions", "risks", "watch_next_week", "notes_for_next_week",
    ],
    "properties": {
        "review": {
            "type": "object",
            "additionalProperties": False,
            "required": ["what_happened", "what_went_right", "what_went_wrong", "lesson"],
            "properties": {k: _STR for k in ("what_happened", "what_went_right", "what_went_wrong", "lesson")},
        },
        "regime": {
            "type": "object",
            "additionalProperties": False,
            "required": ["stance", "confidence", "summary"],
            "properties": {
                "stance": {"type": "string", "enum": ["risk_on", "neutral", "risk_off"]},
                "confidence": {"type": "integer", "minimum": 0, "maximum": 100},
                "summary": _STR,
            },
        },
        "thesis": _STR,
        "positions": {
            "type": "array",
            "maxItems": config.PM_MAX_POSITIONS,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["ticker", "weight", "rationale", "evidence", "exit_if"],
                "properties": {
                    "ticker": _STR,
                    "weight": {"type": "number", "minimum": 0, "maximum": 1},
                    "rationale": _STR,
                    "evidence": {"type": "array", "items": _STR},
                    "exit_if": _STR,
                },
            },
        },
        "cash": {"type": "number", "minimum": 0, "maximum": 1},
        "changes": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["ticker", "from_weight", "to_weight", "reason"],
                "properties": {
                    "ticker": _STR,
                    "from_weight": {"type": "number"},
                    "to_weight": {"type": "number"},
                    "reason": _STR,
                },
            },
        },
        "triggers": {
            "type": "array",
            "maxItems": 10,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["ticker", "kind", "level", "action", "why"],
                "properties": {
                    "ticker": _STR,
                    "kind": {"type": "string", "enum": ["close_below", "close_above"]},
                    "level": {"type": "number"},
                    "action": {"type": "string", "enum": ["review", "reduce", "exit"]},
                    "why": _STR,
                },
            },
        },
        "predictions": {
            "type": "array",
            "minItems": config.PM_MIN_PREDICTIONS,
            "maxItems": config.PM_MAX_PREDICTIONS,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["statement", "kind", "ticker", "level", "benchmark", "resolve_by", "confidence"],
                "properties": {
                    "statement": _STR,
                    "kind": {"type": "string", "enum": [
                        "end_above", "end_below", "touch_above", "touch_below", "outperform", "underperform",
                    ]},
                    "ticker": _STR,
                    "level": {"type": ["number", "null"]},
                    "benchmark": {"type": ["string", "null"]},
                    "resolve_by": _STR,
                    "confidence": {"type": "integer", "minimum": 50, "maximum": 99},
                },
            },
        },
        "risks": {"type": "array", "items": _STR},
        "watch_next_week": {"type": "array", "items": _STR},
        "notes_for_next_week": _STR,
    },
}

PREDICTION_TICKERS_EXTRA = {"^VIX"}
LEVEL_KINDS = {"end_above", "end_below", "touch_above", "touch_below"}
RELATIVE_KINDS = {"outperform", "underperform"}
NEWS_ID = re.compile(r"\bN\d{1,4}\b")


def validate(
    output: dict[str, Any],
    *,
    previous: tuple[dict[str, float], float],
    closes: dict[str, float],
    today: date,
    news_ids: set[str] | None = None,
) -> tuple[list[str], list[str], dict[str, Any]]:
    """คืน (errors, warnings, normalized) — errors ว่างแปลว่าแผนใช้ได้

    `previous` = น้ำหนักเป้าหมายของแผนล่าสุด (satellite, cash) ใช้ตรวจ turnover/changes
    `closes`   = ราคาปิดล่าสุด ใช้ตรวจว่าระดับราคาใน trigger/คำทำนายสมเหตุสมผล
    """
    errors: list[str] = []
    warnings: list[str] = []
    prev_sat, prev_cash = previous

    # --- positions -----------------------------------------------------------
    weights: dict[str, float] = {}
    for i, position in enumerate(output.get("positions") or []):
        ticker = str(position.get("ticker", "")).strip().upper()
        weight = float(position.get("weight") or 0.0)
        if not mandate.in_universe(ticker):
            errors.append(f"positions[{i}]: {ticker!r} ไม่อยู่ใน universe ที่ถือได้")
            continue
        if ticker in weights:
            errors.append(f"positions: {ticker} ซ้ำ — รวมเป็นรายการเดียว")
            continue
        if weight <= 0:
            continue
        if weight > mandate.cap_for(ticker) + 1e-9:
            errors.append(
                f"{ticker} น้ำหนัก {weight:.2f} เกินเพดาน {mandate.cap_for(ticker):.2f} "
                f"ของชนิด {mandate.kind_of(ticker)}"
            )
        if not position.get("evidence"):
            warnings.append(f"{ticker}: ไม่มี evidence อ้างอิง")
        weights[ticker] = weight

    cash = float(output.get("cash") or 0.0)
    total = sum(weights.values()) + cash
    if abs(total - 1.0) > 0.01:
        errors.append(f"ผลรวมน้ำหนัก + cash = {total:.3f} ต้องเท่ากับ 1.0")
    elif total > 0:
        weights = {t: w / total for t, w in weights.items()}
        cash = cash / total

    # --- ปัดการเปลี่ยนแปลงเล็ก ๆ คืน (กัน churn ที่ไม่มีความหมาย) ------------------
    snapped: list[str] = []
    trial = dict(weights)
    for ticker in set(prev_sat) | set(weights):
        old, new = prev_sat.get(ticker, 0.0), weights.get(ticker, 0.0)
        if 0 < abs(new - old) < config.PM_MIN_CHANGE:
            if old > 0:
                trial[ticker] = old
            else:
                trial.pop(ticker, None)
            snapped.append(ticker)
    residual = 1.0 - sum(trial.values())
    if snapped and residual >= -1e-9:
        weights, cash = trial, max(residual, 0.0)
        warnings.append(f"ปัดการเปลี่ยนที่เล็กกว่า {config.PM_MIN_CHANGE:.0%} คืน: {', '.join(sorted(snapped))}")
    elif snapped:
        snapped = []

    new_full = {**weights, mandate.CASH: cash}
    old_full = {**prev_sat, mandate.CASH: prev_cash}
    turn = mandate.turnover(old_full, new_full)
    if turn > config.PM_MAX_TURNOVER + 1e-9:
        errors.append(
            f"turnover {turn:.2f} เกินเพดาน {config.PM_MAX_TURNOVER:.2f} — "
            "ทยอยเปลี่ยนได้ในแผนถัดไป"
        )
    if len(weights) > config.PM_MAX_POSITIONS:
        errors.append(f"ถือ {len(weights)} ตัว เกินเพดาน {config.PM_MAX_POSITIONS}")

    # --- changes: ทุกการเปลี่ยนที่มีนัยต้องมีเหตุผล --------------------------------
    declared = {str(c.get("ticker", "")).strip().upper(): c for c in output.get("changes") or []}
    for ticker in sorted(set(prev_sat) | set(weights)):
        old, new = prev_sat.get(ticker, 0.0), weights.get(ticker, 0.0)
        if abs(new - old) < config.PM_MIN_CHANGE:
            continue
        entry = declared.get(ticker)
        if entry is None:
            errors.append(f"{ticker} เปลี่ยน {old:.2f} → {new:.2f} แต่ไม่มีใน changes (ต้องให้เหตุผลทุกการเปลี่ยน)")
            continue
        if abs(float(entry.get("from_weight") or 0) - old) > 0.011:
            errors.append(f"changes {ticker}: from_weight ต้องเป็น {old:.2f} (น้ำหนักเป้าหมายปัจจุบัน)")
        if not str(entry.get("reason", "")).strip():
            errors.append(f"changes {ticker}: ไม่มี reason")
    for ticker in declared:
        if ticker not in prev_sat and ticker not in weights and ticker != mandate.CASH:
            warnings.append(f"changes มี {ticker} ซึ่งไม่ได้ถือทั้งก่อนและหลัง")

    # --- triggers ------------------------------------------------------------
    triggers: list[dict[str, Any]] = []
    for i, trigger in enumerate(output.get("triggers") or []):
        ticker = str(trigger.get("ticker", "")).strip().upper()
        level = trigger.get("level")
        last = closes.get(ticker)
        if not (mandate.in_universe(ticker) or ticker in PREDICTION_TICKERS_EXTRA):
            errors.append(f"triggers[{i}]: {ticker!r} ไม่รู้จัก")
            continue
        if not isinstance(level, (int, float)) or level <= 0:
            errors.append(f"triggers[{i}] {ticker}: level ต้องเป็นราคาบวก")
            continue
        if last and not 0.5 * last <= level <= 2.0 * last:
            errors.append(f"triggers[{i}] {ticker}: level {level} ห่างจากราคาล่าสุด {last:.2f} เกินไป")
            continue
        triggers.append({**trigger, "ticker": ticker, "level": float(level)})

    # --- predictions ---------------------------------------------------------
    predictions: list[dict[str, Any]] = []
    for i, prediction in enumerate(output.get("predictions") or []):
        label = f"predictions[{i}]"
        ticker = str(prediction.get("ticker", "")).strip().upper()
        kind = prediction.get("kind")
        if not (mandate.in_universe(ticker) or ticker in PREDICTION_TICKERS_EXTRA):
            errors.append(f"{label}: {ticker!r} ไม่รู้จัก")
            continue
        try:
            resolve_by = date.fromisoformat(str(prediction.get("resolve_by", ""))[:10])
        except ValueError:
            errors.append(f"{label}: resolve_by ต้องเป็น YYYY-MM-DD")
            continue
        days = (resolve_by - today).days
        if not config.PM_PREDICTION_MIN_DAYS <= days <= config.PM_PREDICTION_MAX_DAYS:
            errors.append(
                f"{label}: resolve_by ต้องห่างจากวันนี้ {config.PM_PREDICTION_MIN_DAYS}–"
                f"{config.PM_PREDICTION_MAX_DAYS} วัน (ได้ {days})"
            )
            continue
        level = prediction.get("level")
        benchmark = str(prediction.get("benchmark") or "").strip().upper() or None
        last = closes.get(ticker)
        if kind in LEVEL_KINDS:
            if not isinstance(level, (int, float)) or level <= 0:
                errors.append(f"{label} ({kind}): ต้องมี level เป็นราคาบวก")
                continue
            if kind == "touch_above" and last and level <= last:
                errors.append(f"{label}: touch_above {level} ต่ำกว่าราคาปัจจุบัน {last:.2f} = ถูกตั้งแต่วันแรก")
                continue
            if kind == "touch_below" and last and level >= last:
                errors.append(f"{label}: touch_below {level} สูงกว่าราคาปัจจุบัน {last:.2f} = ถูกตั้งแต่วันแรก")
                continue
            benchmark = None
        elif kind in RELATIVE_KINDS:
            if not benchmark or not (mandate.in_universe(benchmark) or benchmark in PREDICTION_TICKERS_EXTRA):
                errors.append(f"{label} ({kind}): ต้องมี benchmark ที่รู้จัก")
                continue
            if benchmark == ticker:
                errors.append(f"{label}: benchmark ต้องไม่ใช่ตัวเดียวกับ ticker")
                continue
            level = None
        predictions.append({
            "statement": str(prediction.get("statement", "")).strip(),
            "kind": kind,
            "ticker": ticker,
            "level": float(level) if level is not None else None,
            "benchmark": benchmark,
            "resolve_by": resolve_by.isoformat(),
            "confidence": int(prediction.get("confidence") or 50),
        })
    if len(predictions) < config.PM_MIN_PREDICTIONS and not any("predictions" in e for e in errors):
        errors.append(f"ต้องมีคำทำนายที่ตัดสินได้อย่างน้อย {config.PM_MIN_PREDICTIONS} ข้อ")

    # --- ข้อความ -------------------------------------------------------------
    if len(str(output.get("thesis", "")).strip()) < 60:
        errors.append("thesis สั้นเกินไป — ต้องอธิบายมุมมองหลักของสัปดาห์ให้เข้าใจได้")
    if news_ids is not None:
        cited = {m for p in output.get("positions") or [] for e in p.get("evidence") or []
                 for m in NEWS_ID.findall(str(e))}
        unknown = sorted(cited - news_ids)
        if unknown:
            warnings.append(f"evidence อ้างข่าวที่ไม่มีในข้อมูล: {', '.join(unknown[:8])}")

    normalized = {
        "satellite": {t: round(w, 4) for t, w in sorted(weights.items(), key=lambda kv: -kv[1])},
        "cash": round(cash, 4),
        "turnover": round(turn, 4),
        "snapped": sorted(snapped),
        "triggers": triggers,
        "predictions": predictions,
    }
    return errors, warnings, normalized


def deadline_examples(today: date) -> dict[str, str]:
    """ตัวอย่างวันที่สำหรับพรอมต์ — ให้โมเดลเห็นช่วงที่ยอมรับเป็นวันจริง"""
    return {
        "min": (today + timedelta(days=config.PM_PREDICTION_MIN_DAYS)).isoformat(),
        "max": (today + timedelta(days=config.PM_PREDICTION_MAX_DAYS)).isoformat(),
        "typical": (today + timedelta(days=14)).isoformat(),
    }
