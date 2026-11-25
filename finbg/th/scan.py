"""Thai scanner: stages 1 to 3 plus the idea portfolio (note 13); morning.sh calls it daily after `finbg th collect`.

    stage 0  collect.py   stock list, prices, news, Form 59 tables                     (0 tokens)
    stage 1  score.py     tradable stocks with new news or insider buying -> <= 20     (0 tokens)
    stage 2  llm.triage   Sonnet reads the cards -> sends some for a deep dive
    stage 3  llm.deep_*   Opus reads the full articles for <= 2 stocks -> ideas
    results  book.py      idea portfolio + a control group drawn from the same finalists

One scan per closed trading day; if the news in the store is not fresh (news collection failed), there is no scan, so nothing is decided on old news.
"""

from __future__ import annotations

import json
from datetime import timedelta
from typing import Any

import pandas as pd

from .. import config, notify, storage
from . import book, llm, prices, score

STATE = config.TH_OPPORTUNITIES / "state.json"
REQUESTS = config.TH_OPPORTUNITIES / "requests"


def _state() -> dict[str, Any]:
    return json.loads(STATE.read_text(encoding="utf-8")) if STATE.exists() else {"scanned": []}


def _save_state(state: dict[str, Any]) -> None:
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def latest_session() -> pd.Timestamp | None:
    table = prices.read_all()
    if table.empty:
        return None
    days = table.loc[table["ticker"] == config.TH_CALENDAR_TICKER, "date"]
    return pd.Timestamp(days.max()) if not days.empty else None


def news_fresh() -> tuple[bool, str]:
    files = sorted(config.RAW_TH_NEWS.glob("*.jsonl"))
    if not files:
        return False, "ยังไม่มีข่าวในคลัง"
    first = files[-1].read_text(encoding="utf-8").splitlines()[:1]
    fetched = pd.Timestamp(json.loads(first[0])["fetched_at"]) if first else None
    age = storage.utc_now() - fetched.to_pydatetime() if fetched is not None else None
    if age is None or age > timedelta(hours=config.TH_SCAN_NEWS_FRESH_HOURS):
        return False, f"ข่าวล่าสุดในคลังเก่าเกิน {config.TH_SCAN_NEWS_FRESH_HOURS} ชม. — ไม่สแกนจากข่าวเก่า"
    return True, ""


def _save_request(label: str, run_id: str, step: str, prompt: str) -> None:
    REQUESTS.mkdir(parents=True, exist_ok=True)
    (REQUESTS / f"{label}@{run_id}-{step}.md").write_text(prompt, encoding="utf-8")


def _th(ticker: str) -> str:
    return f"{str(ticker).upper().removesuffix('.BK')}.BK"


