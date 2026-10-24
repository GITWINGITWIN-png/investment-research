"""สร้าง "context pack" — สรุปสถานการณ์อดีต→ปัจจุบันแบบคำนวณล้วน

**ไฟล์นี้ไม่เรียก LLM เลย** และนั่นคือเจตนา

แนวคิด: แยกงานเป็นสองชั้น
  ชั้นที่ 1 (ไฟล์นี้)  — รวบรวมข้อเท็จจริง  → โค้ดล้วน ฟรี ทำซ้ำได้เป๊ะ
  ชั้นที่ 2 (Claude)   — ตีความ + เชื่อมโยง → ใช้ token เฉพาะตรงที่มนุษย์/LLM เก่งกว่าโค้ด

ถ้าให้ LLM ไปนั่งคำนวณ % เปลี่ยนแปลงเอง = เผา token กับงานที่ pandas ทำได้ฟรี
และยังเสี่ยงคำนวณผิดด้วย
"""

from __future__ import annotations

import json
from typing import Any

import pandas as pd

from .. import config, ledger, storage
from ..collect import calendar as calendar_module
from ..collect import fundamentals as fundamentals_module

# ช่วงเวลาที่ดูย้อนหลัง (วันทำการโดยประมาณ)
HORIZONS: dict[str, int] = {
    "1d": 1,
    "1w": 5,
    "1m": 21,
    "3m": 63,
    "6m": 126,
    "1y": 252,
}


def _pct_change_over(series: pd.Series, periods: int) -> float | None:
    """% เปลี่ยนแปลงย้อนหลัง N วันทำการ — คืน None ถ้าข้อมูลไม่พอ"""
    clean = series.dropna()
    if len(clean) <= periods:
        return None
    return round((clean.iloc[-1] / clean.iloc[-1 - periods] - 1.0) * 100, 2)


def _ticker_snapshot(series: pd.Series) -> dict[str, Any]:
    """สรุปสถานะของ 1 ticker: ราคาล่าสุด, ผลตอบแทนหลายช่วง, ตำแหน่งเทียบ 52 สัปดาห์"""
    clean = series.dropna()
    if clean.empty:
        return {}

    last = float(clean.iloc[-1])
    snapshot: dict[str, Any] = {
        "last": round(last, 2),
        "as_of": str(clean.index[-1].date()),
        "returns_pct": {
            label: _pct_change_over(clean, periods) for label, periods in HORIZONS.items()
        },
    }

    # ตำแหน่งเทียบช่วง 52 สัปดาห์ บอก "ของแพงหรือถูก" ได้เร็วกว่าดูราคาเปล่า ๆ
    year = clean.tail(252)
    if len(year) > 20:
        high, low = float(year.max()), float(year.min())
        snapshot["week52"] = {
            "high": round(high, 2),
            "low": round(low, 2),
            "pct_from_high": round((last / high - 1.0) * 100, 2),
            # 0 = ต่ำสุดของปี, 100 = สูงสุดของปี
            "position_pct": round((last - low) / (high - low) * 100, 1) if high > low else None,
        }

    # อยู่เหนือหรือใต้เส้นค่าเฉลี่ย 200 วัน — ตัวชี้ trend ที่ง่ายและใช้กันมากที่สุด
    if len(clean) >= 200:
        ma200 = float(clean.tail(200).mean())
        snapshot["vs_ma200_pct"] = round((last / ma200 - 1.0) * 100, 2)

    snapshot.update(_quant_signals(clean))
    return snapshot


def _quant_signals(clean: pd.Series) -> dict[str, Any]:
    """สัญญาณเชิงปริมาณพื้นฐาน — คำนวณด้วยโค้ด ไม่ใช้ token

    - `mom_12_1_pct`: ผลตอบแทน 12 เดือน ข้ามเดือนล่าสุด (นิยามเดียวกับ ledger.mom_12_1)
    - `vol_60d_pct`: ความผันผวนรายปีจากผลตอบแทนรายวัน 60 วัน

    ⚠️ ยังไม่ผ่านการพิสูจน์ใน harness (finlab) ว่ามีพลังทำนายบน universe นี้
    ให้ถือเป็นข้อมูลประกอบ ไม่ใช่สัญญาณที่เชื่อได้ — แผนรายสัปดาห์ถูกบอกเรื่องนี้ไว้ตรง ๆ
    """
    out: dict[str, Any] = {}
    if len(clean) > 252:
        out["mom_12_1_pct"] = round((clean.iloc[-22] / clean.iloc[-253] - 1.0) * 100, 2)
    returns = clean.pct_change().dropna()
    if len(returns) >= 60:
        out["vol_60d_pct"] = round(float(returns.tail(60).std() * (252 ** 0.5) * 100), 1)
    return out


