"""finlab: the research evaluation harness (phase 0 in 07 - Long-term Roadmap).

The package has one job: **tell which algorithms or factors are real and which are luck**.

    data.py         universe + price history for research (separate from finbg's point-in-time store)
    factors.py      reference factors + fake factors for testing the harness itself
    metrics.py      IC, Rank IC, ICIR, decay curve, quantile spread, turnover
    validation.py   walk-forward, purged k-fold + embargo
    stats.py        Sharpe, Probabilistic/Deflated Sharpe, PBO (CSCV)
    costs.py        trading costs from turnover
    experiments.py  records every experiment; the number of trials is an input to the Deflated Sharpe
    gate.py         gate 1: the harness must reject fakes before it can be trusted on real factors

Why write it by hand instead of using alphalens or Qlib (per note 07): without writing it yourself you never learn how the numbers can fool you.
So each function is short, with a docstring that says what it computes and where the trap is.
"""