def run(*, dry_run: bool = False, no_llm: bool = False, force: bool = False) -> dict[str, Any]:
    state = _state()
    session = latest_session()
    if session is None:
        return {"skipped": "ยังไม่มีราคาในคลัง — รัน `finbg th collect` ก่อน"}
    label = str(session.date())
    if label in state["scanned"] and not force and not dry_run:
        return {"skipped": f"สแกนวันทำการ {label} ไปแล้ว"}
    fresh, why = news_fresh()
    if not fresh and not dry_run:
        return {"skipped": why}

    now, created_at = storage.utc_now(), storage.utc_stamp()
    candidates, summary = score.candidates(session, now)
    if dry_run:
        return {**summary, "dry_run": True,
                "top": [f"{c['ticker']} {c['score']:.1f} · {' · '.join(c['reasons'])}" for c in candidates]}

    run_id = created_at[:16].replace(":", "")
    triage_map: dict[str, dict[str, Any]] = {}
    picks: list[str] = []
    ideas: list[dict[str, Any]] = []
    refs: dict[str, dict[str, str]] = {}
    cost = 0.0
    if candidates and not no_llm:
        screened = llm.triage(candidates)
        _save_request(label, run_id, "triage", screened["prompt"])
        cost += screened.get("cost_usd") or 0.0
        if screened.get("ok"):
            triage_map = {_th(t["ticker"]): t for t in screened["output"].get("candidates") or []}
            picks = llm.pick(screened["output"], {c["ticker"] for c in candidates})
        else:
            summary["triage_error"] = screened.get("error")
        if picks:
            by_ticker = {c["ticker"]: c for c in candidates}
            prompt, refs, fetched = llm.deep_prompt(picks, by_ticker, session)
            summary["articles_read"] = sum(1 for f in fetched if f["status"] == "ok")
            _save_request(label, run_id, "deepdive", prompt)
            result = llm.deep_dive(prompt)
            cost += result.get("cost_usd") or 0.0
            if result.get("ok"):
                ideas = [{**i, "ticker": _th(i["ticker"])} for i in result["output"].get("ideas") or []
                         if _th(i.get("ticker", "")) in picks]
                summary["deepdive_notes"] = result["output"].get("notes", "")
                errors = llm.validate(result["output"], picks)
                if errors:
                    summary["deepdive_error"] = "; ".join(errors)
            else:
                summary["deepdive_error"] = result.get("error")

    recorded = book.record(label, created_at, candidates, triage_map, ideas)
    page = _render_day(label, summary, candidates, triage_map, ideas, refs, recorded, cost)
    state["scanned"].append(label)
    _save_state(state)
    resolution = book.resolve()
    _render_scoreboard(book.scoreboard())
    if candidates:
        notify.send(f"🇹🇭 สแกนหุ้นไทย {label}",
                    f"เข้ารอบ {len(candidates)} จาก {summary['tradable']} ตัว · ดำลึก {len(picks)} · "
                    f"เข้าพอร์ตไอเดีย: {', '.join(recorded['booked']) or 'ไม่มี'}", "info")
    return {**summary, "candidates": len(candidates), "picks": picks, "booked": recorded["booked"],
            "control": recorded["control"], "cost_usd": round(cost, 3), "resolved_now": resolution.get("resolved", 0),
            "page": str(page.relative_to(config.ROOT))}


