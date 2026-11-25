"""Turn a context pack into a Situation Brief that opens in Obsidian.

This is "layer 1.5": it lays the facts out for people **and** for an LLM.
Interpretation is left for layer 2 to fill in (see the "Analysis" section at the end of the file).

Why the brief is separate from the context pack (JSON):
- JSON suits an LLM (clear structure, no filler words)
- Markdown suits people and serves as a permanent note in Obsidian
Both come from the same source, so they cannot disagree.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from .. import config, storage

ARROW_UP = "🟢"
ARROW_DOWN = "🔴"
ARROW_FLAT = "⚪"


def _fmt_pct(value: Any, width: int = 0) -> str:
    """Format a percentage with its sign; '—' when there is no data"""
    if value is None:
        return "—".rjust(width)
    return f"{value:+.2f}%".rjust(width)


def _signal(value: Any) -> str:
    """Coloured symbol showing the direction at a glance"""
    if value is None:
        return ARROW_FLAT
    if value > 0.5:
        return ARROW_UP
    if value < -0.5:
        return ARROW_DOWN
    return ARROW_FLAT


def _returns_table(items: dict[str, dict[str, Any]], label_key: str | None = "label") -> list[str]:
    """Multi-period return table, reused for benchmarks, sectors and the watchlist"""
    lines = [
        "| | สินทรัพย์ | ราคา | 1d | 1w | 1m | 3m | 1y | vs MA200 | ตำแหน่ง 52w | mom 12-1 | vol 60d |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for ticker, data in items.items():
        if not data or "returns_pct" not in data:
            continue
        rets = data["returns_pct"]
        name = data.get(label_key, "") if label_key else ""
        display = f"**{ticker}**" + (f" · {name}" if name else "")
        week52 = data.get("week52", {})
        position = week52.get("position_pct")
        lines.append(
            f"| {_signal(rets.get('1m'))} | {display} | {data.get('last', '—')} | "
            f"{_fmt_pct(rets.get('1d'))} | {_fmt_pct(rets.get('1w'))} | "
            f"{_fmt_pct(rets.get('1m'))} | {_fmt_pct(rets.get('3m'))} | "
            f"{_fmt_pct(rets.get('1y'))} | {_fmt_pct(data.get('vs_ma200_pct'))} | "
            f"{position if position is not None else '—'} | "
            f"{_fmt_pct(data.get('mom_12_1_pct'))} | "
            f"{_fmt_num(data.get('vol_60d_pct'), '%')} |"
        )
    return lines


def _fmt_num(value: Any, suffix: str = "", digits: int = 1) -> str:
    return "—" if value is None else f"{value:.{digits}f}{suffix}"


def _fmt_money(value: Any) -> str:
    """Large amounts in readable form: 466.8B / 89.1M"""
    if value is None:
        return "—"
    if abs(value) >= 1e12:
        return f"{value / 1e12:.2f}T"
    if abs(value) >= 1e9:
        return f"{value / 1e9:.1f}B"
    return f"{value / 1e6:.0f}M"


def _fundamentals_section(pack: dict[str, Any]) -> list[str]:
    """Latest financials table for the watchlist, from SEC XBRL (the figures as the companies filed them)"""
    data = pack.get("fundamentals") or {}
    if not data:
        return ["*ยังไม่มีข้อมูลงบ — รัน `uv run finbg daily` (ดึงอัตโนมัติสัปดาห์ละครั้ง)*"]
    lines = [
        "| หุ้น | งวดล่าสุด | รายได้ TTM | โต YoY | ไตรมาสล่าสุด YoY | อัตรากำไรสุทธิ | P/E | FCF yield | D/E |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for ticker in sorted(data):
        row = data[ticker]
        period = row.get("period_end") or "—"
        if row.get("stale"):
            period += " ⚠️"
        lines.append(
            f"| **{ticker}** | {period} | {_fmt_money(row.get('revenue_ttm'))} | "
            f"{_fmt_pct(row.get('revenue_growth_pct'))} | {_fmt_pct(row.get('revenue_q_yoy_pct'))} | "
            f"{_fmt_num(row.get('net_margin_pct'), '%')} | {_fmt_num(row.get('pe'))} | "
            f"{_fmt_num(row.get('fcf_yield_pct'), '%')} | {_fmt_num(row.get('debt_to_equity'), '', 2)} |"
        )
    lines.extend([
        "",
        "> [!info] อ่านตารางนี้ยังไง",
        "> TTM = รวม 12 เดือนล่าสุด · P/E ใช้กำไรต่อหุ้น TTM · FCF = กระแสเงินสดจากการดำเนินงาน − capex",
        "> ⚠️ = งวดล่าสุดเก่ากว่า 135 วัน (SEC ยังไม่ประมวลผล 10-Q ฉบับล่าสุด) · "
        "ธนาคารไม่มี FCF/D-E เพราะนิยามใช้กับธนาคารไม่ได้",
    ])
    return lines


def _earnings_section(pack: dict[str, Any]) -> list[str]:
    events = pack.get("earnings_upcoming") or []
    if not events:
        return ["*ไม่มีหุ้นใน watchlist ประกาศงบใน 14 วันข้างหน้า (หรือยังไม่มีข้อมูลปฏิทิน)*"]
    lines = ["| วันที่ | หุ้น | EPS ที่คาด (เฉลี่ย) | รายได้ที่คาด |", "|---|---|---:|---:|"]
    for event in events:
        eps = event.get("eps_avg")
        lines.append(
            f"| {event['date']} | **{event['ticker']}** | "
            f"{f'{eps:.2f}' if isinstance(eps, (int, float)) else '—'} | "
            f"{_fmt_money(event.get('revenue_avg'))} |"
        )
    return lines


def _previous_pack(current_as_of: str) -> dict[str, Any] | None:
    """Find the previous context pack, to compare what changed since the last brief.

    This is what makes the briefs build on each other: one issue is a still picture,
    but comparing with the previous one shows *the change*, which carries more information.
    """
    files = sorted((config.DERIVED / "context").glob("*.json"))
    earlier = [f for f in files if f.stem < current_as_of]
    if not earlier:
        return None
    return json.loads(earlier[-1].read_text(encoding="utf-8"))


def _changes_section(pack: dict[str, Any], previous: dict[str, Any] | None) -> list[str]:
    """Summary of regime changes since the last brief"""
    if previous is None:
        return [
            "> [!info] นี่คือ brief ฉบับแรก",
            "> ยังไม่มีอะไรให้เทียบ — ฉบับหน้าจะเริ่มแสดงว่าอะไรเปลี่ยนไปบ้าง",
        ]

    lines = [f"เทียบกับ brief วันที่ **{previous.get('as_of', '?')}**", ""]
    notable: list[str] = []

    # Volatility regime
    now_vix = pack.get("volatility", {})
    old_vix = previous.get("volatility", {})
    if now_vix.get("regime") and old_vix.get("regime") and now_vix["regime"] != old_vix["regime"]:
        notable.append(
            f"⚠️ **ระบอบความผันผวนเปลี่ยน**: {old_vix['regime']} → **{now_vix['regime']}** "
            f"(VIX {old_vix.get('last')} → {now_vix.get('last')})"
        )

    # Market breadth
    now_breadth = pack.get("breadth", {}).get("pct")
    old_breadth = previous.get("breadth", {}).get("pct")
    if now_breadth is not None and old_breadth is not None:
        delta = now_breadth - old_breadth
        if abs(delta) >= 10:
            direction = "ดีขึ้น" if delta > 0 else "แย่ลง"
            notable.append(
                f"📊 **Breadth {direction}มาก**: {old_breadth}% → {now_breadth}% "
                f"ของหุ้นอยู่เหนือ MA200 ({delta:+.1f} จุด)"
            )

    # Stocks with unusually large moves
    for group in ("watchlist", "sectors"):
        for ticker, data in pack.get(group, {}).items():
            old = previous.get(group, {}).get(ticker, {})
            new_price, old_price = data.get("last"), old.get("last")
            if not new_price or not old_price:
                continue
            move = (new_price / old_price - 1.0) * 100
            if abs(move) >= 7:
                notable.append(f"🔥 **{ticker}** ขยับ {move:+.1f}% ตั้งแต่ brief ที่แล้ว")

    if notable:
        lines.extend(notable)
    else:
        lines.append("ไม่มีการเปลี่ยนแปลงเชิงระบอบที่มีนัยสำคัญ")
    return lines


def render(pack: dict[str, Any]) -> str:
    """Build the Markdown body of the brief"""
    as_of = pack["as_of"]
    previous = _previous_pack(as_of)
    local_now = datetime.now(tz=config.LOCAL_TZ).strftime("%Y-%m-%d %H:%M")

    breadth = pack.get("breadth", {})
    vol = pack.get("volatility", {})

    out: list[str] = [
        "---",
        f"title: Situation Brief {as_of}",
        f"date: {as_of}",
        "tags: [brief, situation, market]",
        f"generated_at: {pack.get('generated_at', '')}",
        # Interpretation moved to the weekly plan (Journal/Plans); the daily brief is facts only
        "analysis: weekly-plan",
        "---",
        "",
        f"# 📊 Situation Brief — {as_of}",
        "",
        f"*สร้างเมื่อ {local_now} (เวลาไทย) · ข้อมูลราคาถึง {as_of}*",
        "",
        "> [!abstract] ส่วนนี้คำนวณด้วยโค้ดล้วน ไม่ใช้ token",
        "> ตัวเลขทุกตัวตรวจสอบย้อนกลับได้จาก `data/raw/` — "
        "ส่วนการตีความอยู่ท้ายไฟล์",
        "",
        "---",
        "",
        "## 🌡️ สภาพตลาดโดยรวม",
        "",
    ]

    # Quick status box
    regime_note = f"**{vol.get('regime', '?')}**" if vol else "ไม่มีข้อมูล"
    out.extend([
        "| ตัวชี้วัด | ค่า | ความหมาย |",
        "|---|---|---|",
        f"| VIX | {vol.get('last', '—')} (percentile {vol.get('percentile_1y', '—')}%) | {regime_note} |",
        f"| Breadth | {breadth.get('pct', '—')}% "
        f"({breadth.get('above_ma200', '—')}/{breadth.get('total', '—')}) | หุ้นที่อยู่เหนือ MA200 |",
        "",
    ])

    if breadth.get("pct") is not None:
        pct = breadth["pct"]
        if pct >= 70:
            out.append("> [!success] Breadth แข็งแรง — การขึ้นมีฐานกว้าง ไม่ได้มาจากหุ้นไม่กี่ตัว")
        elif pct <= 40:
            out.append("> [!warning] Breadth อ่อนแอ — ระวังดัชนีขึ้นเพราะหุ้นใหญ่ไม่กี่ตัวพยุงไว้")
        out.append("")

    out.extend(["---", "", "## 📈 ดัชนีและสินทรัพย์หลัก", ""])
    out.extend(_returns_table(pack.get("benchmarks", {})))

    out.extend(["", "---", "", "## 🏭 หมวดอุตสาหกรรม", ""])
    out.extend(_returns_table(pack.get("sectors", {})))

    out.extend(["", "---", "", "## 👀 Watchlist", ""])
    out.extend(_returns_table(pack.get("watchlist", {}), label_key=None))
    out.extend([
        "",
        "> [!note] mom 12-1 และ vol 60d คำนวณด้วยโค้ด — ยังไม่ผ่านการพิสูจน์ว่าทำนายอะไรได้",
        "> mom 12-1 = ผลตอบแทน 12 เดือนที่ข้ามเดือนล่าสุด · vol 60d = ความผันผวนรายปี",
    ])

    out.extend(["", "---", "", "## 🧾 งบการเงินล่าสุด (SEC XBRL)", ""])
    out.extend(_fundamentals_section(pack))

    out.extend(["", "---", "", "## 📅 ประกาศงบ 14 วันข้างหน้า", ""])
    out.extend(_earnings_section(pack))

    # --- What changed ---
    out.extend(["", "---", "", "## 🔄 เปลี่ยนแปลงตั้งแต่ brief ที่แล้ว", ""])
    out.extend(_changes_section(pack, previous))

    # --- baseline ---
    out.extend(["", "---", "", "## 🎯 Baseline ที่รันคู่ขนาน", ""])
    ledger_block = pack.get("ledger", {})
    if ledger_block.get("available"):
        out.extend([
            "| กลยุทธ์ | NAV | 1m | 3m | 1y |",
            "|---|---:|---:|---:|---:|",
        ])
        for name, data in ledger_block.get("strategies", {}).items():
            rets = data.get("returns_pct", {})
            out.append(
                f"| `{name}` | {data.get('nav', '—'):,.0f} | {_fmt_pct(rets.get('1m'))} | "
                f"{_fmt_pct(rets.get('3m'))} | {_fmt_pct(rets.get('1y'))} |"
            )
        out.extend([
            "",
            "> [!note] เส้นเหล่านี้คือสิ่งที่กลยุทธ์ของคุณต้องเอาชนะ",
            "> ช่วงก่อนวันที่เริ่มเก็บข้อมูลเองเป็น **backtest** ไม่ใช่ track record จริง",
        ])
    else:
        out.append("*ยังไม่มีข้อมูล ledger — รัน `uv run finbg ledger`*")

    # --- News ---
    out.extend(["", "---", "", "## 📰 ข่าวที่เก็บได้", ""])
    news = pack.get("news", {})
    if news.get("available"):
        by_type = news.get("by_source_type", {})
        out.append(
            f"รวม **{news['total_articles']}** รายการ จาก {len(news.get('by_source', {}))} แหล่ง"
            f" · {len(news.get('files_used', []))} วันล่าสุด"
        )
        out.append("")
        if by_type:
            out.append("| ชนิดแหล่ง | จำนวน | ความหมาย |")
            out.append("|---|---:|---|")
            meaning = {
                "primary": "เอกสารต้นทาง (SEC filing, ประกาศธนาคารกลาง) — ข้อเท็จจริงตามกฎหมาย",
                "outlet": "สื่อรายงานเอง",
                "aggregator": "รวบรวมจากหลายที่",
            }
            for source_type in ("primary", "outlet", "aggregator"):
                if source_type in by_type:
                    out.append(f"| `{source_type}` | {by_type[source_type]} | {meaning[source_type]} |")
            out.append("")

        by_source = news.get("by_source", {})
        if by_source:
            out.append("**แหล่ง:** " + " · ".join(
                f"{name} ({count})" for name, count in
                sorted(by_source.items(), key=lambda kv: -kv[1])
            ))
            out.append("")

        mentions = news.get("ticker_mentions", {})
        if mentions:
            out.append("**หุ้นที่ถูกพูดถึง** (ยืนยันจากพาดหัวแล้ว): " + " · ".join(
                f"{ticker} ({count})" for ticker, count in list(mentions.items())[:10]
            ))
            out.append("")

        headlines = news.get("headlines", [])
        if headlines:
            labels_by_type = {
                "primary": "### 📜 แหล่งปฐมภูมิ (อ่านก่อน)",
                "outlet": "### 📰 สื่อ",
                "aggregator": "### 🌐 Aggregator",
            }
            current_type = None
            for item in headlines:
                if item["source_type"] != current_type:
                    current_type = item["source_type"]
                    out.append("")
                    out.append(labels_by_type.get(current_type, f"### {current_type}"))
                    out.append("")
                tickers = f" `{' '.join(item['tickers'])}`" if item.get("tickers") else ""
                out.append(
                    f"- [{item['title']}]({item['url']}){tickers} "
                    f"— *{item['publisher'] or item['source']}*"
                )
            out.append("")
    else:
        out.append("*ยังไม่มีข่าวที่เก็บไว้ — รัน `uv run finbg news`*")

    # --- journal ---
    journal = pack.get("journal", {})
    out.extend(["", "---", "", "## 📓 สิ่งที่ค้างอยู่", ""])

    open_preds = journal.get("open_predictions", [])
    out.append(f"**คำทำนายที่ยังไม่รู้ผล:** {len(open_preds)} ข้อ "
               f"(รู้ผลแล้ว {journal.get('resolved_predictions', 0)} ข้อ)")
    if open_preds:
        out.append("")
        for pred in open_preds[:10]:
            confidence = f" · มั่นใจ {pred['confidence']}%" if pred.get("confidence") else ""
            due = f" · ครบกำหนด {pred['resolve_by']}" if pred.get("resolve_by") else ""
            out.append(f"- [[{pred['file']}|{pred['title']}]]{confidence}{due}")

    backlog = journal.get("hypotheses_backlog", [])
    out.append("")
    out.append(f"**สมมติฐานรอทดสอบ:** {len(backlog)} ใบ "
               f"(ทั้งหมด {journal.get('total_hypotheses', 0)} ใบ)")
    if backlog:
        out.append("")
        for hypothesis in backlog[:10]:
            out.append(f"- [[{hypothesis['file']}|{hypothesis['title']}]]")

    # --- Portfolio Manager: plan, performance, alerts, predictions ---
    out.extend(["", "---", ""])
    out.extend(_pm_section(pack.get("pm") or {}))
    out.extend(["", "---", ""])
    out.extend(_opportunities_section(pack.get("opportunities") or {}))
    out.extend([
        "",
        "---",
        "",
        "🔗 [[00 - AI Investing MOC]] · [[10 - Background Track]] · [[09 - Evaluation Playbook]]",
        "",
    ])

    return "\n".join(out)


def _pm_section(pm: dict[str, Any]) -> list[str]:
    """PM portfolio summary on the brief page; full details are on the plan page"""
    from ..pm import plans as plans_module

    lines = ["## 🧭 แผนรายสัปดาห์ที่ใช้อยู่", ""]
    plan = pm.get("plan")
    if not plan:
        lines += [
            "*ยังไม่มีแผน — แผนแรกออกอัตโนมัติเช้าวันเสาร์ (หรือสั่งเอง `uv run finbg plan run`)*",
        ]
    else:
        icon = {"risk_on": "🟢", "neutral": "⚪", "risk_off": "🔴"}.get(plan.get("stance"), "⚪")
        satellite = ", ".join(f"{t} {w:.0%}" for t, w in plan["satellite"].items())
        cash = f", เงินสด {plan['cash']:.0%}" if plan.get("cash") else ""
        lines += [
            f"**[[{plan['plan_id']}]]** {icon} `{plan.get('stance')}` · satellite: {satellite}{cash}",
            "",
            f"> {plan.get('thesis', '')[:500]}",
        ]
    lines += ["", *plans_module.status_lines(pm.get("status") or {})]

    alerts = pm.get("alerts_7d") or []
    lines += ["", "### 🚨 แจ้งเตือน 7 วันล่าสุด", ""]
    icon = {"critical": "🔴", "high": "🟠", "medium": "🟡", "info": "⚪"}
    lines += [f"- {icon.get(a['severity'], '·')} {a['at'][:16]} UTC — {a['title']}" for a in alerts] or ["*ไม่มี*"]

    card = pm.get("scorecard") or {}
    lines += ["", "### 🎯 คำทำนายของ PM (ระบบตัดสินเองด้วยราคา)", ""]
    if card.get("n"):
        lines.append(f"ตัดสินแล้ว {card['n']} ข้อ · ถูก {card['hit_rate_pct']}% · มั่นใจเฉลี่ย "
                     f"{card['avg_confidence_pct']}% · Brier {card['brier']} (ต่ำกว่า 0.25 = ดีกว่าเดาสุ่ม)")
    else:
        lines.append("*ยังไม่มีข้อที่ครบกำหนด*")
    return lines


def _opportunities_section(block: dict[str, Any]) -> list[str]:
    lines = ["## 🔭 พอร์ตไอเดีย (สแกนทั้งตลาด)", ""]
    if not block.get("session"):
        return lines + ["*ยังไม่มีผลสแกน — รันเองได้ด้วย `uv run finbg scan`*"]
    names = {"add_to_book": "✅ เข้าพอร์ต", "watch": "👀 จับตา", "pass": "⏭️ ผ่าน"}
    lines.append(f"สแกนล่าสุด: **[[{block['session']}]]**")
    lines.append("")
    for idea in block.get("ideas") or []:
        lines.append(f"- **{idea['ticker']}** {names.get(idea['decision'], idea['decision'])} · `{idea['idea_type']}` · "
                     f"มั่นใจ {idea['confidence']}% — {str(idea.get('what_happened', ''))[:160]}")
    board = block.get("scoreboard") or {}
    ideas, control = board.get("ideas", {}), board.get("control", {})
    if ideas.get("n"):
        diff = board.get("ideas_minus_control")
        lines += ["", f"ผลจริง: ไอเดีย {ideas['n']} ตัว เฉลี่ย {ideas['mean_pct']:+.2f}% เทียบ benchmark · "
                      f"สุ่ม {control.get('n', 0)} ตัว {control.get('mean_pct') or 0:+.2f}%"
                      + (f" · **Claude − สุ่ม {diff['diff_pct']:+.2f} จุด (t {diff['t']})**" if diff else "")]
    else:
        lines += ["", f"*ยังไม่มีไอเดียที่ครบ 20 วันทำการ — {board.get('gate', '')}* · ดู [[scoreboard]]"]
    return lines


def build_brief() -> dict[str, Any]:
    """Build a brief from the latest context pack and write it to Obsidian"""
    from . import context as context_module

    pack = context_module.build()
    if not pack.get("ok"):
        return pack

    storage.ensure_dirs()
    path = config.BRIEFS / f"{pack['as_of']}.md"
    path.write_text(render(pack), encoding="utf-8")

    return {
        "ok": True,
        "as_of": pack["as_of"],
        "brief": str(path.relative_to(config.ROOT)),
        "context": pack.get("path"),
    }
