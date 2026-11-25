"""Thai track: SET/mai stocks, fully separate from the US experiment (see note 13).

## Why this track exists (measured 2026-11-08 before deciding)

Simple price signals are clearly stronger in Thai stocks than in US large caps, but most of the effect sits in stocks that cannot really be traded,
and SET's tick sizes make a round trip cost >= 0.6%. What a retail investor can likely use is
slow signals (low volatility, momentum) and Thai-language information foreign funds do not read, in the roughly 150 to 300 tradable stocks.

## Stage 1 (the files in this package): daily point-in-time collection, 0 tokens

    universe.py   every security on SET/mai on that day, so stocks delisted in future stay in our store
    prices.py     daily bars for every Thai common stock (closed bars only)
    news.py       Thai stock news from media RSS, with tickers tagged from headlines
    collect.py    runs all three (called from scripts/morning.sh)

## What is deliberately not collected, and why (site rules checked 2026-11-08)

- SET company announcements: the official channel is SMART Marketplace (paid), and SET's website
  offers no public automated channel, so its internal API is not reverse-engineered; the owner decides this.
- Form 59 (insider trades): sec.or.th blocks programs with a WAF, and robots.txt of
  market.sec.or.th forbids the idisc pages; nothing a site owner deliberately blocks is worked around.

## Kept apart from the US side by structure

No file in finbg/scan or finbg/pm imports this package, so experiments with fixed criteria
(plan W44, the idea portfolio) are unaffected however much the Thai track changes.
"""