def _breadth(prices: pd.DataFrame, tickers: list[str]) -> dict[str, Any]:
    """ความกว้างของตลาด: กี่ % ของหุ้นอยู่เหนือ MA200

    ตัวเลขนี้บอกสิ่งที่ดัชนีไม่บอก — ดัชนีอาจขึ้นเพราะหุ้นใหญ่ไม่กี่ตัว
    ขณะที่หุ้นส่วนใหญ่กำลังลง (breadth แย่) ซึ่งมักเป็นสัญญาณเตือน
    """
    above = 0
    counted = 0
    for ticker in tickers:
        if ticker not in prices.columns:
            continue
        clean = prices[ticker].dropna()
        if len(clean) < 200:
            continue
        counted += 1
        if clean.iloc[-1] > clean.tail(200).mean():
            above += 1

    if counted == 0:
        return {}
    return {
        "above_ma200": above,
        "total": counted,
        "pct": round(above / counted * 100, 1),
    }


def _vix_regime(prices: pd.DataFrame) -> dict[str, Any]:
    """ระดับ VIX พร้อม percentile ย้อนหลัง 1 ปี

    ค่า VIX เปล่า ๆ ตีความยาก (20 ถือว่าสูงไหม?) แต่ percentile ตอบได้ทันที
    """
    if "^VIX" not in prices.columns:
        return {}
    clean = prices["^VIX"].dropna()
    if clean.empty:
        return {}

    last = float(clean.iloc[-1])
    year = clean.tail(252)
    percentile = round((year < last).mean() * 100, 1) if len(year) > 20 else None

    if last < 15:
        regime = "สงบ"
    elif last < 20:
        regime = "ปกติ"
    elif last < 30:
        regime = "ตึงตัว"
    else:
        regime = "ตื่นตระหนก"

    return {"last": round(last, 2), "percentile_1y": percentile, "regime": regime}


def _news_digest(days: int = 3) -> dict[str, Any]:
    """สรุปข่าวที่เก็บได้ล่าสุด — นับตามคำค้นและแหล่ง พร้อมพาดหัวตัวอย่าง

    ไม่พยายามตีความ ณ ตรงนี้ — แค่จัดระเบียบให้ชั้นที่ 2 อ่านง่าย
    """
    files = sorted(config.RAW_NEWS.glob("*.jsonl"))[-days:]
    if not files:
        return {"available": False}

    records: list[dict[str, Any]] = []
    for path in files:
        with path.open(encoding="utf-8") as fh:
            for line in fh:
                try:
                    records.append(json.loads(line))
                except json.JSONDecodeError:
                    continue

    if not records:
        return {"available": False}

    df = pd.DataFrame(records)
    by_source = df["source"].value_counts().to_dict() if "source" in df else {}
    by_type = df["source_type"].value_counts().to_dict() if "source_type" in df else {}
    by_publisher = df["publisher"].value_counts().head(10).to_dict() if "publisher" in df else {}

    # จัดพาดหัวโดยแยก primary ออกมาก่อน — เอกสารต้นทาง (SEC filing, ประกาศ Fed)
    # เป็นข้อเท็จจริงตามกฎหมาย ไม่ใช่การตีความ จึงควรอ่านก่อนข่าวทั่วไป
    headlines: list[dict[str, str]] = []
    if "source_type" in df and "title" in df:
        # ภายในแต่ละชนิด ให้รายการที่ผูกกับ watchlist ขึ้นก่อน แล้วค่อยเรียงตามวันที่
        # เหตุผล: Fed ปล่อยประกาศเยอะ (รวมเรื่องธนาคารเล็ก ๆ ที่ไม่เกี่ยวกับเรา)
        # ถ้าเรียงตามวันที่อย่างเดียว SEC filing ของหุ้นที่เราถือจะถูกเบียดตกไป
        df = df.assign(_relevant=df["tickers"].map(lambda t: 1 if t else 0))
        for source_type in ("primary", "outlet", "aggregator"):
            group = df[df["source_type"] == source_type].sort_values(
                ["_relevant", "published_at"], ascending=[False, False]
            )
            for _, row in group.head(12).iterrows():
                headlines.append({
                    "source_type": source_type,
                    "source": str(row.get("source", "")),
                    "title": str(row.get("title", ""))[:220],
                    "publisher": str(row.get("publisher", "")),
                    "published_at": str(row.get("published_at", ""))[:19],
                    "tickers": list(row.get("tickers") or []),
                    "url": str(row.get("url", "")),
                })

    # นับเฉพาะ ticker ที่ยืนยันแล้ว — ไม่นับ tickers_hint ที่ยังไม่ยืนยัน
    ticker_counts: dict[str, int] = {}
    for record in records:
        for ticker in record.get("tickers") or []:
            ticker_counts[ticker] = ticker_counts.get(ticker, 0) + 1

    return {
        "available": True,
        "files_used": [f.name for f in files],
        "total_articles": len(df),
        "by_source": by_source,
        "by_source_type": by_type,
        "top_publishers": by_publisher,
        "ticker_mentions": dict(sorted(ticker_counts.items(), key=lambda kv: -kv[1])),
        "headlines": headlines,
    }


