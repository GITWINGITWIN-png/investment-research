"""US stock-market time: every "when could this be known" question goes through this file.

## Why this file exists

The system runs from Thailand, but the market is in New York; regular trading is 09:30 to 16:00 ET,
which is **20:30 to 03:00 Thai time** (during EDT) or 21:30 to 04:00 (during EST).

After moving from a 06:00 cron job to anacron, which runs "when the machine is on", a job may run at night
while the market is still open. Without care we would store an intraday price in the close field,
and the brief and the plan would read it as a closing price. So the rules are defined here:

1. **Store only closed bars**: while the market is open, today's bar is dropped.
2. **A plan takes effect at the close of the first trading day that closes *after* the decision time**
   (as if a market-on-close order could really be sent), which rules out look-ahead by construction.

Market holidays are deliberately not listed here: holidays have no price bar anyway,
so functions that need "real trading days" take the calendar from the price data (days on which SPY has a price)
instead of a hand-kept holiday list that can be wrong and needs yearly updates.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pandas as pd

from . import config

ET = ZoneInfo("America/New_York")
SESSION_OPEN = time(9, 30)
SESSION_CLOSE = time(16, 0)


def utc_now() -> datetime:
    return datetime.now(tz=config.UTC)


def to_et(moment: datetime) -> datetime:
    return moment.astimezone(ET)


def is_session_open(now: datetime | None = None) -> bool:
    """Whether the US market is open right now (Monday to Friday 09:30 to 16:00 ET, holidays not counted).

    Not knowing holidays does no harm: a holiday has no bar to drop anyway.
    """
    et = to_et(now or utc_now())
    return et.weekday() < 5 and SESSION_OPEN <= et.time() < SESSION_CLOSE


def session_close_utc(day: date) -> datetime:
    """That day's market close in UTC: 16:00 ET converted with that day's own DST"""
    return datetime.combine(day, SESSION_CLOSE, tzinfo=ET).astimezone(config.UTC)


def drop_incomplete_session(df: pd.DataFrame, now: datetime | None = None) -> tuple[pd.DataFrame, int]:
    """Drop the bars of a day that is still trading; returns (remaining table, number of rows dropped).

    yfinance returns today's bar before the close, with the latest price in the close field;
    keeping it would give that day's snapshot a "close" that is not the real close.
    """
    if df.empty or not is_session_open(now):
        return df, 0
    today_et = to_et(now or utc_now()).date()
    mask = pd.to_datetime(df["date"]).dt.date == today_et
    return df.loc[~mask].reset_index(drop=True), int(mask.sum())


def effective_session(created_at: datetime, sessions: pd.DatetimeIndex) -> pd.Timestamp | None:
    """First trading day whose market close is **after** the decision time = the day the plan takes effect.

    Example: a plan issued Saturday 06:00 Thai time -> Monday is the first day that closes after it,
    so the portfolio is rebalanced at Monday's close and the first counted return is Tuesday's.

    This is more conservative than counting from Monday's open (it gives up Monday's morning return)
    but can never profit from weekend news the plan already read; a handicap is better than cheating.
    Returns None if that day has not come yet (no close in the data).
    """
    for day in sessions:
        if session_close_utc(day.date()) > created_at:
            return day
    return None


def last_close_before(created_at: datetime, sessions: pd.DatetimeIndex) -> pd.Timestamp | None:
    """Latest trading day already closed at decision time; used as the reference price of a prediction"""
    past = [d for d in sessions if session_close_utc(d.date()) <= created_at]
    return past[-1] if past else None


def next_session_date(now: datetime | None = None) -> date:
    """Next trading day not yet closed (counting Monday to Friday, without holidays).

    Used to decide "which week a weekly plan belongs to": on Saturday morning Thai time
    (Friday evening in New York, after the close) the next day is Monday, so it is next week's plan.
    """
    et = to_et(now or utc_now())
    day = et.date()
    if et.weekday() < 5 and et.time() < SESSION_CLOSE:
        return day
    day += timedelta(days=1)
    while day.weekday() >= 5:
        day += timedelta(days=1)
    return day


def iso_week(day: date) -> str:
    """ISO week name such as 2026-W44; sorts alphabetically in time order"""
    year, week, _ = day.isocalendar()
    return f"{year}-W{week:02d}"


def sessions_from_prices(prices: pd.DataFrame) -> pd.DatetimeIndex:
    """Real trading calendar from the data: days on which SPY has a price (dropping BTC, which trades 7 days)"""
    if "SPY" in prices.columns:
        return prices["SPY"].dropna().index
    return prices.index
