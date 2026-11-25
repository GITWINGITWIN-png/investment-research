"""Thai idea portfolio + control group: the judge of whether "Claude reading Thai news picks better than random" (note 13).

The same rules as the US idea portfolio (finbg/scan/book.py) except for 3 things in which the Thai market really differs:

| | US | Thai |
|---|---|---|
| Cost | 30 bps for every stock | **SET tick at the entry price + commission**, per stock (about 0.5 to 1.5%) |
| Benchmark | IWM / SPY | **all tradable Thai stocks on the entry date, equally weighted**; Yahoo has no Thai index history |
| First counted day | the first day closing after the decision time (16:00 ET) | the first day closing after the decision time (17:00 Thai time) |

The benchmark does not affect the main question: Claude's ideas and the random group of the same round face the same benchmark,
so the difference "Claude - random" does not depend on the benchmark chosen.

All files are append-only in data/opportunities_th/, separate from the US portfolio.
The statistics (Welch, Brier, triage IC) use the same code as the US side, so both markets are measured exactly alike.
"""

from __future__ import annotations

import math
import random
import statistics
from datetime import datetime, time, timedelta
from typing import Any

import pandas as pd

from .. import config, storage
from ..scan import book as us_book
from ..scan.book import _append, _read, _stats
from . import prices

CANDIDATES = config.TH_OPPORTUNITIES / "candidates.jsonl"
IDEAS = config.TH_OPPORTUNITIES / "ideas.jsonl"
CONTROL = config.TH_OPPORTUNITIES / "control.jsonl"
RESOLUTIONS = config.TH_OPPORTUNITIES / "resolutions.jsonl"
VOIDED = config.TH_OPPORTUNITIES / "voided.jsonl"
BENCHMARK = "EW_TRADABLE"                  # Every tradable stock on the entry date, equally weighted


def voided_runs() -> set[str]:
    return {r["created_at"] for r in _read(VOIDED)}


def void(created_at: str, reason: str) -> None:
    """A round can be voided only when bad input data is found *before* the entry date, with a written reason (the same rule as the US side)"""
    _append(VOIDED, [{"created_at": created_at, "reason": reason, "voided_at": storage.utc_stamp()}])


def record(session: str, created_at: str, candidates: list[dict[str, Any]],
           triage: dict[str, dict[str, Any]], ideas: list[dict[str, Any]]) -> dict[str, Any]:
    run = created_at[:16].replace(":", "")
    _append(CANDIDATES, [{
        "id": f"{session}@{run}-cand-{c['ticker']}", "session": session, "created_at": created_at,
        "ticker": c["ticker"], "score": c["score"], "parts": c["parts"], "reasons": c["reasons"],
        "market_cap": c.get("market_cap"), "traded_value": c.get("traded_value"), "attention_5d": c.get("attention_5d"),
        "news_urls": [n["url"] for n in c["news"]], "insider_buys": len(c["insider"]),
        "benchmark": BENCHMARK, "triage": triage.get(c["ticker"]),
    } for c in candidates])

    booked = [{"id": f"{session}@{run}-{i['ticker']}", "session": session, "created_at": created_at,
               "benchmark": BENCHMARK, **i} for i in ideas]
    _append(IDEAS, booked)

    in_book = [i for i in booked if i["decision"] == "add_to_book"]
    rng = random.Random(f"th-control-{session}")            # Seed from the date, so it is reproducible and provably not hand-picked
    pool = [c["ticker"] for c in candidates]
    controls = rng.sample(pool, k=min(len(in_book), len(pool)))
    _append(CONTROL, [{"id": f"{session}@{run}-ctl-{n}", "session": session, "created_at": created_at,
                       "ticker": t, "benchmark": BENCHMARK} for n, t in enumerate(controls)])
    return {"booked": [i["ticker"] for i in in_book], "control": controls}


