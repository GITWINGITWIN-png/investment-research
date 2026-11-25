"""Scan the whole market once per closed trading day; morning.sh calls this daily.

Skipped automatically when the US market is still open (data incomplete), that day was already scanned, or the data repeats the previous day (a holiday).
"""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from typing import Any

import pandas as pd

from .. import config, market, notify, storage
from ..collect import fundamentals as fundamentals_module
from . import book, deepdive, filings, panel, score, triage, universe

STATE = config.OPPORTUNITIES / "state.json"
REQUESTS = config.OPPORTUNITIES / "requests"


def last_completed_session(now: datetime) -> date:
    et = market.to_et(now)
    day = et.date()
    if et.weekday() < 5 and et.time() >= market.SESSION_CLOSE:
        return day
    day -= timedelta(days=1)
    while day.weekday() >= 5:
        day -= timedelta(days=1)
    return day


def _state() -> dict[str, Any]:
    try:
        return json.loads(STATE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"scanned": [], "holidays": []}


def _save_state(state: dict[str, Any]) -> None:
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(state, indent=2), encoding="utf-8")


def _money(value: float | None) -> str:
    if not value:
        return "—"
    return f"${value / 1e9:.1f}B" if value >= 1e9 else f"${value / 1e6:.0f}M"


def _deep_prompt(picks: list[str], by_ticker: dict[str, dict[str, Any]], news: dict[str, list[dict[str, Any]]],
                 ticker_cik: dict[str, int], session: date) -> tuple[str, dict[str, dict[str, str]]]:
    refs: dict[str, dict[str, str]] = {}

    def ref(url: str, title: str) -> str:
        key = f"D{len(refs) + 1}"
        refs[key] = {"url": url, "title": title}
        return key

    data = panel.load()
    ret = data.pivot(index="date", columns="ticker", values="ret").sort_index()
    close = data.pivot(index="date", columns="ticker", values="close").sort_index()
    volume = data.pivot(index="date", columns="ticker", values="volume").sort_index()
    bench = storage.price_matrix("adj_close")[["SPY", "IWM"]].pct_change() * 100

    lines = [f"# ดำลึก {len(picks)} ตัว — ข้อมูลถึงวันทำการ {session.isoformat()}", ""]
    for ticker in picks:
        c = by_ticker[ticker]
        lines += [
            f"## {ticker} · {c['name'][:70]}",
            f"{c['sector']} / {c['industry']} · มูลค่า {_money(c['market_cap'])} · "
            f"นักวิเคราะห์ {c.get('n_estimates') if c.get('n_estimates') is not None else 'ไม่มีข้อมูล'} คน · "
            f"benchmark {book.benchmark_for(c['market_cap'])}",
            "เหตุที่เข้ารอบ: " + " · ".join(c["reasons"]),
            "",
        ]
        if ticker in ret.columns:
            series = ret[ticker].dropna()
            sigma = float(series.iloc[-(config.VOL_LOOKBACK + 5):-5].std()) if len(series) > 30 else None
            lines += ["| วันที่ | ราคาปิด | % | z | volume เทียบมัธยฐาน 20 วัน | IWM % | SPY % |",
                      "|---|---:|---:|---:|---:|---:|---:|"]
            for day in series.index[-20:]:
                med = volume[ticker].loc[:day].iloc[-21:-1].median()
                vr = volume.at[day, ticker] / med if med else None
                z = series[day] / sigma if sigma else None
                iwm = bench["IWM"].get(day)
                spy = bench["SPY"].get(day)
                lines.append(
                    f"| {day.date()} | {close.at[day, ticker]:.2f} | {series[day]:+.2f} | "
                    f"{'—' if z is None else f'{z:+.1f}'} | {'—' if vr is None or pd.isna(vr) else f'{vr:.1f}x'} | "
                    f"{'—' if iwm is None or pd.isna(iwm) else f'{iwm:+.2f}'} | {'—' if spy is None or pd.isna(spy) else f'{spy:+.2f}'} |"
                )
            lines.append("")

        cik = ticker_cik.get(ticker)
        if cik:
            try:
                facts = fundamentals_module.facts_for(ticker, f"{cik:010d}")
                f = fundamentals_module.summary(closes={ticker: c["close"]}, facts=facts).get(ticker)
            except Exception:                                   # noqa: BLE001 (missing financials should not break the whole round)
                f = None
            if f:
                def pct(x: Any) -> str:
                    return "—" if x is None else f"{x:+.1f}%"
                lines.append(
                    f"งบ (SEC XBRL งวด {f.get('period_end')}{' ⚠️เก่า' if f.get('stale') else ''}): รายได้ TTM {_money(f.get('revenue_ttm'))} "
                    f"โต {pct(f.get('revenue_growth_pct'))} · ไตรมาสล่าสุด YoY {pct(f.get('revenue_q_yoy_pct'))} · "
                    f"margin {'—' if f.get('net_margin_pct') is None else f'{f['net_margin_pct']:.1f}%'} · "
                    f"P/E {'—' if f.get('pe') is None else f'{f['pe']:.1f}'} · "
                    f"FCF yield {'—' if f.get('fcf_yield_pct') is None else f'{f['fcf_yield_pct']:.1f}%'} · "
                    f"D/E {'—' if f.get('debt_to_equity') is None else f'{f['debt_to_equity']:.2f}'}"
                )
                lines.append("")

        report = c.get("earnings")
        if report:
            lines += [f"งบที่เพิ่งประกาศ {report['report_date']}: EPS {report.get('eps')} vs คาด {report.get('eps_forecast')} "
                      f"(surprise {report.get('surprise_pct')}%) · นักวิเคราะห์ {report.get('n_estimates')} คน", ""]
        insider = c.get("insider")
        if insider:
            lines += ["คนในซื้อในตลาด (10 วัน, ไม่นับแผน 10b5-1):", *[f"- {d}" for d in insider["detail"]], ""]

        for event in c.get("events") or []:
            url = "https://www.sec.gov/Archives/" + event["path"]
            if event["kind"] == "8-K":
                names = ", ".join(f"{i} {config.SEC_8K_ITEMS.get(i, ('?',))[0]}" for i in event.get("items") or [])
                key = ref(url, f"8-K {ticker} {event['filed']}")
                text = filings.document_text(event["path"], 6000)
                lines += [f"**{key} · 8-K ยื่น {event['filed']}** — {names}", "", text or "(อ่านเนื้อหาไม่ได้)", ""]
            elif event["kind"] == "13D":
                key = ref(url, f"{event.get('form')} {ticker} {event['filed']}")
                text = filings.document_text(event["path"], 3000)
                lines += [f"**{key} · {event.get('form')} ยื่น {event['filed']}** (ผู้ยื่น: {event.get('company')})", "",
                          text or "(อ่านเนื้อหาไม่ได้)", ""]

        items = news.get(ticker) or []
        if items:
            lines.append("ข่าว 3 วันล่าสุด:")
            for item in items[:10]:
                key = ref(item["url"], item["title"])
                lines.append(f"- {key} [{item['published_at'][:16]}] {item['publisher']}: {item['title'][:160]}")
            lines.append("")

    lines += ["## วิธีตอบ", "ใส่ `ideas` ครบทุกตัวข้างบน 1 รายการต่อ 1 ตัว · อ้างหลักฐานด้วย D1, D2, ... หรือ \"ราคา\"/\"งบ\""]
    return "\n".join(lines), refs