def _ledger_block() -> dict[str, Any]:
    """สถานะ baseline ที่รันคู่ขนาน — เส้นเปรียบเทียบสำหรับทุกความคิดเห็น"""
    nav = ledger.load()
    if nav.empty:
        return {"available": False}

    block: dict[str, Any] = {"available": True, "as_of": str(nav.index[-1].date()), "strategies": {}}
    for column in nav.columns:
        series = nav[column].dropna()
        if series.empty:
            continue
        block["strategies"][column] = {
            "nav": round(float(series.iloc[-1]), 2),
            "returns_pct": {
                label: _pct_change_over(series, periods) for label, periods in HORIZONS.items()
            },
        }
    return block


def _journal_block() -> dict[str, Any]:
    """คำทำนายและสมมติฐานที่ยังค้างอยู่ — เพื่อให้ brief เตือนเราเรื่องที่เคยคิดไว้"""
    def scan(folder, status_field: str) -> list[dict[str, str]]:
        items: list[dict[str, str]] = []
        for path in sorted(folder.glob("*.md")):
            text = path.read_text(encoding="utf-8")
            meta: dict[str, str] = {}
            if text.startswith("---"):
                _, _, rest = text.partition("---")
                front, _, _ = rest.partition("---")
                for line in front.strip().splitlines():
                    if ":" in line:
                        key, _, value = line.partition(":")
                        meta[key.strip()] = value.strip()
            items.append({
                "file": path.stem,
                "title": next(
                    (l.lstrip("# ").strip() for l in text.splitlines() if l.startswith("# ")),
                    path.stem,
                ),
                status_field: meta.get(status_field, ""),
                "resolve_by": meta.get("resolve_by", ""),
                "confidence": meta.get("confidence", ""),
            })
        return items

    predictions = scan(config.PREDICTIONS, "resolved")
    hypotheses = scan(config.HYPOTHESES, "status")

    return {
        "open_predictions": [p for p in predictions if p.get("resolved", "") in ("", "null", "false")],
        "resolved_predictions": len(predictions) - len(
            [p for p in predictions if p.get("resolved", "") in ("", "null", "false")]
        ),
        "hypotheses_backlog": [h for h in hypotheses if h.get("status", "") == "backlog"],
        "total_hypotheses": len(hypotheses),
    }


def _pm_block() -> dict[str, Any]:
    """สถานะ Portfolio Manager: แผนที่ใช้อยู่ ผลงานเทียบ control แจ้งเตือนล่าสุด คำทำนาย"""
    from datetime import timedelta

    from ..pm import plans as plans_module
    from ..pm import portfolio as pm_portfolio
    from ..pm import predictions as pm_predictions

    record = plans_module.latest()
    block: dict[str, Any] = {"status": pm_portfolio.build(), "scorecard": pm_predictions.scorecard(limit=5)}
    if record:
        block["plan"] = {
            "plan_id": record["plan_id"],
            "kind": record["kind"],
            "created_at": record["created_at"],
            "stance": record["plan"].get("regime", {}).get("stance"),
            "thesis": record["plan"].get("thesis", ""),
            "satellite": record["targets"]["satellite"],
            "cash": record["targets"]["cash"],
        }
    cutoff = (storage.utc_now() - timedelta(days=7)).isoformat()
    alerts = []
    for path in sorted(config.ALERTS.glob("alerts-*.jsonl"))[-2:]:
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                alert = json.loads(line)
            except json.JSONDecodeError:
                continue
            if alert.get("at", "") >= cutoff:
                alerts.append({k: alert.get(k) for k in ("at", "severity", "title", "detail")})
    block["alerts_7d"] = alerts[-15:]
    return block


