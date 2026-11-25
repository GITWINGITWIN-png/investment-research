"""Storage layer that enforces point-in-time discipline.

The core rule of this file: **everything written to disk must carry `fetched_at`**.

Why (see the note "09 - Evaluation Playbook"):
without knowing when a piece of data became known, we would unknowingly use future data
in backtested decisions, which is look-ahead bias and voids every test result.

`fetched_at` is the most conservative version of knowledge_date: we know for certain
that the data existed at that time (because we had just fetched it).
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd

from . import config


def utc_now() -> datetime:
    """Current time, always timezone-aware; naive datetimes breed bugs"""
    return datetime.now(tz=config.UTC)


def utc_stamp() -> str:
    """ISO string used as fetched_at; seconds are precise enough"""
    return utc_now().replace(microsecond=0).isoformat()


def ensure_dirs() -> None:
    """Create every required folder; safe to call repeatedly"""
    for path in (
        config.RAW_PRICES,
        config.RAW_NEWS,
        config.RAW_CALENDAR,
        config.RAW_FUNDAMENTALS,
        config.DERIVED,
        config.PREDICTIONS,
        config.HYPOTHESES,
        config.BRIEFS,
        config.PLANS,
        config.PLAN_REQUESTS,
        config.JOURNAL_PLANS,
        config.ALERTS,
        config.JOURNAL_ALERTS,
    ):
        path.mkdir(parents=True, exist_ok=True)


def log_manifest(kind: str, path: Path, rows: int, **extra: Any) -> None:
    """Write one line per stored file: the audit trail of the data store.

    JSONL because it can be appended to without reading the file,
    and if the file breaks midway, the earlier lines stay readable.
    """
    config.MANIFEST.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "fetched_at": utc_stamp(),
        "kind": kind,
        "path": str(path.relative_to(config.ROOT)),
        "rows": rows,
        **extra,
    }
    with config.MANIFEST.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")


def write_parquet(df: pd.DataFrame, path: Path, kind: str, **extra: Any) -> Path:
    """Write a DataFrame with fetched_at attached and record it in the manifest.

    If the file already exists it is **never overwritten**; a new suffix is added instead,
    because stored raw data is evidence, and overwriting would destroy the audit trail.
    """
    path.parent.mkdir(parents=True, exist_ok=True)

    if "fetched_at" not in df.columns:
        df = df.assign(fetched_at=utc_stamp())

    final = path
    counter = 1
    while final.exists():
        final = path.with_name(f"{path.stem}__v{counter}{path.suffix}")
        counter += 1

    df.to_parquet(final, index=False)
    log_manifest(kind, final, len(df), **extra)
    return final


def write_jsonl(records: list[dict[str, Any]], path: Path, kind: str, **extra: Any) -> Path:
    """Store semi-structured data (such as news) in its rawest form.

    The intent: keep each response exactly as received, uninterpreted and with every field,
    because we do not know which fields we will want in 12 months, and by then it cannot be collected again.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    stamp = utc_stamp()

    final = path
    counter = 1
    while final.exists():
        final = path.with_name(f"{path.stem}__v{counter}{path.suffix}")
        counter += 1

    with final.open("w", encoding="utf-8") as fh:
        for record in records:
            fh.write(json.dumps({"fetched_at": stamp, **record}, ensure_ascii=False) + "\n")

    log_manifest(kind, final, len(records), **extra)
    return final


def read_all_prices() -> pd.DataFrame:
    """Read every stored price snapshot and merge them into one table.

    If a date repeats (for example two runs in one day), keep the row with the newest fetched_at,
    because closing prices can be corrected after the close, so the later value is more accurate.
    """
    files = sorted(config.RAW_PRICES.glob("*.parquet"))
    if not files:
        return pd.DataFrame()

    frames = [pd.read_parquet(f) for f in files]
    df = pd.concat(frames, ignore_index=True)

    df["date"] = pd.to_datetime(df["date"]).dt.tz_localize(None).dt.normalize()
    df = (
        df.sort_values("fetched_at")
        .drop_duplicates(subset=["ticker", "date"], keep="last")
        .sort_values(["ticker", "date"])
        .reset_index(drop=True)
    )
    return df


def price_matrix(field: str = "close") -> pd.DataFrame:
    """Convert to a wide table: index = date, columns = ticker.

    This shape makes returns and momentum easiest to compute.
    """
    df = read_all_prices()
    if df.empty:
        return pd.DataFrame()
    return df.pivot(index="date", columns="ticker", values=field).sort_index()