def run(*, dry_run: bool = False, no_llm: bool = False, force: bool = False) -> dict[str, Any]:
    now = storage.utc_now()
    if market.is_session_open(now) and not force:
        return {"ok": True, "skipped": "ตลาดสหรัฐเปิดอยู่ — ข้อมูลวันนี้ยังไม่ครบ"}
    session = last_completed_session(now)
    label = session.isoformat()
    state = _state()
    if label in state["scanned"] and not force:
        return {"ok": True, "skipped": f"สแกนวันทำการ {label} ไปแล้ว"}

    # ── Stage 0: collect the whole market ────────────────────────────────────────────
    # screener = stock list + market cap + sector (stored point-in-time); real prices come from yfinance because the screener can be stale
    snap, _ = universe.snapshot(label)
    wanted = sorted(universe.tradable(snap)["ticker"])
    update = panel.update_recent(wanted)
    if update.get("latest") != label and not force:
        return {"ok": True, "skipped": f"ยังไม่มีราคาของวันทำการ {label} (ล่าสุด {update.get('latest')}) — "
                                        "วันหยุดตลาดหรือข้อมูลยังไม่เข้า รอบหน้าลองใหม่"}
    # Incomplete prices mean deciding on some stale data (it has happened), so no scan; try again next round
    if (update.get("coverage") or 0) < 0.95 and not force:
        return {"ok": True, "skipped": f"ได้ราคาของวันทำการ {label} แค่ {update.get('coverage', 0):.0%} ของ universe "
                                        f"(ไม่ได้ {update.get('failed')} ตัว — น่าจะโดนจำกัดความถี่) รอบหน้าลองใหม่"}
    base = panel.baselines(label)
    tradable = universe.tradable(snap, base["dollar_volume_median"] if not base.empty else None)
    need = [t for t in tradable["ticker"] if base.empty or t not in base.index or (base.at[t, "history"] or 0) < 60]
    backfill = panel.backfill(need) if need else {"filled": 0}
    if backfill.get("filled"):
        base = panel.baselines(label)

    cik_map = filings.cik_to_tickers()
    ticker_cik = {t: c for c, ts in cik_map.items() for t in ts}
    ciks = {ticker_cik[t] for t in tradable["ticker"] if t in ticker_cik}
    seen = filings.seen_accessions()
    sec = [filings.collect_day(day, ciks, seen) for day in filings.recent_sessions_back(session, 2)]
    events = filings.events_since(session - timedelta(days=4))
    insider = score.insider_summary(filings.insider_history(session - timedelta(days=config.INSIDER_LOOKBACK_DAYS)))
    # Public edition: the earnings calendar came from api.nasdaq.com, whose robots.txt disallows
    # automated access, so this copy scores without surprise and analyst-count inputs.
    calendar: dict[str, list[dict[str, Any]]] = {"reported": [], "upcoming": []}
    coverage = {r["ticker"]: r["n_estimates"] for r in calendar["reported"] + calendar["upcoming"]
                if r["n_estimates"] is not None}
    reported = [r for r in calendar["reported"] if r["report_date"] >= (session - timedelta(days=3)).isoformat()]

    # ── Stage 1: score ────────────────────────────────────────────────
    scored = score.score(tradable, base, events, insider, reported, coverage, cik_map)
    candidates = scored.head(config.SCAN_CANDIDATES).to_dict(orient="records") if not scored.empty else []
    # Stocks without a real price for that day may not become finalists (so the model never analyses stale rows)
    fresh_today = set(panel.load().query("date == @pd.Timestamp(@label)")["ticker"])
    if not scored.empty:
        scored = scored[scored["ticker"].isin(fresh_today)].reset_index(drop=True)
        candidates = scored.head(config.SCAN_CANDIDATES).to_dict(orient="records")
    summary: dict[str, Any] = {
        "ok": True, "session": label, "universe": len(tradable), "backfilled": backfill.get("filled", 0),
        "price_coverage": update.get("coverage"),
        "sec_filings": sum(s["filings"] for s in sec), "events": len(events), "insider_buyers": len(insider),
        "earnings_reported": len(reported), "scored": len(scored), "candidates": len(candidates),
        "top": [f"{c['ticker']} {c['score']:.1f}: {' · '.join(c['reasons'])[:120]}" for c in candidates[:10]],
    }
    if dry_run:
        return summary

    created_at = storage.utc_stamp()
    run = f"{label}@{created_at[:16].replace(':', '')}"
    REQUESTS.mkdir(parents=True, exist_ok=True)
    triage_map: dict[str, dict[str, Any]] = {}
    ideas: list[dict[str, Any]] = []
    refs: dict[str, dict[str, str]] = {}
    costs = 0.0

    if candidates and not no_llm:
        # ── Stage 2: Sonnet screens ──────────────────────────────────────
        # Public edition: per-company news came from Google News RSS search, which
        # news.google.com disallows for automated clients, so the cards carry no news.
        news: dict[str, list[dict[str, Any]]] = {}
        excerpts = {}
        for c in candidates:
            filing = next((e for e in c.get("events") or [] if e["kind"] == "8-K"), None)
            if filing:
                excerpts[c["ticker"]] = filings.document_text(filing["path"], 900)
        cards = [triage.card(c, news.get(c["ticker"], []), excerpts.get(c["ticker"], "")) for c in candidates]
        screened = triage.run(candidates, cards)
        (REQUESTS / f"{run}-triage.md").write_text(screened.pop("prompt"), encoding="utf-8")
        costs += screened.get("cost_usd") or 0
        if screened.get("ok"):
            triage_map = {str(c["ticker"]).upper(): c for c in screened["output"].get("candidates") or []}
            picks = triage.pick(screened["output"], {c["ticker"] for c in candidates}, config.SCAN_DEEP_DIVES)
        else:
            picks = []
            summary["triage_error"] = screened.get("error")

        # ── Stage 3: Opus deep dive ──────────────────────────────────────────
        if picks:
            by_ticker = {c["ticker"]: c for c in candidates}
            prompt, refs = _deep_prompt(picks, by_ticker, news, ticker_cik, session)
            (REQUESTS / f"{run}-deepdive.md").write_text(prompt, encoding="utf-8")
            result = deepdive.run(prompt)
            costs += result.get("cost_usd") or 0
            errors = deepdive.validate(result["output"], picks) if result.get("ok") else [str(result.get("error"))]
            if errors and result.get("ok"):
                retry = deepdive.run(prompt + "\n\n## ⚠️ คำตอบรอบก่อนไม่ครบ\n" + "\n".join(f"- {e}" for e in errors))
                costs += retry.get("cost_usd") or 0
                if retry.get("ok") and not deepdive.validate(retry["output"], picks):
                    result, errors = retry, []
            if result.get("ok"):
                ideas = [i for i in result["output"]["ideas"] if str(i.get("ticker", "")).upper() in picks]
                summary["deepdive_notes"] = result["output"].get("notes", "")
            if errors:
                summary["deepdive_error"] = "; ".join(errors)
        summary["picks"] = picks

    recorded = book.record(label, created_at, candidates, triage_map, ideas)
    page = _render_day(label, summary, candidates, triage_map, ideas, refs, recorded)
    state["scanned"].append(label)
    _save_state(state)
    resolution = book.resolve()
    board = book.scoreboard()
    _render_scoreboard(board)

    summary.update({"booked": recorded["booked"], "control": recorded["control"], "page": str(page.relative_to(config.ROOT)),
                    "cost_usd": round(costs, 3), "resolved_now": resolution.get("resolved", 0)})
    if candidates:
        booked = ", ".join(recorded["booked"]) or "ไม่มี"
        notify.send(f"🔭 สแกนทั้งตลาด {label}",
                    f"เข้ารอบ {len(candidates)} จาก {len(tradable):,} ตัว · ดำลึก {len(summary.get('picks') or [])} · "
                    f"เข้าพอร์ตไอเดีย: {booked}", "info")
    return summary