def _entries() -> list[dict[str, Any]]:
    voided = voided_runs()
    entries = [{**r, "kind": "idea"} for r in _read(IDEAS) if r.get("decision") == "add_to_book"]
    entries += [{**r, "kind": "control"} for r in _read(CONTROL)]
    entries += [{**r, "kind": "candidate"} for r in _read(CANDIDATES)]
    return [e for e in entries if e["created_at"] not in voided]


def effective_session(created_at: datetime, sessions: pd.DatetimeIndex) -> pd.Timestamp | None:
    """First trading day on which SET closes (17:00 Thai time) after the decision time; None if that day has not come yet"""
    for day in sessions:
        if datetime.combine(day.date(), time(config.TH_BAR_FINAL_HOUR), tzinfo=config.LOCAL_TZ) > created_at:
            return day
    return None


def _adjusted_closes(tickers: list[str], start: pd.Timestamp, end: pd.Timestamp) -> pd.DataFrame:
    """The latest fresh adjusted prices (dividends and splits) from Yahoo; the same set is used for both entry and exit"""
    from ..datasource import yf
    frames = []
    for i in range(0, len(tickers), config.TH_CHUNK):
        batch = tickers[i:i + config.TH_CHUNK]
        raw = yf.download(batch, start=(start - timedelta(days=7)).date(), end=(end + timedelta(days=3)).date(),
                          auto_adjust=True, progress=False, threads=True)
        if raw.empty:
            continue
        close = raw["Close"] if isinstance(raw.columns, pd.MultiIndex) else raw[["Close"]].set_axis(batch[:1], axis=1)
        frames.append(close)
    if not frames:
        return pd.DataFrame()
    out = pd.concat(frames, axis=1)
    out.index = pd.to_datetime(out.index).tz_localize(None).normalize()
    return out


def resolve() -> dict[str, Any]:
    """Compute the outcome of every entry that has reached 20 trading days"""
    done = {r["id"] for r in _read(RESOLUTIONS)}
    table = prices.read_all()
    if table.empty:
        return {"ok": True, "resolved": 0}
    stocks = table[~table["ticker"].str.startswith("^")]
    close = stocks.pivot(index="date", columns="ticker", values="close").sort_index()
    volume = stocks.pivot(index="date", columns="ticker", values="volume").sort_index()
    sessions = close.index[close[config.TH_CALENDAR_TICKER].notna()]

    due = []
    for entry in _entries():
        if entry["id"] in done:
            continue
        start = effective_session(datetime.fromisoformat(entry["created_at"]), sessions)
        if start is None:
            continue
        position = sessions.get_loc(start)
        if position + config.TH_IDEA_HOLD_SESSIONS >= len(sessions):
            continue
        due.append((entry, start, sessions[position + config.TH_IDEA_HOLD_SESSIONS]))
    if not due:
        return {"ok": True, "resolved": 0}

    # Benchmark for each entry date: the stocks really tradable on that day (using data up to that day only)
    value = (close * volume).rolling(config.TH_SCAN_TRADABLE_DAYS, min_periods=10).median()
    tradable = {start: list(value.columns[(value.loc[start] >= config.TH_TRADABLE_MIN_VALUE)
                                          & (close.loc[start] >= config.TH_TRADABLE_MIN_PRICE)])
                for start in {s for _, s, _ in due}}
    tickers = sorted({e["ticker"] for e, _, _ in due} | {t for names in tradable.values() for t in names})
    adj = _adjusted_closes(tickers, min(s for _, s, _ in due), max(x for _, _, x in due))

    def price(ticker: str, day: pd.Timestamp) -> float | None:
        if ticker not in adj.columns or day not in adj.index or pd.isna(adj.at[day, ticker]):
            return None
        return float(adj.at[day, ticker])

    bench: dict[tuple[pd.Timestamp, pd.Timestamp], tuple[float | None, int]] = {}
    out = []
    for entry, start, end in due:
        if (start, end) not in bench:
            moves = [price(t, end) / price(t, start) - 1 for t in tradable[start]
                     if price(t, start) and price(t, end) is not None]
            bench[(start, end)] = (statistics.fmean(moves) if moves else None, len(moves))
        b, n_bench = bench[(start, end)]
        p0, p1 = price(entry["ticker"], start), price(entry["ticker"], end)
        raw_entry = close.at[start, entry["ticker"]] if entry["ticker"] in close.columns else float("nan")
        base = {"id": entry["id"], "kind": entry["kind"], "ticker": entry["ticker"], "session": entry["session"],
                "entry": str(start.date()), "exit": str(end.date()), "resolved_at": storage.utc_stamp()}
        if None in (p0, p1, b) or pd.isna(raw_entry):
            out.append({**base, "outcome": "data_missing"})
            continue
        cost = _tick_cost(float(raw_entry))
        net = p1 / p0 - 1 - cost
        out.append({**base, "outcome": "ok", "return": round(net, 5), "cost": round(cost, 5),
                    "benchmark_return": round(b, 5), "benchmark_names": n_bench, "excess": round(net - b, 5)})
    _append(RESOLUTIONS, out)
    return {"ok": True, "resolved": len(out)}


