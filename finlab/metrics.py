"""Factor-level metrics: part 1 of note 09 (Evaluation Playbook).

Every function takes a (date x ticker) factor whose value on day t uses data up to t only,
and forward returns from data.forward_returns.
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd

from .data import forward_returns


def rank_ic(factor: pd.DataFrame, fwd: pd.DataFrame, min_names: int = 20, method: str = "spearman") -> pd.Series:
    """Daily IC = cross-sectional correlation between that day's factor values and forward returns.

    Rank IC (Spearman) = correlation of the ranks; robust to a few stocks jumping 300%, which would
    drag the Pearson value; Rank IC is the main measure, per note 09.
    Days with fewer than `min_names` stocks to compare are dropped (a correlation from a handful of stocks is noise).
    """
    f, r = factor.align(fwd, join="inner")
    valid = f.notna() & r.notna()
    f, r = f.where(valid), r.where(valid)
    if method == "spearman":
        f, r = f.rank(axis=1), r.rank(axis=1)
    fm = f.sub(f.mean(axis=1), axis=0)
    rm = r.sub(r.mean(axis=1), axis=0)
    ic = (fm * rm).sum(axis=1) / np.sqrt((fm ** 2).sum(axis=1) * (rm ** 2).sum(axis=1))
    return ic[valid.sum(axis=1) >= min_names].dropna()


def ic_summary(ic: pd.Series, horizon: int) -> dict[str, float]:
    """IC summary: mean, ICIR, t-stat, share of positive days.

    ## The t-stat trap

    The IC of 20-day returns computed every day **overlaps by 19 days**, so adjacent days are almost the same value.
    Counting 2,500 days as 2,500 independent samples would inflate the t-stat by about sqrt(20) = 4.5 times,
    so the t-stat is computed only from samples h days apart (non-overlapping).
    """
    ic = ic.dropna()
    if len(ic) < 2:
        return {"mean_ic": float("nan"), "std_ic": float("nan"), "icir": float("nan"),
                "t_stat": float("nan"), "hit_rate": float("nan"), "n_days": len(ic), "n_independent": 0}
    sampled = ic.iloc[::max(horizon, 1)]
    t_stat = (sampled.mean() / sampled.std() * math.sqrt(len(sampled))) if len(sampled) > 2 and sampled.std() > 0 else float("nan")
    return {
        "mean_ic": float(ic.mean()),
        "std_ic": float(ic.std()),
        "icir": float(ic.mean() / ic.std()) if ic.std() > 0 else float("nan"),
        "t_stat": float(t_stat),
        "hit_rate": float((ic > 0).mean()),
        "n_days": int(len(ic)),
        "n_independent": int(len(sampled)),
    }


def decay_curve(factor: pd.DataFrame, prices: pd.DataFrame, horizons: tuple[int, ...] = (1, 5, 10, 21, 63)) -> pd.DataFrame:
    """IC at several horizons: how long the signal lasts (fast decay = frequent trading = fees eat the return)"""
    rows = []
    for horizon in horizons:
        summary = ic_summary(rank_ic(factor, forward_returns(prices, horizon)), horizon)
        rows.append({"horizon": horizon, **summary})
    return pd.DataFrame(rows).set_index("horizon")


def quantile_spread(factor: pd.DataFrame, fwd: pd.DataFrame, quantiles: int = 5) -> pd.Series:
    """Daily forward return of the top group minus the bottom group (a rough long-short)"""
    f, r = factor.align(fwd, join="inner")
    ranks = f.where(r.notna()).rank(axis=1, pct=True)
    top = r.where(ranks > 1 - 1 / quantiles).mean(axis=1)
    bottom = r.where(ranks <= 1 / quantiles).mean(axis=1)
    return (top - bottom).dropna()


def top_quantile_turnover(factor: pd.DataFrame, quantiles: int = 5, step: int = 21) -> float:
    """Share of the top group's members that change per rebalance; this drives trading costs"""
    ranks = factor.rank(axis=1, pct=True).iloc[::step]
    top = ranks > 1 - 1 / quantiles
    changed = (top & ~top.shift(fill_value=False)).sum(axis=1)
    size = top.sum(axis=1).replace(0, np.nan)
    return float((changed / size).iloc[1:].mean())


def lookahead_audit(factor: pd.DataFrame, prices: pd.DataFrame, horizon: int = 5) -> dict[str, object]:
    """Check for two symptoms of "knowing the future".

    1. **IC too high**: daily cross-sectional factors with a mean IC above 0.15 hardly exist (note 09)
    2. **IC vanishes when shifted**: using the factor value from h days earlier (data only slightly older),
       a real, slow factor degrades a little, but a factor that peeks at the future almost disappears,
       because the "future" as of h days ago is a past that says nothing about the real future.
    """
    fwd = forward_returns(prices, horizon)
    ic_now = float(rank_ic(factor, fwd).mean())
    ic_lagged = float(rank_ic(factor.shift(horizon), fwd).mean())
    flags = []
    if ic_now > 0.15:
        flags.append(f"IC เฉลี่ย {ic_now:.3f} > 0.15 — สูงเกินกว่าที่ factor จริงทำได้")
    if ic_now > 0.05 and ic_lagged < 0.25 * ic_now:
        flags.append(f"IC หายจาก {ic_now:.3f} เหลือ {ic_lagged:.3f} เมื่อใช้ค่าเก่าไป {horizon} วัน")
    return {"ic": ic_now, "ic_lagged": ic_lagged, "suspicious": bool(flags), "flags": flags}