def _render_day(label: str, summary: dict[str, Any], candidates: list[dict[str, Any]],
                triage_map: dict[str, dict[str, Any]], ideas: list[dict[str, Any]],
                refs: dict[str, dict[str, str]], recorded: dict[str, Any]) -> Any:
    names = {"add_to_book": "✅ เข้าพอร์ตไอเดีย", "watch": "👀 จับตา", "pass": "⏭️ ผ่าน"}

    def link(item: str) -> str:
        r = refs.get(item.strip())
        return f"[{item.strip()}]({r['url']})" if r else item

    lines = [
        "---", f"session: {label}", "tags: [opportunity, scan]", "---", "",
        f"# 🔭 สแกนทั้งตลาด — วันทำการ {label}", "",
        f"หุ้นที่ซื้อขายได้จริง **{summary['universe']:,}** ตัว · filing ของ SEC {summary['sec_filings']:,} ฉบับ · "
        f"เหตุการณ์ 8-K/13D ของ universe {summary['events']} · คนในซื้อในตลาด {summary['insider_buyers']} บริษัท · "
        f"ประกาศงบ {summary['earnings_reported']} บริษัท → ได้คะแนน {summary['scored']} ตัว → เข้ารอบ {len(candidates)}",
        "",
    ]
    if ideas:
        lines += ["## ผลดำลึก", ""]
        for idea in ideas:
            lines += [
                f"### {idea['ticker']} — {names.get(idea['decision'], idea['decision'])} · `{idea['idea_type']}` · มั่นใจ {idea['confidence']}%",
                "",
                f"- **เกิดอะไรขึ้น:** {idea['what_happened']}",
                f"- **ทำไมตลาดอาจมองข้าม:** {idea['why_overlooked']}",
                f"- **ตัวเร่ง:** {idea['catalyst']}",
                f"- **ความเสี่ยง:** " + "; ".join(idea.get("risks") or []),
                f"- **ถ้าเห็นสิ่งนี้แปลว่าผิด:** {idea['proves_me_wrong']}",
                f"- **หลักฐาน:** " + " · ".join(link(e) for e in idea.get("evidence") or []),
                "",
            ]
        if summary.get("deepdive_notes"):
            lines += [f"> {summary['deepdive_notes']}", ""]
    if recorded["control"]:
        lines += [f"**กลุ่มควบคุม (สุ่มจากผู้เข้ารอบ ด้วย seed ของวันที่):** {', '.join(recorded['control'])}", ""]
    for key in ("triage_error", "deepdive_error"):
        if summary.get(key):
            lines += [f"> [!warning] {key}: {summary[key]}", ""]

    lines += ["## ผู้เข้ารอบ", "",
              "| # | หุ้น | มูลค่า | นักวิเคราะห์ | คะแนน | เหตุที่เข้ารอบ | Sonnet (ข้อมูล/มองข้าม) |",
              "|---|---|---:|---:|---:|---|---|"]
    for i, c in enumerate(candidates, start=1):
        t = triage_map.get(c["ticker"])
        tri = f"{t['information_value']}/{t['overlooked']}{' 🔎' if t['deep_dive'] else ''} — {t['reason']}" if t else "—"
        lines.append(f"| {i} | **{c['ticker']}** {c['name'][:28]} | {_money(c['market_cap'])} | "
                     f"{c.get('n_estimates') if c.get('n_estimates') is not None else '—'} | {c['score']:.1f} | "
                     f"{' · '.join(c['reasons'])[:150]} | {tri} |")
    lines += ["", f"*ต้นทุนโมเดลเทียบเท่า ${summary.get('cost_usd', 0) or 0:.2f} · ข้อมูลที่โมเดลได้อ่าน: "
              f"`data/opportunities/requests/{label}@*.md`*", "", "🔗 [[scoreboard]] · [[12 - Opportunity Scanner]]", ""]
    config.JOURNAL_OPPORTUNITIES.mkdir(parents=True, exist_ok=True)
    page = config.JOURNAL_OPPORTUNITIES / f"{label}.md"
    page.write_text("\n".join(lines), encoding="utf-8")
    return page


