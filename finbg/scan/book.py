"""พอร์ตไอเดีย + กลุ่มควบคุม — ตัวตัดสินว่า "การดำลึกของ Claude หาโอกาสที่คนมองข้ามได้จริงไหม"

## บันทึกอะไรบ้าง (append-only ทั้งหมด — แก้ย้อนหลังไม่ได้)

| ไฟล์ | อะไร | ตอบคำถาม |
|---|---|---|
| candidates.jsonl | ผู้เข้ารอบ 30 ตัวทุกวัน (คะแนนชั้น 1 + ผล Sonnet) | ตัวกรองของโค้ดช่วยไหม |
| ideas.jsonl | ผลดำลึกทุกตัว (add_to_book / watch / pass) | — |
| control.jsonl | สุ่มจาก 30 ตัวเดียวกัน จำนวนเท่ากับ add_to_book | **Claude เลือกดีกว่าสุ่มไหม** |
| resolutions.jsonl | ผลจริงหลัง 20 วันทำการ | — |

## กติกา (ตั้งก่อนเห็นผล — ห้ามแก้ระหว่างทาง ดูโน้ต 12)

- เข้า: ราคาปิดของวันทำการแรกที่ตลาดปิดหลังเวลาตัดสินใจ · ออก: 20 วันทำการถัดไป ที่ราคาปิด
- ต้นทุน 30 bps ไป-กลับ · benchmark: IWM ถ้ามูลค่า < $10B ไม่งั้น SPY
- การสุ่มกลุ่มควบคุมใช้ seed จากวันที่ → ทำซ้ำได้ ตรวจสอบได้ว่าไม่ได้เลือก
- หุ้นที่ข้อมูลหาย (ถูกซื้อกิจการ/ถอดจากตลาด) บันทึกเป็น data_missing แยกให้เห็น ไม่ตัดทิ้งเงียบ ๆ
"""

from __future__ import annotations

import json
import math
import random
import statistics
from datetime import datetime, timedelta
from typing import Any

import pandas as pd

from .. import config, market, storage

CANDIDATES = config.OPPORTUNITIES / "candidates.jsonl"
IDEAS = config.OPPORTUNITIES / "ideas.jsonl"
CONTROL = config.OPPORTUNITIES / "control.jsonl"
RESOLUTIONS = config.OPPORTUNITIES / "resolutions.jsonl"
# รอบที่ประกาศยกเลิก (ข้อมูลเข้าเสีย) — ต้องประกาศ *ก่อน* รู้ผล พร้อมเหตุผล · บันทึกเดิมไม่ถูกลบ
VOIDED = config.OPPORTUNITIES / "voided.jsonl"


def voided_runs() -> set[str]:
    return {r["created_at"] for r in _read(VOIDED)}


def void(created_at: str, reason: str) -> None:
    _append(VOIDED, [{"created_at": created_at, "reason": reason, "voided_at": storage.utc_stamp()}])


def benchmark_for(market_cap: float | None) -> str:
    return config.IDEA_LARGE_CAP_BENCHMARK if (market_cap or 0) >= config.IDEA_LARGE_CAP else config.IDEA_SMALL_CAP_BENCHMARK


