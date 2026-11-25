"""Financial statements from SEC XBRL (companyfacts): point-in-time data by nature.

## Why this source is better than it looks

Every figure in companyfacts comes with:
  `end`    = end of the period (event_date)
  `filed`  = date filed with the SEC (knowledge_date), the core of point-in-time
  `accn`   = accession number (leads back to the original document)
and each filing's figures are kept separately: if a company restates,
the new figure is a new fact with a later `filed`, and the old figure is not overwritten.
So "what was known on date t" is simply `filed <= t` (as in note 09, part 4).

## Limits to know

- Figures arrive with the 10-Q/10-K, about 1 to 5 weeks after the earnings release (8-K 2.02),
  so this data always lags the market: safe from look-ahead, but not news.
- Cash-flow statements are reported year to date (3/6/9/12 months), not per quarter;
  quarterly values come from subtracting them, see `discrete_quarters()`.
- Banks use different concepts from other companies (no gross profit, no capex), so some fields are None,
  which is more correct than forcing in a meaningless number.
- **SEC companyfacts misses some filings.** Seen 2026-10-24: KO filed its Q2 10-Q with XBRL
  on 29 July, but the API did not have those figures yet (DUK, XOM, NEE and PLD showed the same).
  So figures up to about 300 days old are accepted, and `summary()` sets a `stale` flag
  instead of hiding the company's whole row.
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

# Period length in days, wide enough for 52/53-week fiscal years (13- or 14-week quarters)
QUARTER_DAYS = (80, 100)
YEAR_DAYS = (350, 380)
# Figures older than this count as "missing"; better than showing numbers from years ago as current
MAX_FLOW_AGE_DAYS = 300
MAX_INSTANT_AGE_DAYS = 400
STALE_AFTER_DAYS = 135     # A latest period older than this means at least one quarter was missed
SHARE_METRICS = {"shares_out", "diluted_shares"}


# --- Collect --------------------------------------------------------------------

def _extract(ticker: str, cik: str, payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Keep only the concepts in use, cutting a large JSON (4 to 8 MB) down to a few thousand rows"""
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
    """Fetch companyfacts for every watchlist stock and store them as one table"""
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
            # The SEC API has temporarily returned incomplete data (only the single latest filing)
            # If the number of facts shrinks by more than half since the last round, retry once; real data does not shrink like that
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
    """Whether to refetch financials: they are stale, or a new 10-Q/10-K for the watchlist has arrived"""
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


# --- Point-in-time reads ------------------------------------------------------

def load_facts(as_of: datetime | None = None) -> pd.DataFrame:
    """Every fact that **could be known at as_of** (None = everything available now).

    The condition `filed < date of as_of` is one day stricter than `<=` on purpose:
    a filing made late that day may not have been seen by anyone at the time of the query.
    For a period filed several times (restatements), keep the latest version known at that time.
    """
    files = sorted(config.RAW_FUNDAMENTALS.glob("*.parquet"))
    if not files:
        return pd.DataFrame()
    # Merge every snapshot, not only the latest file: each fact records "what was filed, and when"
    # and never changes, but the SEC API returns incomplete data in some rounds (seen: XOM got
    # 127 revenue rows in one round and 4 rows a few minutes later), so merging gives the most complete record
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
    """Quarterly values (index = period end) from 3-month facts and year-to-date facts.

    1. A fact covering about 3 months is already a quarterly value.
    2. Quarters without a direct figure (such as Q4, or every quarter of cash flow) come from subtracting
       year-to-date values that start on the same day: Q2 = 6 months - 3 months, Q4 = full year - 9 months.
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
    """Trailing 12 months (TTM) plus the prior year's TTM (for growth).

    First try summing the latest 4 consecutive quarters; otherwise use the latest full-year figure.
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
    """Total shares outstanding. Companies with several share classes (GOOGL has A/B/C) report each class
    separately in the same document, so they are summed by the accn of the latest document.
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
    """A company may use several concepts for one metric (renamed as accounting standards change).

    General rule: pick the one with the latest period; on a tie, use the order in config.
    Exception for revenue: among concepts whose latest periods are within one quarter of each other, pick the
    largest TTM, because companies can tag the total and a subtotal at once (AMT, DUK) and the total is always larger.
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
    """Facts for any company (not only the watchlist), used after a whole-market scan to look at the selected stocks.

    Not saved to the store (the SEC keeps the history anyway); returns a table in the same form as load_facts().
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
    """Key figures per company at as_of, as used by the weekly plan and the brief.

    Valuation uses `close` (not adj_close) because EPS is per actual share
    at that time, while adj_close is adjusted backwards for dividends.
    `facts` = pass a table in (for example from facts_for) instead of reading the store.
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
            """Percent growth; None when the base is zero or negative (a swing from loss to profit has no meaningful percentage)"""
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