def _opportunities_block() -> dict[str, Any]:
    """ผลสแกนทั้งตลาดครั้งล่าสุด + ตารางผลจริงของพอร์ตไอเดียเทียบกลุ่มควบคุม"""
    from ..scan import book as scan_book

    ideas = [json.loads(l) for l in scan_book.IDEAS.read_text(encoding="utf-8").splitlines() if l.strip()] \
        if scan_book.IDEAS.exists() else []
    voided = scan_book.voided_runs()
    ideas = [i for i in ideas if i["created_at"] not in voided]       # รอบที่ประกาศยกเลิกไม่แสดง
    latest_run = max((i["created_at"] for i in ideas), default=None)
    latest = [i for i in ideas if i["created_at"] == latest_run]
    return {
        "session": latest[0]["session"] if latest else None,
        "ideas": [{k: i.get(k) for k in ("ticker", "decision", "idea_type", "confidence", "what_happened")}
                  for i in latest],
        "scoreboard": scan_book.scoreboard(),
    }


def _fundamentals_block() -> dict[str, Any]:
    """ตัวเลขงบล่าสุดต่อบริษัท (SEC XBRL) — ราคาที่ใช้คิด P/E คือราคาปิดล่าสุด"""
    closes = storage.price_matrix("close")
    if closes.empty:
        return {}
    last = closes.ffill().iloc[-1].dropna().to_dict()
    return fundamentals_module.summary(closes=last)


def build() -> dict[str, Any]:
    """ประกอบ context pack ทั้งหมด แล้วบันทึกเป็น JSON

    ผลลัพธ์ไฟล์นี้คือ *อินพุตเดียว* ที่ชั้นตีความต้องใช้ —
    ทำให้ควบคุมได้ว่า token จะถูกใช้กับข้อมูลเท่าไหร่
    """
    storage.ensure_dirs()
    prices = storage.price_matrix("adj_close")
    if prices.empty:
        return {"ok": False, "reason": "ยังไม่มีข้อมูลราคา"}

    as_of = str(prices.index[-1].date())

    pack: dict[str, Any] = {
        "ok": True,
        "as_of": as_of,
        "generated_at": storage.utc_stamp(),
        "benchmarks": {
            ticker: {"label": label, **_ticker_snapshot(prices[ticker])}
            for ticker, label in config.BENCHMARKS.items()
            if ticker in prices.columns
        },
        "sectors": {
            ticker: {"label": label, **_ticker_snapshot(prices[ticker])}
            for ticker, label in config.SECTORS.items()
            if ticker in prices.columns
        },
        "watchlist": {
            ticker: _ticker_snapshot(prices[ticker])
            for ticker in config.WATCHLIST
            if ticker in prices.columns
        },
        "breadth": _breadth(prices, [*config.SECTORS, *config.WATCHLIST]),
        "volatility": _vix_regime(prices),
        "ledger": _ledger_block(),
        "news": _news_digest(),
        "journal": _journal_block(),
        "fundamentals": _fundamentals_block(),
        "earnings_upcoming": calendar_module.upcoming(days=14),
        "pm": _pm_block(),
        "opportunities": _opportunities_block(),
    }

    out_dir = config.DERIVED / "context"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{as_of}.json"
    out_path.write_text(json.dumps(pack, ensure_ascii=False, indent=2), encoding="utf-8")

    pack["path"] = str(out_path.relative_to(config.ROOT))
    return pack


def latest() -> dict[str, Any] | None:
    """อ่าน context pack ล่าสุดที่สร้างไว้"""
    files = sorted((config.DERIVED / "context").glob("*.json"))
    if not files:
        return None
    return json.loads(files[-1].read_text(encoding="utf-8"))