def _render_scoreboard(board: dict[str, Any]) -> None:
    def row(name: str, s: dict[str, Any]) -> str:
        if not s["n"]:
            return f"| {name} | 0 | — | — | — |"
        return f"| {name} | {s['n']} | {s['mean_pct']:+.2f}% | {s['hit_pct']}% | {s['t'] if s['t'] is not None else '—'} |"

    def ic_row(name: str, s: dict[str, Any]) -> str:
        if not s["sessions"]:
            return f"| {name} | 0 | — | — |"
        return f"| {name} | {s['sessions']} | {s['mean_ic']:+.4f} | {s['t_nw'] if s['t_nw'] is not None else '—'} |"

    diff = board.get("ideas_minus_control")
    tri = board["triage_ic"]
    lines = [
        "---", "tags: [opportunity, scoreboard]", "---", "",
        "# 📊 พอร์ตไอเดีย — ผลจริงหลัง 20 วันทำการ", "",
        f"*อัปเดต {storage.utc_now().astimezone(config.LOCAL_TZ):%Y-%m-%d %H:%M} · ไอเดียที่ยังถืออยู่ {board['open_ideas']} · "
        f"ข้อมูลหาย {board['data_missing']} · {board['gate']}*", "",
        "ผลตอบแทนส่วนเกินเทียบ benchmark หลังหักต้นทุน 30 bps", "",
        "| กลุ่ม | จำนวน | เฉลี่ย | ชนะ benchmark | t |", "|---|---:|---:|---:|---:|",
        row("ไอเดียของ Claude", board["ideas"]), row("กลุ่มควบคุม (สุ่ม)", board["control"]),
        row("ผู้เข้ารอบทั้งหมด (ตัวกรองของโค้ด)", board["candidates"]), "",
        f"**Claude − สุ่ม:** {f'{diff['diff_pct']:+.2f} จุด · t = {diff['t']}' if diff else 'ยังไม่พอคำนวณ'} "
        "← ตัวเลขที่ตอบคำถามหลัก: การดำลึกหาโอกาสที่คนมองข้ามได้จริงไหม",
        f"**Brier ของความมั่นใจ:** {board['brier'] if board['brier'] is not None else '—'} (ต่ำกว่า 0.25 = ดีกว่าเดาครึ่งต่อครึ่ง)", "",
        "## การทดสอบรอง — คะแนนของ Sonnet เรียงผู้เข้ารอบตรงกับผลจริงไหม (ตั้ง 2026-11-08)", "",
        "ใช้ผู้เข้ารอบทุกตัว (~30 ตัว/รอบ) จึงไวกว่าการทดสอบหลักที่ได้ ~1 ไอเดีย/วัน · "
        "IC = rank correlation ต่อรอบ เฉลี่ยข้ามรอบ · t แบบ Newey-West เพราะผลของรอบติดกันซ้อนทับกัน", "",
        "| คะแนน | รอบ | IC เฉลี่ย | t |", "|---|---:|---:|---:|",
        ic_row("Sonnet: ข้อมูล × มองข้าม", tri["sonnet"]), ic_row("ชั้น 1 ของโค้ด (อ้างอิง)", tri["code"]),
        ic_row("ส่วนที่ Sonnet เพิ่มจากคะแนนของโค้ด", tri["sonnet_beyond_code"]), "",
        "| ผู้เข้ารอบ | จำนวน | เฉลี่ย | ชนะ benchmark | t |", "|---|---:|---:|---:|---:|",
        row("Sonnet ส่งไปดำลึก", tri["deep_dive"]), row("Sonnet ไม่ส่ง", tri["not_deep_dive"]), "",
        f"**ผล:** {tri['verdict']}", "",
        "## แยกตามประเภทไอเดีย", "", "| ประเภท | จำนวน | เฉลี่ย | ชนะ | t |", "|---|---:|---:|---:|---:|",
        *[row(k, v) for k, v in board["by_type"].items()], "",
        "🔗 [[12 - Opportunity Scanner]]", "",
    ]
    config.JOURNAL_OPPORTUNITIES.mkdir(parents=True, exist_ok=True)
    (config.JOURNAL_OPPORTUNITIES / "scoreboard.md").write_text("\n".join(lines), encoding="utf-8")
