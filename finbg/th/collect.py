"""ขั้น 1 ของแทร็กไทย — เก็บรายชื่อ ราคา ข่าว วันละครั้ง (0 token · scripts/morning.sh เรียก)

แต่ละขั้นล้มได้อิสระ (หลักเดียวกับ collect/news.py): Yahoo ล่มก็ยังได้ข่าว · feed หนึ่งพังก็ยังได้ราคา
ลำดับสำคัญอย่างเดียว: รายชื่อต้องมาก่อน เพราะราคาและการติดป้ายข่าวใช้รายชื่อ
"""

from __future__ import annotations

import collections
import json
from datetime import timedelta
from typing import Any, Callable

import pandas as pd

from .. import config, storage
from . import news, prices, universe


def _guarded(step: Callable[..., dict[str, Any]], *args: Any) -> dict[str, Any]:
    try:
        return step(*args)
    except Exception as exc:                              # noqa: BLE001 — ขั้นหนึ่งพังต้องไม่หยุดขั้นอื่น
        return {"ok": False, "reason": f"{type(exc).__name__}: {exc}"[:200]}


def run() -> dict[str, Any]:
    result: dict[str, Any] = {"universe": _guarded(universe.snapshot)}
    symbols = universe.ever_listed()                      # ใช้ snapshot เก่าได้ ถ้าวันนี้ screener ล่ม
    if not symbols:
        reason = {"ok": False, "reason": "ยังไม่มีรายชื่อหุ้นในคลังเลย"}
        return {**result, "prices": reason, "news": reason, "ok": False}
    result["prices"] = _guarded(prices.snapshot, symbols)
    result["news"] = _guarded(news.collect, symbols)
    result["ok"] = all(step.get("ok") for step in result.values())
    return result


def status(days: int = 7) -> dict[str, Any]:
    """คลังมีอะไรแล้วบ้าง — ใช้ตอบว่า "ข้อมูลพอจะเริ่มขั้นต่อไปหรือยัง" (เช่น ข่าวครอบคลุมหุ้นกี่ตัว)"""
    out: dict[str, Any] = {}
    snaps = sorted(config.RAW_TH_UNIVERSE.glob("*.parquet"))
    out["universe"] = {"snapshots": len(snaps), "latest": snaps[-1].stem if snaps else None,
                       "common_stocks_now": len(universe.latest()), "ever_listed": len(universe.ever_listed())}

    table = prices.read_all()
    out["prices"] = {"snapshots": len(list(config.RAW_TH_PRICES.glob("*.parquet"))),
                     "tickers": int(table["ticker"].nunique()) if not table.empty else 0,
                     "last_bar": str(table["date"].max().date()) if not table.empty else None}

    since = storage.utc_now() - timedelta(days=days)
    seen: dict[str, dict[str, Any]] = {}
    for path in sorted(config.RAW_TH_NEWS.glob("*.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            record = json.loads(line)
            seen.setdefault(record["url"], record)         # ข่าวเดียวกันอยู่หลายไฟล์ได้ (ดึงซ้อนกัน) — นับครั้งเดียว
    recent = [r for r in seen.values()
              if r.get("published_at") and pd.Timestamp(r["published_at"]) >= pd.Timestamp(since)]
    mentions = collections.Counter(t for r in recent for t in r["tickers"])
    out["news"] = {"unique_items": len(seen), f"last_{days}d": len(recent),
                   f"last_{days}d_tagged": sum(1 for r in recent if r["tickers"]),
                   f"stocks_mentioned_{days}d": len(mentions), "top": mentions.most_common(10),
                   "by_publisher": dict(collections.Counter(r["publisher"] for r in recent))}
    return out