def _append(path, records: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        for record in records:
            fh.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")


def _read(path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def record(session: str, created_at: str, candidates: list[dict[str, Any]],
           triage: dict[str, dict[str, Any]], ideas: list[dict[str, Any]]) -> dict[str, Any]:
    caps = {c["ticker"]: c.get("market_cap") for c in candidates}
    run = created_at[:16].replace(":", "")            # id ไม่ซ้ำแม้สแกนวันทำการเดิมซ้ำ (หลังยกเลิกรอบที่ข้อมูลเสีย)
    _append(CANDIDATES, [{
        "id": f"{session}@{run}-cand-{c['ticker']}", "session": session, "created_at": created_at,
        "ticker": c["ticker"], "score": c["score"], "parts": c["parts"], "reasons": c["reasons"],
        "market_cap": c.get("market_cap"), "sector": c.get("sector"), "n_estimates": c.get("n_estimates"),
        "benchmark": benchmark_for(c.get("market_cap")), "triage": triage.get(c["ticker"]),
    } for c in candidates])

    booked = []
    for idea in ideas:
        ticker = str(idea["ticker"]).upper()
        entry = {"id": f"{session}@{run}-{ticker}", "session": session, "created_at": created_at,
                 "benchmark": benchmark_for(caps.get(ticker)), "market_cap": caps.get(ticker), **idea, "ticker": ticker}
        booked.append(entry)
    _append(IDEAS, booked)

    in_book = [i for i in booked if i["decision"] == "add_to_book"]
    rng = random.Random(f"control-{session}")
    pool = [c["ticker"] for c in candidates]
    controls = rng.sample(pool, k=min(len(in_book), len(pool)))
    _append(CONTROL, [{
        "id": f"{session}@{run}-ctl-{i}", "session": session, "created_at": created_at, "ticker": t,
        "benchmark": benchmark_for(caps.get(t)),
    } for i, t in enumerate(controls)])
    return {"booked": [i["ticker"] for i in in_book], "control": controls}


def _entries() -> list[dict[str, Any]]:
    voided = voided_runs()
    entries = [{**r, "kind": "idea"} for r in _read(IDEAS) if r.get("decision") == "add_to_book"]
    entries += [{**r, "kind": "control"} for r in _read(CONTROL)]
    entries += [{**r, "kind": "candidate"} for r in _read(CANDIDATES)]
    return [e for e in entries if e["created_at"] not in voided]


def resolve(now: datetime | None = None) -> dict[str, Any]:
    """คิดผลของทุกรายการที่ครบ 20 วันทำการแล้ว (ราคาปรับแล้วจาก yfinance ชุดใหม่)"""
    import yfinance as yf

    done = {r["id"] for r in _read(RESOLUTIONS)}
    sessions = market.sessions_from_prices(storage.price_matrix("adj_close"))
    due = []
    for entry in _entries():
        if entry["id"] in done:
            continue
        start = market.effective_session(datetime.fromisoformat(entry["created_at"]), sessions)
        if start is None:
            continue
        position = sessions.get_loc(start)
        if position + config.IDEA_HOLD_SESSIONS >= len(sessions):
            continue
        due.append((entry, start, sessions[position + config.IDEA_HOLD_SESSIONS]))
    if not due:
        return {"ok": True, "resolved": 0}

    tickers = sorted({e["ticker"] for e, _, _ in due} | {e["benchmark"] for e, _, _ in due})
    first = min(s for _, s, _ in due) - timedelta(days=7)
    last = max(x for _, _, x in due) + timedelta(days=3)
    raw = yf.download(tickers, start=first.date(), end=last.date(), auto_adjust=True, progress=False, threads=True)
    closes = raw["Close"] if isinstance(raw.columns, pd.MultiIndex) else raw[["Close"]].set_axis(tickers[:1], axis=1)
    closes.index = pd.to_datetime(closes.index).tz_localize(None).normalize()

    def price(ticker: str, day: pd.Timestamp) -> float | None:
        if ticker not in closes.columns or day not in closes.index:
            return None
        value = closes.at[day, ticker]
        return None if pd.isna(value) else float(value)

    out = []
    for entry, start, end in due:
        p0, p1 = price(entry["ticker"], start), price(entry["ticker"], end)
        b0, b1 = price(entry["benchmark"], start), price(entry["benchmark"], end)
        base = {"id": entry["id"], "kind": entry["kind"], "ticker": entry["ticker"], "session": entry["session"],
                "entry": str(start.date()), "exit": str(end.date()), "resolved_at": storage.utc_stamp()}
        if None in (p0, p1, b0, b1):
            out.append({**base, "outcome": "data_missing"})
            continue
        net = p1 / p0 - 1 - config.IDEA_COST_BPS / 10_000
        bench = b1 / b0 - 1
        out.append({**base, "outcome": "ok", "return": round(net, 5), "benchmark_return": round(bench, 5),
                    "excess": round(net - bench, 5)})
    _append(RESOLUTIONS, out)
    return {"ok": True, "resolved": len(out)}


def _stats(values: list[float]) -> dict[str, float | None]:
    if not values:
        return {"n": 0, "mean_pct": None, "hit_pct": None, "t": None}
    mean = statistics.fmean(values)
    sd = statistics.stdev(values) if len(values) > 1 else 0.0
    return {"n": len(values), "mean_pct": round(mean * 100, 2),
            "hit_pct": round(sum(v > 0 for v in values) / len(values) * 100, 1),
            "t": round(mean / (sd / math.sqrt(len(values))), 2) if sd > 0 else None}


def scoreboard() -> dict[str, Any]:
    voided = voided_runs()
    resolved = {r["id"]: r for r in _read(RESOLUTIONS)}
    ideas = {r["id"]: r for r in _read(IDEAS) if r["created_at"] not in voided}
    by_kind: dict[str, list[float]] = {"idea": [], "control": [], "candidate": []}
    missing = 0
    by_type: dict[str, list[float]] = {}
    calibration: list[tuple[float, float]] = []
    valid_ids = {e["id"] for e in _entries()}
    for r in resolved.values():
        if r["id"] not in valid_ids:
            continue
        if r.get("outcome") != "ok":
            missing += 1
            continue
        by_kind[r["kind"]].append(r["excess"])
        if r["kind"] == "idea" and r["id"] in ideas:
            idea = ideas[r["id"]]
            by_type.setdefault(idea["idea_type"], []).append(r["excess"])
            calibration.append((idea["confidence"] / 100, 1.0 if r["excess"] > 0 else 0.0))

    ideas_s, control_s = by_kind["idea"], by_kind["control"]
    welch = None
    if len(ideas_s) > 1 and len(control_s) > 1:
        diff = statistics.fmean(ideas_s) - statistics.fmean(control_s)
        se = math.sqrt(statistics.variance(ideas_s) / len(ideas_s) + statistics.variance(control_s) / len(control_s))
        welch = {"diff_pct": round(diff * 100, 2), "t": round(diff / se, 2) if se > 0 else None}
    open_ideas = len([i for i in ideas.values() if i.get("decision") == "add_to_book" and i["id"] not in resolved])
    return {
        "ideas": _stats(ideas_s), "control": _stats(control_s), "candidates": _stats(by_kind["candidate"]),
        "ideas_minus_control": welch, "by_type": {k: _stats(v) for k, v in sorted(by_type.items())},
        "brier": round(sum((p - h) ** 2 for p, h in calibration) / len(calibration), 4) if calibration else None,
        "data_missing": missing, "open_ideas": open_ideas,
        "gate": f"ตัดสินเมื่อมีไอเดียครบ {config.IDEA_GATE_MIN_RESOLVED} ตัว (ตอนนี้ {len(ideas_s)})",
    }
