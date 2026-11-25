"""Trading costs: a factor with a good IC that needs frequent rebalancing can lose money net.

net = gross - turnover x (commission + half spread + slippage)
The default 10 bps per unit of traded value is a conservative level for large caps and liquid ETFs;
small caps should use 30 to 100 bps.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

DEFAULT_BPS = 10.0


def net_returns(gross: pd.Series, turnover: pd.Series | float, bps: float = DEFAULT_BPS) -> pd.Series:
    return gross - turnover * bps / 10_000


def tick_round_trip(price: pd.DataFrame, ticks: list[tuple[float, float]], commission: float) -> pd.DataFrame:
    """Cost of one buy-and-sell round trip (as a share of price) in a market with a tick table, such as SET.

    Even the most liquid stock's spread is at least 1 tick: buy at the ask, sell at the bid,
    which loses 1 tick per round trip. On SET, 1 tick is 0.3 to 1% of the price (stocks at ฿25 to ฿100 move in ฿0.25 steps),
    much more than the commission, so it must be computed per stock from its price, not as fixed bps as for US stocks.

    `ticks` = [(price below, tick size), ...] from low to high; this is a minimum, and illiquid stocks have wider spreads.
    """
    values = price.to_numpy(dtype=float)
    tick = np.full(values.shape, np.nan)
    lower = 0.0
    for upper, size in ticks:
        tick[(values >= lower) & (values < upper)] = size
        lower = upper
    return pd.DataFrame(tick / values + commission, index=price.index, columns=price.columns)
