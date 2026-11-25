"""Reference factors: each returns a (date x ticker) table whose value on day t uses data up to day t only.

Real ones (with an economic rationale in the literature):
    momentum_12_1   12-month winners (skipping the latest month) tend to keep winning; Jegadeesh & Titman 1993
    reversal_1m     last month's losers tend to bounce (short term); Jegadeesh 1990
    low_vol_60d     low-volatility stocks give better risk-adjusted returns; the "low-volatility anomaly"

Fakes (to test that the harness catches them):
    random_noise     no signal at all; the harness must call it worthless
    lookahead_cheat  secretly uses future returns; the harness must flag it as too good to be true
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def momentum_12_1(prices: pd.DataFrame) -> pd.DataFrame:
    return prices.shift(21) / prices.shift(252) - 1.0


def reversal_1m(prices: pd.DataFrame) -> pd.DataFrame:
    return -(prices / prices.shift(21) - 1.0)


def low_vol_60d(prices: pd.DataFrame) -> pd.DataFrame:
    return -prices.pct_change().rolling(60, min_periods=50).std()


def random_noise(prices: pd.DataFrame, seed: int = 0) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    noise = rng.standard_normal(prices.shape)
    return pd.DataFrame(noise, index=prices.index, columns=prices.columns).where(prices.notna())


def lookahead_cheat(prices: pd.DataFrame, horizon: int = 5, noise: float = 3.0, seed: int = 0) -> pd.DataFrame:
    """A "cheating" factor: the return h days ahead plus a large dose of noise.

    The noise is heavy on purpose, so it looks more like a real factor (IC below 1.0)
    while staying higher than any real factor can reach, like real-life look-ahead bugs,
    which usually creep in a little at a time rather than blatantly.
    """
    future = prices.shift(-horizon) / prices - 1.0
    scale = future.stack().std()
    rng = np.random.default_rng(seed)
    jitter = pd.DataFrame(rng.standard_normal(prices.shape) * scale * noise,
                          index=prices.index, columns=prices.columns)
    return (future + jitter).where(prices.notna())


REAL = {"momentum_12_1": momentum_12_1, "reversal_1m": reversal_1m, "low_vol_60d": low_vol_60d}
