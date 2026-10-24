"""งบการเงินจาก SEC XBRL (companyfacts) — ข้อมูล point-in-time โดยธรรมชาติ

## ทำไมแหล่งนี้ดีกว่าที่คิด

ทุกตัวเลขใน companyfacts มาพร้อม:
  `end`    = วันสิ้นงวด (event_date)
  `filed`  = วันที่ยื่นต่อ SEC (knowledge_date) ← หัวใจของ PIT
  `accn`   = เลขที่เอกสาร (ย้อนไปดูต้นฉบับได้)
และเก็บ "ตัวเลขตามที่ยื่นแต่ละครั้ง" แยกกัน — ถ้าบริษัทแก้งบย้อนหลัง
ตัวเลขใหม่จะเป็น fact ใหม่ที่ `filed` ช้ากว่า ตัวเลขเดิมไม่ถูกเขียนทับ
→ ถาม "ณ วันที่ t รู้อะไรบ้าง" ได้ตรง ๆ ด้วย `filed <= t` (ตรงตามโน้ต 09 ส่วนที่ 4)

## ข้อจำกัดที่ต้องรู้

- ตัวเลขเข้ามาตอนยื่น 10-Q/10-K ซึ่งช้ากว่าวันประกาศงบ (8-K 2.02) ราว 1–5 สัปดาห์
  → ข้อมูลที่นี่ "ช้ากว่าตลาด" เสมอ = ปลอดภัยจาก look-ahead แต่ไม่ใช่ข่าวใหม่
- งบกระแสเงินสดรายงานแบบสะสมทั้งปี (3/6/9/12 เดือน) ไม่มีรายไตรมาส
  ต้องลบกันเองเพื่อได้ยอดรายไตรมาส — ดู `discrete_quarters()`
- ธนาคารใช้ concept ต่างจากบริษัททั่วไป (ไม่มี gross profit, capex) → บางช่องเป็น None
  ซึ่งถูกต้องกว่าการยัดตัวเลขที่ไม่มีความหมาย
- **companyfacts ของ SEC ตกหล่นบาง filing** — พบจริง 2026-10-24: KO ยื่น 10-Q ไตรมาส 2
  พร้อม XBRL เมื่อ 29 ก.ค. แต่ API ยังไม่มีตัวเลขชุดนั้น (DUK, XOM, NEE, PLD อาการเดียวกัน)
  จึงยอมรับตัวเลขเก่าได้ถึง ~300 วัน และ `summary()` ติดธง `stale` ให้เห็นชัด
  แทนที่จะซ่อนบริษัทนั้นไปทั้งแถว
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta
from typing import Any

import pandas as pd
import requests

from .. import config, storage
from .sources import sec as sec_source

FACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"

# ช่วงจำนวนวันของงวด — กว้างพอรองรับปีบัญชี 52/53 สัปดาห์ (ไตรมาส 13 หรือ 14 สัปดาห์)
QUARTER_DAYS = (80, 100)
YEAR_DAYS = (350, 380)
# ตัวเลขที่เก่ากว่านี้ถือว่า "ไม่มี" — ดีกว่าแสดงตัวเลขเมื่อหลายปีก่อนเหมือนเป็นปัจจุบัน
MAX_FLOW_AGE_DAYS = 300
MAX_INSTANT_AGE_DAYS = 400
STALE_AFTER_DAYS = 135     # งวดล่าสุดเก่ากว่านี้ = พลาดไปแล้วอย่างน้อย 1 ไตรมาส
SHARE_METRICS = {"shares_out", "diluted_shares"}


# --- เก็บ --------------------------------------------------------------------

def _extract(ticker: str, cik: str, payload: dict[str, Any]) -> list[dict[str, Any]]:
    """ดึงเฉพาะ concept ที่ใช้ จาก JSON ก้อนใหญ่ (4–8 MB) เหลือไม่กี่พันแถว"""
    rows: list[dict[str, Any]] = []
    facts = payload.get("facts", {})
    for metric, candidates in config.FUNDAMENTAL_CONCEPTS.items():
        for taxonomy, concept, unit in candidates:
            entries = facts.get(taxonomy, {}).get(concept, {}).get("units", {}).get(unit) or []
            for entry in entries:
                if entry.get("form") not in config.FUNDAMENTAL_FORMS:
                    continue
                rows.append({
                    "ticker": ticker,
                    "cik": cik,
                    "metric": metric,
                    "concept": concept,
                    "unit": unit,
                    "start": entry.get("start"),
                    "end": entry.get("end"),
                    "val": entry.get("val"),
                    "fy": entry.get("fy"),
                    "fp": entry.get("fp"),
                    "form": entry.get("form"),
                    "filed": entry.get("filed"),
                    "accn": entry.get("accn"),
                })
    return rows


def _fetch_one(ticker: str, cik: str) -> list[dict[str, Any]]:
    response = requests.get(FACTS_URL.format(cik=cik), headers=sec_source._headers(), timeout=60)
    response.raise_for_status()
    return _extract(ticker, cik, response.json())


def _previous_counts() -> dict[str, int]:
    files = sorted(config.RAW_FUNDAMENTALS.glob("*.parquet"))
    if not files:
        return {}
    return pd.read_parquet(files[-1], columns=["ticker"])["ticker"].value_counts().to_dict()


def collect(tickers: list[str] | None = None) -> dict[str, Any]:
    """ดึง companyfacts ของทุกหุ้นใน watchlist แล้วเก็บเป็นตารางเดียว"""
    storage.ensure_dirs()
    targets = tickers or config.WATCHLIST
    cik_map = sec_source._ticker_to_cik()
    if not cik_map:
        return {"ok": False, "reason": "โหลดแผนที่ ticker→CIK ไม่ได้"}

    previous = _previous_counts()
    rows: list[dict[str, Any]] = []
    failed: list[str] = []
    for i, ticker in enumerate(targets):
        cik = cik_map.get(ticker.upper())
        if not cik:
            failed.append(ticker)
            continue
        if i:
            time.sleep(config.SEC_MIN_INTERVAL_SEC)
        try:
            extracted = _fetch_one(ticker, cik)
            for old_cik in config.SEC_PREDECESSOR_CIKS.get(ticker, []):
                time.sleep(config.SEC_MIN_INTERVAL_SEC)
                extracted.extend(_fetch_one(ticker, old_cik))
            # API ของ SEC เคยคืนข้อมูลไม่ครบชั่วคราว (เหลือแค่ filing ล่าสุดฉบับเดียว)
            # ถ้าจำนวน fact หดลงเกินครึ่งจากรอบก่อน ลองใหม่อีกครั้ง — ของจริงไม่หดแบบนั้น
            if previous.get(ticker) and len(extracted) < previous[ticker] * 0.5:
                time.sleep(2.0)
                retry = _fetch_one(ticker, cik)
                if len(retry) > len(extracted):
                    extracted = retry
                print(f"  ⚠️  fundamentals/{ticker}: ได้ {len(extracted)} fact (รอบก่อน {previous[ticker]})")
            rows.extend(extracted)
        except (requests.RequestException, ValueError) as exc:
            print(f"  ⚠️  fundamentals/{ticker}: {type(exc).__name__}")
            failed.append(ticker)

    if not rows:
        return {"ok": False, "reason": "ไม่ได้งบของบริษัทไหนเลย"}

    df = pd.DataFrame(rows)
    stamp = storage.utc_now().strftime("%Y-%m-%d")
    path = storage.write_parquet(df, config.RAW_FUNDAMENTALS / f"{stamp}.parquet",
                                 kind="fundamentals", tickers=len(targets) - len(failed),
                                 failed=failed)
    return {"ok": True, "facts": len(df), "tickers": df["ticker"].nunique(),
            "failed": len(failed), "path": str(path.relative_to(config.ROOT))}


def _last_fetch() -> datetime | None:
    files = sorted(config.RAW_FUNDAMENTALS.glob("*.parquet"))
    if not files:
        return None
    stamps = pd.read_parquet(files[-1], columns=["fetched_at"])["fetched_at"]
    return datetime.fromisoformat(str(stamps.max()))


def due(now: datetime | None = None) -> tuple[bool, str]:
    """ควรดึงงบใหม่ไหม — เก่าเกินกำหนด หรือมี 10-Q/10-K ใหม่ของ watchlist เข้ามา"""
    import json

    now = now or storage.utc_now()
    last = _last_fetch()
    if last is None:
        return True, "ยังไม่เคยดึง"
    if now - last > timedelta(days=config.FUNDAMENTALS_REFRESH_DAYS):
        return True, f"ข้อมูลอายุ {(now - last).days} วัน"

    for path in sorted(config.RAW_NEWS.glob("*.jsonl")):
        if path.stem[:10] < last.date().isoformat():
            continue
        with path.open(encoding="utf-8") as fh:
            for line in fh:
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                form = (record.get("extra") or {}).get("form")
                if record.get("source") != "sec" or form not in ("10-Q", "10-K"):
                    continue
                published = record.get("published_at", "")
                if published and published > last.isoformat():
                    tickers = ",".join(record.get("tickers") or [])
                    return True, f"มี {form} ใหม่ของ {tickers}"
    return False, "ยังสดอยู่"


# --- อ่านแบบ point-in-time ------------------------------------------------------

def load_facts(as_of: datetime | None = None) -> pd.DataFrame:
    """fact ทั้งหมดที่ **รู้ได้ ณ เวลา as_of** (None = ทั้งหมดที่มีตอนนี้)

    เงื่อนไข `filed < วันที่ของ as_of` เข้มกว่า `<=` หนึ่งวันโดยตั้งใจ:
    filing ที่ยื่นช่วงเย็นของวันนั้นอาจยังไม่มีใครเห็น ณ เวลาที่ถาม
    สำหรับงวดเดียวกันที่ยื่นหลายครั้ง (งบแก้ไข) เก็บฉบับล่าสุดที่รู้ ณ ตอนนั้น
    """
    files = sorted(config.RAW_FUNDAMENTALS.glob("*.parquet"))
    if not files:
        return pd.DataFrame()
    # รวมทุก snapshot ไม่ใช่แค่ไฟล์ล่าสุด — fact แต่ละตัวคือบันทึก "ยื่นอะไร เมื่อไหร่"
    # ที่ไม่เปลี่ยน แต่ API ของ SEC คืนข้อมูลไม่ครบในบางรอบ (พบจริง: XOM รอบหนึ่งได้
    # revenue 127 แถว อีกรอบไม่กี่นาทีต่อมาได้ 4 แถว) การรวมจึงได้ความรู้ที่ครบที่สุด
    df = pd.concat([pd.read_parquet(f) for f in files], ignore_index=True)
    df = df.drop_duplicates(
        ["ticker", "metric", "concept", "unit", "start", "end", "val", "filed", "accn"]
    )
    for column in ("start", "end", "filed"):
        df[column] = pd.to_datetime(df[column], errors="coerce")
    if as_of is not None:
        df = df[df["filed"] < pd.Timestamp(as_of.date())]
    return (
        df.sort_values("filed")
        .drop_duplicates(["ticker", "metric", "concept", "start", "end"], keep="last")
        .reset_index(drop=True)
    )


def discrete_quarters(facts: pd.DataFrame) -> pd.Series:
    """ยอดรายไตรมาส (index = วันสิ้นงวด) จาก fact แบบ 3 เดือน และแบบสะสม YTD

    1. fact ที่ยาว ~3 เดือน คือยอดไตรมาสอยู่แล้ว
    2. ไตรมาสที่ไม่มีตัวเลขตรง (เช่น Q4 หรือกระแสเงินสดทุกไตรมาส) ได้จากการลบยอดสะสม
       ที่เริ่มวันเดียวกัน: Q2 = 6 เดือน − 3 เดือน, Q4 = ทั้งปี − 9 เดือน
    """
    flows = facts.dropna(subset=["start", "end", "val"]).copy()
    if flows.empty:
        return pd.Series(dtype=float)
    flows["days"] = (flows["end"] - flows["start"]).dt.days

    quarters: dict[pd.Timestamp, float] = {}
    direct = flows[flows["days"].between(*QUARTER_DAYS)]
    for row in direct.itertuples():
        quarters[row.end] = float(row.val)

    for _, chain in flows.groupby("start"):
        chain = chain.sort_values("end")
        previous = None
        for row in chain.itertuples():
            if previous is not None and row.end not in quarters:
                gap = (row.end - previous.end).days
                if QUARTER_DAYS[0] <= gap <= QUARTER_DAYS[1] and row.days > previous.days:
                    quarters[row.end] = float(row.val) - float(previous.val)
            previous = row

    return pd.Series(quarters, dtype=float).sort_index()


def _chain_ok(index: pd.DatetimeIndex) -> bool:
    gaps = [(b - a).days for a, b in zip(index[:-1], index[1:])]
    return all(QUARTER_DAYS[0] <= g <= QUARTER_DAYS[1] for g in gaps)


def ttm(facts: pd.DataFrame, today: pd.Timestamp) -> dict[str, Any] | None:
    """ยอด 12 เดือนล่าสุด (TTM) + TTM ของปีก่อนหน้า (ไว้คิดการเติบโต)

    ลองรวม 4 ไตรมาสล่าสุดที่ต่อเนื่องกันก่อน ถ้าไม่ได้ ใช้ตัวเลขทั้งปีล่าสุดแทน
    """
    quarters = discrete_quarters(facts)
    result: dict[str, Any] | None = None

    if len(quarters) >= 4 and _chain_ok(quarters.index[-4:]):
        last4 = quarters.iloc[-4:]
        result = {"value": float(last4.sum()), "end": last4.index[-1], "basis": "4q"}
        if len(quarters) >= 8 and _chain_ok(quarters.index[-8:]):
            result["prior"] = float(quarters.iloc[-8:-4].sum())
        result["last_q"] = float(last4.iloc[-1])
        year_ago = [d for d in quarters.index if 350 <= (last4.index[-1] - d).days <= 380]
        if year_ago:
            result["last_q_year_ago"] = float(quarters[year_ago[-1]])
    else:
        annual = facts.dropna(subset=["start"]).copy()
        annual["days"] = (annual["end"] - annual["start"]).dt.days
        annual = annual[annual["days"].between(*YEAR_DAYS)].sort_values("end")
        if not annual.empty:
            last = annual.iloc[-1]
            result = {"value": float(last["val"]), "end": last["end"], "basis": "fy"}
            prior = annual[(last["end"] - annual["end"]).dt.days.between(*YEAR_DAYS)]
            if not prior.empty:
                result["prior"] = float(prior.iloc[-1]["val"])

    if result is None or (today - result["end"]).days > MAX_FLOW_AGE_DAYS:
        return None
    return result


def _latest_instant(facts: pd.DataFrame, today: pd.Timestamp) -> tuple[float, pd.Timestamp] | None:
    point = facts.dropna(subset=["end", "val"]).sort_values(["end", "filed"])
    if point.empty:
        return None
    last = point.iloc[-1]
    if (today - last["end"]).days > MAX_INSTANT_AGE_DAYS:
        return None
    return float(last["val"]), last["end"]


def _shares(group: pd.DataFrame, today: pd.Timestamp) -> float | None:
    """จำนวนหุ้นทั้งหมด — บริษัทที่มีหลายชนิดหุ้น (GOOGL มี A/B/C) รายงานแยกต่อชนิด
    ภายในเอกสารเดียวกัน จึงรวมตาม accn ของเอกสารล่าสุด
    """
    cover = group[group["metric"] == "shares_out"].dropna(subset=["end", "val"])
    if not cover.empty:
        latest_accn = cover.sort_values(["filed", "end"]).iloc[-1]["accn"]
        same = cover[cover["accn"] == latest_accn]
        same = same[same["end"] == same["end"].max()]
        if (today - same["end"].max()).days <= MAX_INSTANT_AGE_DAYS:
            return float(same["val"].sum())
    diluted = group[group["metric"] == "diluted_shares"].dropna(subset=["start", "end", "val"])
    if not diluted.empty:
        diluted = diluted.assign(days=(diluted["end"] - diluted["start"]).dt.days)
        quarterly = diluted[diluted["days"].between(*QUARTER_DAYS)].sort_values("end")
        if not quarterly.empty and (today - quarterly.iloc[-1]["end"]).days <= MAX_FLOW_AGE_DAYS:
            return float(quarterly.iloc[-1]["val"])
    return None


def _pick_concept(group: pd.DataFrame, metric: str, today: pd.Timestamp | None = None) -> pd.DataFrame:
    """บริษัทหนึ่งอาจมีหลาย concept ของ metric เดียว (เปลี่ยนชื่อตามมาตรฐานบัญชี)

    กติกาทั่วไป: เลือกตัวที่มีงวดล่าสุด — เสมอกันให้ใช้ลำดับใน config
    ยกเว้น revenue: ในบรรดาตัวที่งวดล่าสุดห่างกันไม่เกิน 1 ไตรมาส เลือกตัวที่ TTM ใหญ่ที่สุด
    เพราะบริษัทแท็กทั้งยอดรวมและยอดย่อยพร้อมกันได้ (AMT, DUK) และยอดรวมใหญ่กว่าเสมอ
    """
    rows = group[group["metric"] == metric]
    if rows.empty:
        return rows
    order = {concept: i for i, (_, concept, _) in enumerate(config.FUNDAMENTAL_CONCEPTS[metric])}
    latest = rows.groupby("concept")["end"].max()
    ranked = sorted(latest.index, key=lambda c: (-latest[c].value, order.get(c, 99)))

    if metric == "revenue" and today is not None:
        newest = latest[ranked[0]]
        fresh = [c for c in ranked if (newest - latest[c]).days <= QUARTER_DAYS[1]]
        sized = []
        for concept in fresh:
            block = ttm(rows[rows["concept"] == concept], today)
            if block:
                sized.append((block["value"], concept))
        if sized:
            return rows[rows["concept"] == max(sized)[1]]
    return rows[rows["concept"] == ranked[0]]


def _ratio(numerator: float | None, denominator: float | None, positive_only: bool = False) -> float | None:
    if numerator is None or denominator in (None, 0):
        return None
    if positive_only and (denominator <= 0 or numerator <= 0):
        return None
    return numerator / denominator


def facts_for(ticker: str, cik: str) -> pd.DataFrame:
    """fact ของบริษัทใดก็ได้ (ไม่ต้องอยู่ใน watchlist) — ใช้ตอนสแกนทั้งตลาดแล้วต้องดูงบตัวที่ถูกเลือก

    ไม่บันทึกลงคลัง (SEC เก็บย้อนหลังให้อยู่แล้ว) — คืนตารางรูปแบบเดียวกับ load_facts()
    """
    df = pd.DataFrame(_fetch_one(ticker, cik))
    if df.empty:
        return df
    for column in ("start", "end", "filed"):
        df[column] = pd.to_datetime(df[column], errors="coerce")
    return (df.sort_values("filed")
            .drop_duplicates(["ticker", "metric", "concept", "start", "end"], keep="last")
            .reset_index(drop=True))


def summary(as_of: datetime | None = None, closes: dict[str, float] | None = None,
            facts: pd.DataFrame | None = None) -> dict[str, dict[str, Any]]:
    """ตัวเลขสำคัญต่อบริษัท ณ เวลา as_of — ที่แผนรายสัปดาห์และ brief ใช้

    ราคาที่ใช้คิด valuation คือ `close` (ไม่ใช่ adj_close) เพราะ EPS เป็นต่อหุ้นจริง
    ณ ตอนนั้น ส่วน adj_close ถูกปรับย้อนหลังด้วยเงินปันผล
    `facts` = ส่งตารางมาเอง (เช่นจาก facts_for) แทนการอ่านคลัง
    """
    facts = load_facts(as_of) if facts is None else facts
    if facts.empty:
        return {}
    today = pd.Timestamp((as_of or storage.utc_now()).date())
    closes = closes or {}
    out: dict[str, dict[str, Any]] = {}

    for ticker, group in facts.groupby("ticker"):
        row: dict[str, Any] = {}
        for metric in ("revenue", "net_income", "eps_diluted", "operating_income",
                       "gross_profit", "cfo", "capex"):
            row[metric] = ttm(_pick_concept(group, metric, today), today)
        instants = {m: _latest_instant(_pick_concept(group, m), today) for m in ("equity", "debt", "cash")}
        shares = _shares(group, today)
        price = closes.get(ticker)

        revenue = row["revenue"]["value"] if row["revenue"] else None
        eps = row["eps_diluted"]["value"] if row["eps_diluted"] else None
        net_income = row["net_income"]["value"] if row["net_income"] else None
        cfo = row["cfo"]["value"] if row["cfo"] else None
        capex = row["capex"]["value"] if row["capex"] else None
        equity = instants["equity"][0] if instants["equity"] else None
        debt = instants["debt"][0] if instants["debt"] else None
        market_cap = price * shares if price and shares else None
        fcf = cfo - capex if cfo is not None and capex is not None else None

        def growth(block: dict[str, Any] | None, key_now: str = "value", key_then: str = "prior") -> float | None:
            """% เติบโต — คืน None ถ้าฐานเป็นศูนย์/ติดลบ (เช่น กำไรพลิกจากขาดทุน ตีความเป็น % ไม่ได้)"""
            if not block:
                return None
            now_value, then_value = block.get(key_now), block.get(key_then)
            if now_value is None or then_value is None or then_value <= 0:
                return None
            return (now_value / then_value - 1.0) * 100

        filings = group.sort_values("filed").iloc[-1]
        period_end = row["revenue"]["end"] if row["revenue"] else None
        out[ticker] = {
            "period_end": str(period_end.date()) if period_end is not None else None,
            "stale": period_end is None or (today - period_end).days > STALE_AFTER_DAYS,
            "last_filing": {"form": filings["form"], "filed": str(filings["filed"].date()),
                            "period_end": str(filings["end"].date())},
            "revenue_ttm": revenue,
            "revenue_growth_pct": growth(row["revenue"]),
            "revenue_q_yoy_pct": growth(row["revenue"], "last_q", "last_q_year_ago"),
            "eps_ttm": eps,
            "eps_growth_pct": growth(row["eps_diluted"]),
            "net_margin_pct": _pct(_ratio(net_income, revenue)),
            "operating_margin_pct": _pct(_ratio(
                row["operating_income"]["value"] if row["operating_income"] else None, revenue)),
            "gross_margin_pct": _pct(_ratio(
                row["gross_profit"]["value"] if row["gross_profit"] else None, revenue)),
            "fcf_ttm": fcf,
            "market_cap": market_cap,
            "pe": _ratio(price, eps, positive_only=True),
            "ps": _ratio(market_cap, revenue, positive_only=True),
            "fcf_yield_pct": _pct(_ratio(fcf, market_cap)),
            "debt_to_equity": _ratio(debt, equity, positive_only=True),
        }
    return out


def _pct(value: float | None) -> float | None:
    return value * 100 if value is not None else None
