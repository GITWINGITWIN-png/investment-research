"""Data splits for financial data: part 3 of note 09.

Plain K-fold does not work: a random split trains on the future and tests on the past.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def walk_forward(dates: pd.DatetimeIndex, train_years: int = 3, test_months: int = 12) -> list[dict[str, pd.Timestamp]]:
    """Rolling window: [train 3 years] -> [test the next 12 months], then move forward 12 months at a time.

    It mimics what would happen in practice (update periodically, then apply to an unknown future) and gives several results,
    so the variation across years is visible instead of one number that may be lucky.
    """
    splits = []
    start = dates[0]
    while True:
        train_end = start + pd.DateOffset(years=train_years)
        test_end = train_end + pd.DateOffset(months=test_months)
        test = dates[(dates >= train_end) & (dates < test_end)]
        if len(test) < 20:
            break
        train = dates[(dates >= start) & (dates < train_end)]
        splits.append({"train_start": train[0], "train_end": train[-1], "test_start": test[0], "test_end": test[-1]})
        start = start + pd.DateOffset(months=test_months)
    return splits


def purged_kfold(n: int, n_splits: int = 5, horizon: int = 20, embargo: int = 5) -> list[dict[str, np.ndarray]]:
    """K contiguous folds + purge + embargo (López de Prado, AFML chapter 7).

    - **purge**: training samples whose label uses returns `horizon` days ahead that reach into the test period
      must be dropped, otherwise the model "sees" the test answers through the training labels
    - **embargo**: leave a further `embargo` days after the test period before data is used for training again,
      to cut remaining autocorrelation
    """
    folds = np.array_split(np.arange(n), n_splits)
    out = []
    for test in folds:
        first, last = int(test[0]), int(test[-1])
        train = np.ones(n, dtype=bool)
        train[first:last + 1] = False
        train[max(0, first - horizon):first] = False          # purge
        train[last + 1:min(n, last + 1 + embargo)] = False      # embargo
        out.append({"train": np.flatnonzero(train), "test": test})
    return out


def walk_forward_ic(ic: pd.Series, splits: list[dict[str, pd.Timestamp]]) -> pd.DataFrame:
    """Mean IC of each test period; a good factor is consistently positive, not good for a single year"""
    rows = []
    for split in splits:
        window = ic.loc[split["test_start"]:split["test_end"]]
        rows.append({"test_start": split["test_start"].date(), "test_end": split["test_end"].date(),
                     "mean_ic": float(window.mean()) if len(window) else float("nan"), "days": len(window)})
    return pd.DataFrame(rows)
