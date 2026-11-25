"""Idea portfolio + control group: the judge of whether Claude's deep dives really find overlooked opportunities.

## What is recorded (all append-only, never edited afterwards)

| File | What | Question answered |
|---|---|---|
| candidates.jsonl | the 30 finalists every day (stage 1 score + Sonnet's result) | does the code filter help |
| ideas.jsonl | every deep-dive result (add_to_book / watch / pass) | - |
| control.jsonl | a random draw from the same 30, as many as add_to_book | **does Claude pick better than random** |
| resolutions.jsonl | the real outcome after 20 trading days | - |

## Rules (set before seeing results; never changed midway, see note 12)

- Entry: the close of the first trading day that closes after the decision time; exit: 20 trading days later, at the close
- Cost 30 bps round trip; benchmark: IWM if market cap < $10B, otherwise SPY
- The control draw uses a seed from the date, so it is reproducible and provably not hand-picked
- Stocks whose data disappears (acquired or delisted) are recorded separately as data_missing, not silently dropped
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
# Rounds declared void (bad input data): they must be declared *before* the outcome is known, with a reason; the original records are not deleted
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
    run = created_at[:16].replace(":", "")            # Unique ids even when the same trading day is scanned again (after voiding a round with bad data)
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
    """Compute the outcome of every entry that has reached 20 trading days (fresh adjusted prices from yfinance)"""
    from ..datasource import yf
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


def _spearman(x: list[float], y: list[float]) -> float | None:
    """Rank correlation, using average ranks for ties (triage scores tie often)"""
    rx, ry = pd.Series(x, dtype=float).rank(), pd.Series(y, dtype=float).rank()
    if len(rx) < 3 or rx.std() == 0 or ry.std() == 0:
        return None
    return float(rx.corr(ry))


def _residual_ranks(y: list[float], x: list[float]) -> list[float]:
    """Ranks of y after removing what the ranks of x explain, i.e. what Sonnet knows that the code score does not"""
    ry, rx = pd.Series(y, dtype=float).rank(pct=True), pd.Series(x, dtype=float).rank(pct=True)
    beta = ry.cov(rx) / rx.var() if rx.var() > 0 else 0.0
    return list(ry - ry.mean() - beta * (rx - rx.mean()))


def _newey_west_t(values: list[float], lag: int) -> float | None:
    """t statistic of a mean when neighbouring values are correlated.

    Adjacent scan rounds hold stocks over windows that overlap by 19 days (and the same stock can be a finalist again); a plain t would count
    20 rounds that are almost one event as 20 independent samples and come out too high.
    Newey-West adds the covariance of rounds up to `lag` apart into the variance (with weights that shrink with distance).
    """
    n = len(values)
    if n < 3:
        return None
    mean = statistics.fmean(values)
    dev = [v - mean for v in values]
    lag = min(lag, n - 1)
    gamma = [sum(dev[i] * dev[i - k] for i in range(k, n)) / n for k in range(lag + 1)]
    variance = gamma[0] + 2 * sum((1 - k / (lag + 1)) * gamma[k] for k in range(1, lag + 1))
    return round(mean / math.sqrt(variance / n), 2) if variance > 0 else None


def triage_ic(candidates_path=None, resolutions_path=None, voided: set[str] | None = None) -> dict[str, Any]:
    """Secondary test (note 12, set 2026-11-08 before the first real outcome): does Sonnet's score rank the finalists in line with the real outcome?

    Score = information_value x overlooked, the same formula triage.pick uses to choose deep dives (not a new formula made after seeing results).
    Per scan round: the rank correlation between the score and the 20-day excess return of finalists with results, averaged across rounds.
    Comparisons: the code's stage 1 score, and "what Sonnet adds" after removing what the code score already explains.

    The defaults are the US scanner's files; the Thai track (finbg/th/book.py) passes its own paths, so both markets
    are measured with exactly the same statistics.
    """
    voided = voided_runs() if voided is None else voided
    outcome = {r["id"]: r["excess"] for r in _read(resolutions_path or RESOLUTIONS)
               if r.get("kind") == "candidate" and r.get("outcome") == "ok"}
    runs: dict[tuple[str, str], list[tuple[float, float, float, bool]]] = {}
    for c in _read(candidates_path or CANDIDATES):
        t = c.get("triage")
        if c["created_at"] in voided or not t or c["id"] not in outcome:
            continue
        runs.setdefault((c["session"], c["created_at"]), []).append(
            (t["information_value"] * t["overlooked"], c["score"], outcome[c["id"]], bool(t.get("deep_dive"))))

    series: dict[str, list[float]] = {"sonnet": [], "code": [], "sonnet_beyond_code": []}
    dive, no_dive = [], []
    for key in sorted(runs):                                   # Sorted by time; Newey-West needs to know which rounds are adjacent
        rows = runs[key]
        sonnet, code, excess = [r[0] for r in rows], [r[1] for r in rows], [r[2] for r in rows]
        ic_sonnet = _spearman(sonnet, excess)
        if len(rows) < config.TRIAGE_IC_MIN_NAMES or ic_sonnet is None:
            continue                                           # Too few stocks, or Sonnet gave the whole round the same score
        series["sonnet"].append(ic_sonnet)
        for name, value in (("code", _spearman(code, excess)),
                            ("sonnet_beyond_code", _spearman(_residual_ranks(sonnet, code), excess))):
            if value is not None:
                series[name].append(value)
        dive += [r[2] for r in rows if r[3]]
        no_dive += [r[2] for r in rows if not r[3]]

    def summarize(values: list[float]) -> dict[str, Any]:
        return {"sessions": len(values), "mean_ic": round(statistics.fmean(values), 4) if values else None,
                "t_nw": _newey_west_t(values, config.TRIAGE_IC_NW_LAG)}

    main = summarize(series["sonnet"])
    n, ic, t = main["sessions"], main["mean_ic"], main["t_nw"]
    if n < config.TRIAGE_IC_GATE_SESSIONS:
        verdict = f"ยังไม่ตัดสิน — ตัดสินเมื่อครบ {config.TRIAGE_IC_GATE_SESSIONS} รอบ (ตอนนี้ {n})"
    elif ic is not None and ic > 0 and t is not None and t >= 2:
        verdict = "✅ คะแนนของ Sonnet เรียงผลได้ดีกว่าบังเอิญ"
    elif ic is not None and ic > 0 and n < config.TRIAGE_IC_EXTEND_SESSIONS:
        verdict = f"➖ เป็นบวกแต่ t < 2 — เก็บต่อถึง {config.TRIAGE_IC_EXTEND_SESSIONS} รอบแล้วตัดสินครั้งสุดท้าย (ตอนนี้ {n})"
    else:
        verdict = "❌ ไม่ดีกว่าบังเอิญ — การคัดกรองของ Sonnet ไม่ได้เพิ่มอะไรภายในผู้เข้ารอบ 30 ตัว"
    return {"sonnet": main, "code": summarize(series["code"]),
            "sonnet_beyond_code": summarize(series["sonnet_beyond_code"]),
            "deep_dive": _stats(dive), "not_deep_dive": _stats(no_dive), "verdict": verdict}


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
        "triage_ic": triage_ic(),
    }
