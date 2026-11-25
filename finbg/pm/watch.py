"""Watch for strong events: code decides first, and the LLM is woken only when needed.

Normal round: **once a week**, before the weekly plan (morning.sh calls `finbg watch --weekly`).
It used to run hourly; the owner found that too frequent (2026-10-24). The parts that really need speed (violent price moves
and plan triggers) moved to volatility.py, which runs daily after data collection,
so this file now summarises the week's 8-Ks, strong news and VIX for the plan to read.

## What is watched (all rules in code, all checkable)

| Source | Condition | Level |
|---|---|---|
| Holding prices | move today >= ALERT_SHOCK_Z times normal volatility and >= the minimum % | high (z >= 5 -> critical) |
| SPY | move today >= ALERT_MARKET_MOVE % | high (>= 4% -> critical) |
| VIX | level >= ALERT_VIX_LEVEL or a jump >= ALERT_VIX_JUMP % in one day | high (>= 40 -> critical) |
| Plan triggers | the price crosses a level the PM set itself | exit/reduce -> high, review -> medium |
| 8-Ks of holdings | high/critical items (see config.SEC_8K_ITEMS) | per item |
| Labelled news | materiality >= ALERT_LABEL_MATERIALITY and it affects a holding | high |

## Why code is the first gate

Having an LLM read the news every hour to spot what matters burns quota 24 times a day, and the LLM
is inconsistent (important today, not tomorrow). Written rules behave the same every time,
and the LLM is called only when real judgement is needed: whether the event changes the plan.
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timedelta
from typing import Any

import pandas as pd
import requests

from .. import config, labels as labels_module, notify, storage
from ..collect.sources import sec as sec_source
from . import mandate, plans as plans_module

STATE_PATH = config.ALERTS / "state.json"
ESCALATE = {"high", "critical"}


def _load_state() -> dict[str, Any]:
    try:
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"fired": {}, "sec_since": None, "labels_since": None}


def _save_state(state: dict[str, Any]) -> None:
    cutoff = (storage.utc_now() - timedelta(days=30)).isoformat()
    state["fired"] = {k: v for k, v in state["fired"].items() if v >= cutoff}
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def _alert(key: str, severity: str, title: str, detail: str, tickers: list[str], source: str) -> dict[str, Any]:
    return {"key": key, "severity": severity, "title": title, "detail": detail,
            "tickers": tickers, "source": source, "at": storage.utc_stamp()}


# --- Prices --------------------------------------------------------------------

def check_prices(satellite: dict[str, float], triggers: list[dict[str, Any]], plan_id: str | None,
                 known_through: str | None = None) -> list[dict[str, Any]]:
    """`known_through` = date of the latest price the current plan has seen; earlier bars do not count as new events"""
    from ..datasource import yf
    watch = sorted(set(satellite) | {"SPY", "^VIX"} | {t["ticker"] for t in triggers})
    raw = yf.download(watch, period="4mo", interval="1d", auto_adjust=False, progress=False, threads=True)
    if raw.empty:
        return []
    closes = raw["Close"] if isinstance(raw.columns, pd.MultiIndex) else raw[["Close"]].rename(columns={"Close": watch[0]})
    alerts: list[dict[str, Any]] = []

    for ticker in watch:
        if ticker not in closes.columns:
            continue
        series = closes[ticker].dropna()
        if len(series) < 30:
            continue
        last, prev = float(series.iloc[-1]), float(series.iloc[-2])
        move = (last / prev - 1.0) * 100
        sigma = float(series.pct_change().iloc[-61:-1].std() * 100)
        z = move / sigma if sigma > 0 else 0.0
        day = series.index[-1].date().isoformat()
        if known_through and day <= known_through:
            continue                      # The current plan has already seen this bar, so it is not news to it

        if ticker == "^VIX":
            if last >= config.ALERT_VIX_LEVEL or move >= config.ALERT_VIX_JUMP:
                severity = "critical" if last >= 40 else "high"
                alerts.append(_alert(f"vix:{day}", severity, f"VIX {last:.1f} ({move:+.0f}% วันนี้)",
                                     "ความกลัวในตลาดพุ่ง — ระบอบความผันผวนอาจเปลี่ยน", ["^VIX"], "price"))
            continue
        if ticker == "SPY" and abs(move) >= config.ALERT_MARKET_MOVE:
            severity = "critical" if abs(move) >= 4 else "high"
            alerts.append(_alert(f"market:{day}", severity, f"SPY {move:+.1f}% วันนี้ ({z:+.1f}σ)",
                                 "ตลาดทั้งตลาดขยับแรงผิดปกติ", ["SPY"], "price"))
        if ticker in satellite:
            kind = "stock" if mandate.kind_of(ticker) == "stock" else "etf"
            if abs(z) >= config.ALERT_SHOCK_Z and abs(move) >= config.ALERT_SHOCK_MIN_MOVE[kind]:
                severity = "critical" if abs(z) >= 5 else "high"
                share = satellite[ticker]
                alerts.append(_alert(f"price:{ticker}:{day}", severity, f"{ticker} {move:+.1f}% ({z:+.1f}σ)",
                                     f"ถืออยู่ {share:.0%} ของ satellite — ขยับแรงกว่าปกติมาก", [ticker], "price"))

    for i, trigger in enumerate(triggers):
        series = closes[trigger["ticker"]].dropna() if trigger["ticker"] in closes.columns else pd.Series(dtype=float)
        if series.empty:
            continue
        last = float(series.iloc[-1])
        crossed = last < trigger["level"] if trigger["kind"] == "close_below" else last > trigger["level"]
        if crossed:
            severity = "high" if trigger["action"] in ("exit", "reduce") else "medium"
            alerts.append(_alert(
                f"trigger:{plan_id}:{i}", severity,
                f"{trigger['ticker']} ทะลุ trigger ({trigger['kind'].replace('_', ' ')} {trigger['level']:g})",
                f"ราคาล่าสุด {last:.2f} · แผนตั้งไว้ว่า → {trigger['action']}: {trigger.get('why', '')}",
                [trigger["ticker"]], "trigger",
            ))
    return alerts


# --- SEC ---------------------------------------------------------------------

def check_sec(stocks: list[str], since: str | None) -> tuple[list[dict[str, Any]], str]:
    """New 8-Ks of holdings, high/critical items only"""
    now = storage.utc_stamp()
    since = since or (storage.utc_now() - timedelta(days=2)).isoformat()
    cik_map = sec_source._ticker_to_cik()
    alerts: list[dict[str, Any]] = []
    for ticker in stocks:
        cik = cik_map.get(ticker)
        if not cik:
            continue
        try:
            response = requests.get(sec_source.SUBMISSIONS_URL.format(cik=cik), headers=sec_source._headers(), timeout=30)
            response.raise_for_status()
            recent = response.json()["filings"]["recent"]
        except (requests.RequestException, ValueError, KeyError):
            continue
        for i, form in enumerate(recent.get("form", [])):
            accepted = sec_source._field(recent, "acceptanceDateTime", i).replace(".000Z", "+00:00")
            if accepted and accepted < since:
                break
            if form != "8-K":
                continue
            items = [c.strip() for c in sec_source._field(recent, "items", i).split(",") if c.strip()]
            severity = sec_source.item_severity(items)
            if severity not in ESCALATE:
                continue
            names = " · ".join(f"{c} {config.SEC_8K_ITEMS.get(c, ('?', ''))[0]}" for c in items)
            accession = recent["accessionNumber"][i]
            alerts.append(_alert(f"sec:{accession}", severity, f"{ticker} ยื่น 8-K: {names}",
                                 f"SEC รับเอกสาร {accepted[:16]} UTC · accession {accession}", [ticker], "sec"))
        time.sleep(config.SEC_MIN_INTERVAL_SEC)
    return alerts, now


# --- Labelled news ----------------------------------------------------------

def check_labels(held: set[str], since: str | None) -> tuple[list[dict[str, Any]], str]:
    now = storage.utc_stamp()
    since = since or (storage.utc_now() - timedelta(days=1)).isoformat()
    titles: dict[str, str] = {}
    alerts: list[dict[str, Any]] = []
    for path in sorted((config.DATA / "labels").glob("*.jsonl")):
        if path.stem < since[:10]:
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                label = json.loads(line)
            except json.JSONDecodeError:
                continue
            if label.get("labeled_at", "") < since or (label.get("materiality") or 0) < config.ALERT_LABEL_MATERIALITY:
                continue
            touched = sorted({e.get("ticker") for e in label.get("entities") or []} & held)
            if not touched:
                continue
            if not titles:
                titles = {r["url"]: r["title"] for r in labels_module._candidates(False)}
            alerts.append(_alert(
                f"label:{label['url']}", "high",
                f"ข่าวแรงกระทบ {', '.join(touched)} (materiality {label['materiality']})",
                f"{titles.get(label['url'], label.get('topic_free', ''))[:160]} — {label.get('rationale', '')}",
                touched, "label",
            ))
    return alerts, now


# --- Record, notify, replan ---------------------------------------------------

def _record(alerts: list[dict[str, Any]]) -> None:
    storage.ensure_dirs()
    month = storage.utc_now().strftime("%Y-%m")
    with (config.ALERTS / f"alerts-{month}.jsonl").open("a", encoding="utf-8") as fh:
        for alert in alerts:
            fh.write(json.dumps(alert, ensure_ascii=False) + "\n")
    day = datetime.now(tz=config.LOCAL_TZ).strftime("%Y-%m-%d")
    page = config.JOURNAL_ALERTS / f"{day}.md"
    lines = [] if page.exists() else [f"# 🚨 การแจ้งเตือน {day}", ""]
    icon = {"critical": "🔴", "high": "🟠", "medium": "🟡", "info": "⚪"}
    for alert in alerts:
        local = datetime.fromisoformat(alert["at"]).astimezone(config.LOCAL_TZ).strftime("%H:%M")
        lines.append(f"- {icon.get(alert['severity'], '·')} **{local}** {alert['title']} — {alert['detail']}")
    with page.open("a", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")


def weekly_due() -> tuple[bool, str]:
    """Weekly round: a weekly plan is due, or nothing was checked for more than 7 days"""
    from . import runner

    state = _load_state()
    last = state.get("weekly_last")
    if runner.due()["due"]:
        return True, "ก่อนออกแผนรายสัปดาห์"
    if last is None or storage.utc_now() - datetime.fromisoformat(last) > timedelta(days=7):
        return True, "ไม่ได้ตรวจมาเกิน 7 วัน"
    return False, f"ตรวจรอบสัปดาห์ไปแล้วเมื่อ {last[:16]} UTC"


def mark_weekly() -> None:
    state = _load_state()
    state["weekly_last"] = storage.utc_stamp()
    _save_state(state)


def run(dry_run: bool = False, escalate: bool = True) -> dict[str, Any]:
    """Check one round and return a summary; dry_run = check only, no alerts, nothing saved, no replan"""
    state = _load_state()
    satellite, _, plan_id = plans_module.current_targets()
    record = plans_module.latest()
    triggers = (record or {}).get("targets", {}).get("triggers") or []
    held = set(satellite)
    stocks = [t for t in satellite if mandate.kind_of(t) == "stock"]

    # Events from before the current plan was issued are already known to it; no need to wake it for old news
    known_through = (record or {}).get("information", {}).get("prices_as_of")
    plan_created = (record or {}).get("created_at")
    if plan_created:
        state["sec_since"] = max(filter(None, [state.get("sec_since"), plan_created]))
        state["labels_since"] = max(filter(None, [state.get("labels_since"), plan_created]))

    found: list[dict[str, Any]] = []
    errors: list[str] = []
    for name, check in (
        ("prices", lambda: check_prices(satellite, triggers, plan_id, known_through)),
        ("sec", lambda: check_sec(stocks, state.get("sec_since"))),
        ("labels", lambda: check_labels(held, state.get("labels_since"))),
    ):
        try:
            result = check()
        except Exception as exc:                            # noqa: BLE001 (one failure must not stop the others)
            errors.append(f"{name}: {type(exc).__name__}: {exc}")
            continue
        if name == "sec":
            result, state["sec_since"] = result
        elif name == "labels":
            result, state["labels_since"] = result
        found.extend(result)

    new = [a for a in found if a["key"] not in state["fired"]]
    summary: dict[str, Any] = {"ok": True, "checked": len(found), "new": len(new), "errors": errors,
                               "alerts": [f"[{a['severity']}] {a['title']}" for a in new]}
    if dry_run:
        return summary

    for alert in new:
        state["fired"][alert["key"]] = alert["at"]
    if new:
        _record(new)
        for alert in new:
            if alert["severity"] != "info":
                notify.send(f"🚨 {alert['title']}", alert["detail"], alert["severity"])
    _save_state(state)

    urgent = [a for a in new if a["severity"] in ESCALATE]
    if urgent and escalate:
        from . import runner
        result = runner.run("replan", trigger=[f"[{a['severity']}] {a['title']} — {a['detail']}" for a in urgent])
        summary["replan"] = (result.get("plan_id") if result.get("ok") and not result.get("skipped")
                             else result.get("reason"))
    return summary
