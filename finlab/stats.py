"""Statistics that guard against fooling ourselves: part 2 of note 09 (multiple testing).

The problem: try 1,000 formulas with no signal and about 50 will "pass" at the 5% level by chance;
picking the best of those 50 picks the luckiest noise.

Tools:
- Probabilistic Sharpe (PSR): the probability that the true Sharpe beats a threshold, given the sample length
  and the shape of the distribution (skew, fat tails); Bailey & López de Prado 2012
- Deflated Sharpe (DSR): PSR with the threshold raised to "the highest Sharpe expected by chance"
  from N tries; the more tries, the more it must win by; Bailey & López de Prado 2014
- PBO (CSCV): the probability that the best in-sample choice ranks below the median out of sample;
  Bailey, Borwein, López de Prado & Zhu 2014
"""

from __future__ import annotations

import itertools
import math
from statistics import NormalDist

import numpy as np
import pandas as pd

NORMAL = NormalDist()
EULER_GAMMA = 0.5772156649015329


def sharpe(returns: pd.Series, periods: int = 252) -> float:
    r = returns.dropna()
    return float(r.mean() / r.std() * math.sqrt(periods)) if len(r) > 2 and r.std() > 0 else float("nan")


def probabilistic_sharpe(returns: pd.Series, benchmark_sr: float = 0.0) -> float:
    """PSR, using the per-period Sharpe (not annualised) for both returns and benchmark_sr"""
    r = returns.dropna()
    n = len(r)
    if n < 3 or r.std() == 0:
        return float("nan")
    sr = r.mean() / r.std()
    skew = float(r.skew())
    kurt = float(r.kurt()) + 3.0            # pandas gives excess kurtosis; the formula needs the non-excess form
    denominator = math.sqrt(max(1e-12, 1 - skew * sr + (kurt - 1) / 4 * sr ** 2))
    return NORMAL.cdf((sr - benchmark_sr) * math.sqrt(n - 1) / denominator)


def expected_max_sharpe(n_trials: int, variance_of_sr: float) -> float:
    """Highest per-period Sharpe expected from n_trials tries when there is no signal at all"""
    if n_trials < 2 or variance_of_sr <= 0:
        return 0.0
    return math.sqrt(variance_of_sr) * (
        (1 - EULER_GAMMA) * NORMAL.inv_cdf(1 - 1 / n_trials)
        + EULER_GAMMA * NORMAL.inv_cdf(1 - 1 / (n_trials * math.e))
    )


def deflated_sharpe(returns: pd.Series, n_trials: int, variance_of_sr: float) -> float:
    """DSR > 0.95 = this Sharpe is probably not an artefact of many tries (at 95% confidence)"""
    return probabilistic_sharpe(returns, expected_max_sharpe(n_trials, variance_of_sr))


def pbo_cscv(performance: pd.DataFrame, blocks: int = 16) -> dict[str, float]:
    """Probability of Backtest Overfitting.

    `performance` = per-period returns (rows = time, columns = strategies or settings tried).
    Time is split into `blocks` chunks; every way of choosing half of them as IS and the other half as OOS
    finds the best IS choice and looks at its rank OOS.
    PBO = the share of cases in which the best IS choice falls into the bottom half OOS (> 0.5 = probably overfit).
    """
    data = performance.dropna(how="all").fillna(0.0).to_numpy()
    rows = (len(data) // blocks) * blocks
    chunks = np.array_split(data[:rows], blocks)
    logits = []
    for chosen in itertools.combinations(range(blocks), blocks // 2):
        in_sample = np.vstack([chunks[i] for i in chosen])
        out_sample = np.vstack([chunks[i] for i in range(blocks) if i not in chosen])
        is_sr = in_sample.mean(axis=0) / (in_sample.std(axis=0) + 1e-12)
        oos_sr = out_sample.mean(axis=0) / (out_sample.std(axis=0) + 1e-12)
        best = int(np.argmax(is_sr))
        rank = (oos_sr < oos_sr[best]).sum() + 1                 # 1 = worst, N = best
        omega = rank / (len(oos_sr) + 1)
        logits.append(math.log(omega / (1 - omega)))
    logits_arr = np.array(logits)
    return {"pbo": float((logits_arr <= 0).mean()), "combinations": len(logits),
            "logit_median": float(np.median(logits_arr))}
