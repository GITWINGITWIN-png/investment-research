"""เอกสารที่ยื่นต่อ SEC — แหล่งปฐมภูมิที่มีค่าที่สุดในทั้งระบบ

## ทำไมสำคัญกว่าข่าว

ข่าวคือ **การตีความ** ของนักข่าว ส่วน filing คือ **ข้อเท็จจริงที่บริษัทมีหน้าที่แจ้งตามกฎหมาย**

โดยเฉพาะ **8-K** = "current report" ที่บริษัทต้องยื่นเมื่อเกิดเหตุการณ์สำคัญ
(เปลี่ยนผู้บริหาร ควบรวม ผิดนัดชำระหนี้ ฯลฯ) ภายในไม่กี่วันทำการ
→ **นิยามของ "ข่าวที่มีนัยสำคัญ" ตามกฎหมาย** ไม่ใช่ตามที่นักข่าวเลือก

## ข้อกำหนดของ SEC ที่ต้องทำตาม

SEC บังคับให้ทุก request ระบุ User-Agent เป็น **"ชื่อ อีเมล"** ที่ติดต่อได้จริง
ถ้าไม่ใส่จะโดน HTTP 403 ทันที (ทดสอบแล้ว) — ไม่ใช่มารยาท แต่เป็นเงื่อนไขการใช้งาน
"""

from __future__ import annotations

import json
import time
from typing import Any

import requests

from ... import config
from . import normalize

SOURCE = "sec"
SOURCE_TYPE = "primary"

SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik}.json"
TICKER_MAP_URL = "https://www.sec.gov/files/company_tickers.json"
ARCHIVE_URL = "https://www.sec.gov/Archives/edgar/data/{cik_int}/{accession_nodash}/{doc}"


def _headers() -> dict[str, str]:
    """SEC ต้องการ User-Agent ที่มีอีเมลติดต่อได้ ไม่งั้น 403"""
    return {"User-Agent": config.SEC_USER_AGENT, "Accept-Encoding": "gzip, deflate"}


def _ticker_to_cik() -> dict[str, str]:
    """แผนที่ ticker → CIK พร้อม cache ลงดิสก์

    รายการนี้เปลี่ยนน้อยมาก จึง cache ไว้ 30 วัน — ไม่ต้องโหลดใหม่ทุกวัน
    """
    cache = config.DERIVED / "sec_ticker_cik.json"
    if cache.exists() and (time.time() - cache.stat().st_mtime) < 30 * 86400:
        return json.loads(cache.read_text(encoding="utf-8"))

    try:
        response = requests.get(TICKER_MAP_URL, headers=_headers(), timeout=30)
        response.raise_for_status()
        mapping = {
            entry["ticker"].upper(): f"{int(entry['cik_str']):010d}"
            for entry in response.json().values()
        }
    except (requests.RequestException, ValueError, KeyError) as exc:
        print(f"  ⚠️  sec: โหลดแผนที่ ticker→CIK ไม่ได้ ({type(exc).__name__})")
        return json.loads(cache.read_text(encoding="utf-8")) if cache.exists() else {}

    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps(mapping), encoding="utf-8")
    return mapping


def fetch(tickers: list[str] | None = None, lookback_days: int = 7) -> list[dict[str, Any]]:
    """ดึง filing ล่าสุดของ watchlist — เฉพาะฟอร์มที่บอกเหตุการณ์จริง

    ตัด Form 3/4/5 (การซื้อขายของผู้บริหาร) ออก เพราะมีปริมาณมากและ
    ส่วนใหญ่เป็นรายการตามแผนที่ตั้งไว้ล่วงหน้า ไม่ใช่สัญญาณ
    """
    from datetime import date, timedelta

    targets = tickers or config.WATCHLIST
    cik_map = _ticker_to_cik()
    if not cik_map:
        return []

    cutoff = (date.today() - timedelta(days=lookback_days)).isoformat()
    records: list[dict[str, Any]] = []

    for ticker in targets:
        cik = cik_map.get(ticker.upper())
        if not cik:
            continue

        try:
            response = requests.get(
                SUBMISSIONS_URL.format(cik=cik), headers=_headers(), timeout=30
            )
            response.raise_for_status()
            recent = response.json()["filings"]["recent"]
        except (requests.RequestException, ValueError, KeyError) as exc:
            print(f"  ⚠️  sec/{ticker}: {type(exc).__name__}")
            continue

        for i, form in enumerate(recent.get("form", [])):
            filing_date = recent["filingDate"][i]
            if filing_date < cutoff:
                break                                  # เรียงใหม่→เก่า เจอเก่าแล้วหยุดได้
            if form not in config.SEC_FORMS:
                continue

            accession = recent["accessionNumber"][i]
            document = recent.get("primaryDocument", [""] * (i + 1))[i]
            url = ARCHIVE_URL.format(
                cik_int=int(cik),
                accession_nodash=accession.replace("-", ""),
                doc=document,
            )

            description = recent.get("primaryDocDescription", [""] * (i + 1))[i] or form
            records.append(
                normalize(
                    url=url,
                    title=f"[{form}] {ticker} — {description}",
                    published_at=f"{filing_date}T00:00:00+00:00",
                    source=SOURCE,
                    source_type=SOURCE_TYPE,
                    publisher="SEC EDGAR",
                    tickers=[ticker],
                    extra={"form": form, "accession": accession, "filing_date": filing_date},
                )
            )

        # SEC ขอไม่เกิน 10 req/วินาที — เว้นไว้เผื่อ
        time.sleep(config.SEC_MIN_INTERVAL_SEC)

    return records
