"""แปลง context pack → Situation Brief ที่เปิดอ่านใน Obsidian ได้

นี่คือ "ชั้นที่ 1.5": จัดข้อเท็จจริงให้อ่านง่ายสำหรับคน **และ** สำหรับ LLM
ส่วนการตีความเว้นไว้ให้ชั้นที่ 2 เติม (ดูหัวข้อ "การวิเคราะห์" ท้ายไฟล์)

ทำไมต้องแยกไฟล์ brief ออกจาก context pack (JSON):
- JSON เหมาะให้ LLM อ่าน (โครงสร้างชัด ไม่มีคำฟุ่มเฟือย)
- Markdown เหมาะให้คนอ่านและใช้เป็นบันทึกถาวรใน Obsidian
ทั้งสองมาจากแหล่งเดียวกัน จึงไม่มีทางขัดแย้งกัน
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
    """จัดรูปแบบ % พร้อมเครื่องหมาย — คืน '—' ถ้าไม่มีข้อมูล"""
    if value is None:
        return "—".rjust(width)
    return f"{value:+.2f}%".rjust(width)


def _signal(value: Any) -> str:
    """สัญลักษณ์สีบอกทิศทางแบบเร็ว ๆ"""
    if value is None:
        return ARROW_FLAT
    if value > 0.5:
        return ARROW_UP
    if value < -0.5:
        return ARROW_DOWN
    return ARROW_FLAT


def _returns_table(items: dict[str, dict[str, Any]], label_key: str | None = "label") -> list[str]:
    """ตารางผลตอบแทนหลายช่วงเวลา ใช้ซ้ำได้ทั้ง benchmark / sector / watchlist"""
    lines = [
        "| | สินทรัพย์ | ราคา | 1d | 1w | 1m | 3m | 1y | vs MA200 | ตำแหน่ง 52w |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|",
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
            f"{position if position is not None else '—'} |"
        )
    return lines


def _previous_pack(current_as_of: str) -> dict[str, Any] | None:
    """หา context pack ก่อนหน้า เพื่อเทียบว่า 'อะไรเปลี่ยนไปตั้งแต่ brief ที่แล้ว'

    ส่วนนี้คือสิ่งที่ทำให้ brief ทบต้น — อ่านฉบับเดียวได้ภาพนิ่ง
    แต่การเทียบกับฉบับก่อนทำให้เห็น *การเปลี่ยนแปลง* ซึ่งมีข้อมูลมากกว่า
    """
    files = sorted((config.DERIVED / "context").glob("*.json"))
    earlier = [f for f in files if f.stem < current_as_of]
    if not earlier:
        return None
    return json.loads(earlier[-1].read_text(encoding="utf-8"))


def _changes_section(pack: dict[str, Any], previous: dict[str, Any] | None) -> list[str]:
    """สรุปการเปลี่ยนแปลงเชิงระบอบ (regime) ตั้งแต่ brief ที่แล้ว"""
    if previous is None:
        return [
            "> [!info] นี่คือ brief ฉบับแรก",
            "> ยังไม่มีอะไรให้เทียบ — ฉบับหน้าจะเริ่มแสดงว่าอะไรเปลี่ยนไปบ้าง",
        ]

    lines = [f"เทียบกับ brief วันที่ **{previous.get('as_of', '?')}**", ""]
    notable: list[str] = []

    # ระบอบความผันผวน
    now_vix = pack.get("volatility", {})
    old_vix = previous.get("volatility", {})
    if now_vix.get("regime") and old_vix.get("regime") and now_vix["regime"] != old_vix["regime"]:
        notable.append(
            f"⚠️ **ระบอบความผันผวนเปลี่ยน**: {old_vix['regime']} → **{now_vix['regime']}** "
            f"(VIX {old_vix.get('last')} → {now_vix.get('last')})"
        )

    # ความกว้างของตลาด
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

    # หุ้นที่ขยับแรงผิดปกติ
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
    """สร้างเนื้อหา Markdown ของ brief"""
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
        "analysis_status: pending",   # เปลี่ยนเป็น done เมื่อชั้นที่ 2 เติมแล้ว
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

    # กล่องสรุปสถานะเร็ว
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

    # --- อะไรเปลี่ยนไป ---
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

    # --- ข่าว ---
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

    # --- ช่องให้ชั้นที่ 2 เติม ---
    out.extend([
        "",
        "---",
        "",
        "## 🧠 การวิเคราะห์",
        "",
        "> [!tip] ส่วนนี้เว้นไว้ให้เติมด้วย Claude",
        "> เปิด Claude Code แล้วสั่ง:",
        "> ```",
        f"> อ่าน data/derived/context/{as_of}.json แล้วเติมหัวข้อ 'การวิเคราะห์'",
        f"> ใน Journal/Briefs/{as_of}.md",
        "> ```",
        "> แล้วเปลี่ยน `analysis_status` ใน frontmatter เป็น `done`",
        "",
        "<!-- ANALYSIS:START -->",
        "*(ยังไม่ได้วิเคราะห์)*",
        "<!-- ANALYSIS:END -->",
        "",
        "---",
        "",
        "## ✍️ คำทำนายประจำสัปดาห์",
        "",
        "> [!important] อย่าข้ามขั้นนี้",
        "> การเขียนคำทำนาย **ก่อน** รู้ผล คือการประเมินแบบเดียวที่ไม่มีทางปนเปื้อน",
        "> สร้างไฟล์ใหม่ด้วย `uv run finbg predict \"หัวข้อ\"`",
        "",
        "---",
        "",
        "🔗 [[00 - AI Investing MOC]] · [[10 - Background Track]] · [[09 - Evaluation Playbook]]",
        "",
    ])

    return "\n".join(out)


def build_brief() -> dict[str, Any]:
    """สร้าง brief จาก context pack ล่าสุด แล้วเขียนลง Obsidian"""
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