def _tick_cost(entry_price: float) -> float:
    """Round-trip cost = 1 tick at the entry price + commission (the same formula as finlab.costs.tick_round_trip)"""
    lower = 0.0
    for upper, tick in config.TH_TICKS:
        if lower <= entry_price < upper:
            return tick / entry_price + config.TH_COMMISSION_ROUND_TRIP
        lower = upper
    return config.TH_COMMISSION_ROUND_TRIP


def scoreboard() -> dict[str, Any]:
    voided = voided_runs()
    resolved = {r["id"]: r for r in _read(RESOLUTIONS)}
    ideas = {r["id"]: r for r in _read(IDEAS) if r["created_at"] not in voided}
    valid = {e["id"] for e in _entries()}
    by_kind: dict[str, list[float]] = {"idea": [], "control": [], "candidate": []}
    by_type: dict[str, list[float]] = {}
    calibration: list[tuple[float, float]] = []
    missing = 0
    for r in resolved.values():
        if r["id"] not in valid:
            continue
        if r.get("outcome") != "ok":
            missing += 1
            continue
        by_kind[r["kind"]].append(r["excess"])
        if r["kind"] == "idea" and r["id"] in ideas:
            idea = ideas[r["id"]]
            by_type.setdefault(idea["idea_type"], []).append(r["excess"])
            calibration.append((idea["confidence"] / 100, 1.0 if r["excess"] > 0 else 0.0))

    a, b = by_kind["idea"], by_kind["control"]
    welch = None
    if len(a) > 1 and len(b) > 1:
        diff = statistics.fmean(a) - statistics.fmean(b)
        se = math.sqrt(statistics.variance(a) / len(a) + statistics.variance(b) / len(b))
        welch = {"diff_pct": round(diff * 100, 2), "t": round(diff / se, 2) if se > 0 else None}
    open_ideas = len([i for i in ideas.values() if i.get("decision") == "add_to_book" and i["id"] not in resolved])
    return {
        "ideas": _stats(a), "control": _stats(b), "candidates": _stats(by_kind["candidate"]),
        "ideas_minus_control": welch, "by_type": {k: _stats(v) for k, v in sorted(by_type.items())},
        "brier": round(sum((p - h) ** 2 for p, h in calibration) / len(calibration), 4) if calibration else None,
        "data_missing": missing, "open_ideas": open_ideas,
        "gate": f"ตัดสินเมื่อมีไอเดียครบ {config.TH_IDEA_GATE_MIN_RESOLVED} ตัว (ตอนนี้ {len(a)})",
        "triage_ic": us_book.triage_ic(CANDIDATES, RESOLUTIONS, voided),
    }
