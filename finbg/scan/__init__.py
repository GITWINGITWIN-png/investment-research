"""Scan the whole US market for small opportunities others overlook (see note 12).

A 4-stage funnel, widest where code does the work and deepest where Claude does:

    stage 0  universe.py, panel.py, filings.py
             collect the whole market: about 3,100 stocks, about 3,400 SEC filings a day   (0 tokens)
             (earnings.py, the earnings calendar, is not in the public edition)
    stage 1  score.py     score unusual activity -> 30 stocks remain                     (0 tokens)
    stage 2  triage.py    Sonnet reads the cards of the 30 -> picks <= 5 for a deep dive
    stage 3  deepdive.py  Opus digs deep -> ideas that prices can settle
    results  book.py      idea portfolio + a control group drawn at random from the same 30
    all      runner.py

## Why a control group

LLMs are very good at telling stories about noise; every idea will sound reasonable. So the question is not
"did the ideas make money" but **"does Claude's deep dive pick better than a random draw from the same pile"**.
If it does not, any profit came from the stage 1 filter or from the market, not from the analysis.
"""