def _render_day(label: str, summary: dict[str, Any], candidates: list[dict[str, Any]],
                triage_map: dict[str, dict[str, Any]], ideas: list[dict[str, Any]],
                refs: dict[str, dict[str, str]], recorded: dict[str, Any], cost: float) -> Any:
    names = {"add_to_book": "✅ เข้าพอร์ตไอเดีย", "watch": "👀 จับตา", "pass": "⏭️ ผ่าน"}

    def link(item: str) -> str:
        r = refs.get(item.strip())
        return f"[{item.strip()}]({r['url']})" if r else item

    lines = ["---", f"session: {label}", "tags: [thailand, opportunity, scan]", "---", "",
             f"# 🇹🇭 สแกนหุ้นไทย — วันทำการ {label}", "",
             f"หุ้นที่ซื้อขายได้จริง **{summary['tradable']}** ตัว · มีข่าวใหม่ {summary['with_news']} · "
             f"ผู้บริหารซื้อ {summary['with_insider_buys']} → มีเหตุการณ์ {summary['events']} → เข้ารอบ {len(candidates)}",
             f"หน้าต่างข่าว {summary['window']} · benchmark = หุ้นที่ซื้อขายได้ทั้งหมดถือเท่ากัน · ต้นทุนตาม tick + ค่าคอม", ""]
    if ideas:
        lines += ["## ผลดำลึก", ""]
        for idea in ideas:
            lines += [f"### {idea['ticker'].removesuffix('.BK')} — {names.get(idea['decision'], idea['decision'])} · "
                      f"`{idea['idea_type']}` · มั่นใจ {idea['confidence']}%", "",
                      f"- **เกิดอะไรขึ้น:** {idea['what_happened']}",
                      f"- **ทำไมตลาดอาจมองข้าม:** {idea['why_overlooked']}",
                      f"- **ตัวเร่ง:** {idea['catalyst']}",
                      "- **ความเสี่ยง:** " + "; ".join(idea.get("risks") or []),
                      f"- **ถ้าเห็นสิ่งนี้แปลว่าผิด:** {idea['proves_me_wrong']}",
                      "- **หลักฐาน:** " + " · ".join(link(e) for e in idea.get("evidence") or []), ""]
        if summary.get("deepdive_notes"):
            lines += [f"> {summary['deepdive_notes']}", ""]
    if recorded["control"]:
        lines += [f"**กลุ่มควบคุม (สุ่มจากผู้เข้ารอบ ด้วย seed ของวันที่):** "
                  f"{', '.join(t.removesuffix('.BK') for t in recorded['control'])}", ""]
    for key in ("triage_error", "deepdive_error"):
        if summary.get(key):
            lines += [f"> [!warning] {key}: {summary[key]}", ""]
    lines += ["## ผู้เข้ารอบ", "", "| # | หุ้น | มูลค่า | ซื้อขาย/วัน | คะแนน | เหตุที่เข้ารอบ | Sonnet (ข้อมูล/มองข้าม) |",
              "|---|---|---:|---:|---:|---|---|"]
    for n, c in enumerate(candidates, start=1):
        t = triage_map.get(c["ticker"])
        tri = f"{t['information_value']}/{t['overlooked']}{' 🔎' if t['deep_dive'] else ''} — {t['reason']}" if t else "—"
        cap = f"฿{c['market_cap'] / 1e9:.1f}bn" if c.get("market_cap") else "—"
        lines.append(f"| {n} | **{c['ticker'].removesuffix('.BK')}** {c['name'][:26]} | {cap} | ฿{c['traded_value'] / 1e6:.0f}M | "
                     f"{c['score']:.1f} | {' · '.join(c['reasons'])[:120]} | {tri} |")
    lines += ["", f"*ต้นทุนโมเดลเทียบเท่า ${cost:.2f} · ข้อมูลที่โมเดลได้อ่าน: `data/opportunities_th/requests/{label}@*.md`*",
              "", "🔗 [[Thai/scoreboard|scoreboard ไทย]] · [[13 - Thai Track]]", ""]
    config.JOURNAL_TH.mkdir(parents=True, exist_ok=True)
    page = config.JOURNAL_TH / f"{label}.md"
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

    diff, tri = board.get("ideas_minus_control"), board["triage_ic"]
    lines = ["---", "tags: [thailand, opportunity, scoreboard]", "---", "",
             "# 🇹🇭 พอร์ตไอเดียไทย — ผลจริงหลัง 20 วันทำการ", "",
             f"*อัปเดต {storage.utc_now().astimezone(config.LOCAL_TZ):%Y-%m-%d %H:%M} · ไอเดียที่ยังถืออยู่ {board['open_ideas']} · "
             f"ข้อมูลหาย {board['data_missing']} · {board['gate']}*", "",
             "ผลตอบแทนส่วนเกินเทียบหุ้นไทยที่ซื้อขายได้ทั้งหมด (ถือเท่ากัน) หลังหักต้นทุนตาม tick + ค่าคอม", "",
             "| กลุ่ม | จำนวน | เฉลี่ย | ชนะ benchmark | t |", "|---|---:|---:|---:|---:|",
             row("ไอเดียของ Claude", board["ideas"]), row("กลุ่มควบคุม (สุ่ม)", board["control"]),
             row("ผู้เข้ารอบทั้งหมด (ตัวกรองของโค้ด)", board["candidates"]), "",
             f"**Claude − สุ่ม:** {f'{diff['diff_pct']:+.2f} จุด · t = {diff['t']}' if diff else 'ยังไม่พอคำนวณ'} "
             "← คำถามหลัก: Claude ที่อ่านข่าวภาษาไทยเลือกได้ดีกว่าสุ่มไหม",
             f"**Brier ของความมั่นใจ:** {board['brier'] if board['brier'] is not None else '—'}", "",
             "## การทดสอบรอง — คะแนนของ Sonnet เรียงผู้เข้ารอบตรงกับผลจริงไหม", "",
             "| คะแนน | รอบ | IC เฉลี่ย | t |", "|---|---:|---:|---:|",
             ic_row("Sonnet: ข้อมูล × มองข้าม", tri["sonnet"]), ic_row("ชั้น 1 ของโค้ด (อ้างอิง)", tri["code"]),
             ic_row("ส่วนที่ Sonnet เพิ่มจากคะแนนของโค้ด", tri["sonnet_beyond_code"]), "",
             f"**ผล:** {tri['verdict']}", "",
             "## แยกตามประเภทไอเดีย", "", "| ประเภท | จำนวน | เฉลี่ย | ชนะ | t |", "|---|---:|---:|---:|---:|",
             *[row(k, v) for k, v in board["by_type"].items()], "", "🔗 [[13 - Thai Track]]", ""]
    config.JOURNAL_TH.mkdir(parents=True, exist_ok=True)
    (config.JOURNAL_TH / "scoreboard.md").write_text("\n".join(lines), encoding="utf-8")
