"""Every setting of the background track lives in this one file.

Keeping them in one place means changing the universe or moving a path touches a single file,
and reading back in six months shows at once what the settings were then.
"""

from __future__ import annotations

import os
from pathlib import Path
from zoneinfo import ZoneInfo

# --- Paths -------------------------------------------------------------------
# Every path is relative to the project root, so cron can run it from anywhere
ROOT = Path(__file__).resolve().parent.parent

DATA = ROOT / "data"
RAW = DATA / "raw"                  # Raw data: never edit, never overwrite
DERIVED = DATA / "derived"          # Derived data: can always be recomputed, safe to delete

RAW_PRICES = RAW / "prices"
RAW_NEWS = RAW / "news"
MANIFEST = RAW / "_manifest.jsonl"  # Log of what was fetched when = knowledge_date

JOURNAL = ROOT / "Journal"          # Folder that Obsidian reads
PREDICTIONS = JOURNAL / "Predictions"
HYPOTHESES = JOURNAL / "Hypotheses"
BRIEFS = JOURNAL / "Briefs"

# --- Time --------------------------------------------------------------------
# Every timestamp is stored in UTC (markets are in different time zones, so this avoids confusion)
# but the brief shows Thai time, because the reader is in Thailand
UTC = ZoneInfo("UTC")
LOCAL_TZ = ZoneInfo("Asia/Bangkok")

# --- Universe ----------------------------------------------------------------
# This set describes market conditions; it is not the universe for factor research
# The real universe for quant work comes in phase 1; for now this is enough for an overview

BENCHMARKS: dict[str, str] = {
    "SPY": "S&P 500",
    "QQQ": "Nasdaq 100",
    "IWM": "Russell 2000 (หุ้นเล็ก)",
    "AGG": "US Aggregate Bond",
    "TLT": "US Treasury 20y+",
    "GLD": "ทองคำ",
    "UUP": "ดอลลาร์ (DXY proxy)",
    "BTC-USD": "Bitcoin",
    "^VIX": "VIX (ความกลัว)",
}

SECTORS: dict[str, str] = {
    "XLK": "Technology",
    "XLF": "Financials",
    "XLE": "Energy",
    "XLV": "Health Care",
    "XLI": "Industrials",
    "XLY": "Consumer Discretionary",
    "XLP": "Consumer Staples",
    "XLU": "Utilities",
    "XLB": "Materials",
    "XLRE": "Real Estate",
    "XLC": "Communication Services",
}

# Representative stocks for each sector; this structure does two jobs at once:
# 1. it says which stocks to search news for
# 2. it maps stock-level news to a sector, to aggregate into a cross-section later
#
# Why all 11 sectors: cross-sectional work **ranks** many stocks
# on the same day and buys the top group and sells the bottom; with news for only 3 sectors, 8 slots are empty
# and no ranking is possible; the original set had only XLK/XLC/XLY and hit this limit
#
# And why it is urgent: news history cannot be collected later, so every incomplete day is data lost for good
# Price and factor history can always be fetched later, so they are less urgent
SECTOR_MEMBERS: dict[str, list[str]] = {
    "XLK": ["NVDA", "MSFT", "AAPL", "AVGO"],   # Technology
    "XLC": ["GOOGL", "META"],                  # Communication Services
    "XLY": ["AMZN", "TSLA"],                   # Consumer Discretionary
    "XLF": ["JPM", "BAC"],                     # Financials
    "XLE": ["XOM", "CVX"],                     # Energy
    "XLV": ["JNJ", "UNH"],                     # Health Care
    "XLI": ["CAT", "GE"],                      # Industrials
    "XLP": ["PG", "KO"],                       # Consumer Staples
    "XLU": ["NEE", "DUK"],                     # Utilities
    "XLB": ["LIN", "SHW"],                     # Materials
    "XLRE": ["PLD", "AMT"],                    # Real Estate
}

WATCHLIST: list[str] = sorted({t for members in SECTOR_MEMBERS.values() for t in members})

# Reverse map, used to aggregate stock-level news to sector level
TICKER_SECTOR: dict[str, str] = {
    ticker: sector for sector, members in SECTOR_MEMBERS.items() for ticker in members
}

# Tickers that happen to be ordinary English words: **never match news on the ticker**
# match only on the company names in COMPANY_ALIASES
# CAT = a cat, KO = a knockout, GE/LIN = parts of other words, PG = a film rating or PG&E
# AMT = alternative minimum tax (common in financial news), NEE = née
# META is not listed because word boundaries already solve "Metal/Metaverse"
# and in financial news a bare "Meta" almost always means the company
AMBIGUOUS_SYMBOLS: set[str] = {"CAT", "KO", "GE", "LIN", "PG", "AMT", "NEE"}


def all_tickers() -> list[str]:
    """Every ticker to fetch prices for, deduplicated and sorted so results are deterministic"""
    return sorted({*BENCHMARKS, *SECTORS, *WATCHLIST})


# --- Paper ledger ------------------------------------------------------------
# Baseline strategies that always run in parallel, as a comparison line nobody can argue with
# (see the Journal note "10 - Background Track", BG-2)

LEDGER_START_CAPITAL = 100_000.0

# A ticker with fewer price rows than this (about 4 years) counts as never backfilled, and gets 10 years automatically
PRICE_BACKFILL_MIN_ROWS = 1000

# 12-1 momentum picks from this group (classic sector rotation)
MOMENTUM_POOL: list[str] = list(SECTORS)
MOMENTUM_TOP_N = 3

# --- News --------------------------------------------------------------------
# The GDELT DOC 2.0 API is free and needs no API key, which suits day one
# but it asks for at most 1 request every 5 seconds, so we throttle
GDELT_ENDPOINT = "https://api.gdeltproject.org/api/v2/doc/doc"
GDELT_MAX_RECORDS = 250          # Ask for many results at once, so fewer requests are needed
GDELT_TIMEOUT_SEC = 45

# Lesson from real tests: GDELT is much stricter than its documentation says
# frequent requests get 429 even 6 seconds apart, and once limited you stay in the "penalty box" for a while
# so keep a wide gap; better than being banned and getting no data at all
GDELT_MIN_INTERVAL_SEC = 15.0

# Time limit for news collection: a job called by cron must always finish in a predictable time
# When it runs out, keep what was collected and stop (collect again tomorrow; nothing critical is lost)
GDELT_TIME_BUDGET_SEC = 240.0

# After a 429, retry only once, because this is a daily job
# missing one day does less harm than a job hanging for half an hour
GDELT_RETRY_ON_LIMIT = 1
GDELT_BACKOFF_SEC = 30.0

# Combine several terms into one query with OR to cut the number of requests
# (GDELT counts the rate limit per request, not per result)
NEWS_QUERIES: list[str] = [
    '("federal reserve" OR "interest rate" OR inflation OR CPI) (stocks OR market)',
    '("stock market" OR "S&P 500" OR Nasdaq) (rally OR selloff OR record OR correction)',
    '(earnings OR guidance OR outlook) (beat OR miss OR cut OR raised) stock',
]


# --- News sources: which ones are enabled ------------------------------------------
# Ordered from fastest and most reliable to slowest
# To drop a source, remove its name from this list
#
# The public edition has no yahoo or gnews source, because robots.txt of query1/query2.finance.yahoo.com
# and news.google.com does not allow bots (checked 2026-11-25); see the data sources section of the README
NEWS_SOURCES: list[str] = ["rss", "sec", "gdelt"]

# Company names that news uses instead of tickers, for matching headlines to stocks
# Matched on word boundaries (\b), not substrings; see tag_tickers()
# For tickers in AMBIGUOUS_SYMBOLS this is the **only** way news gets matched,
# so it must include every spelling seen in practice (with and without &, with and without hyphens)
COMPANY_ALIASES: dict[str, str] = {
    # Technology
    "Nvidia": "NVDA",
    "Microsoft": "MSFT",
    "Apple": "AAPL",
    "Broadcom": "AVGO",
    # Communication Services
    "Google": "GOOGL",
    "Alphabet": "GOOGL",
    "Meta": "META",
    "Facebook": "META",
    # Consumer Discretionary
    "Amazon": "AMZN",
    "Tesla": "TSLA",
    # Financials
    "JPMorgan": "JPM",
    "JP Morgan": "JPM",
    "Bank of America": "BAC",
    # Energy
    "Exxon": "XOM",
    "ExxonMobil": "XOM",
    "Exxon Mobil": "XOM",
    "Chevron": "CVX",
    # Health Care
    "Johnson & Johnson": "JNJ",
    "Johnson and Johnson": "JNJ",
    "UnitedHealth": "UNH",
    "United Health": "UNH",
    # Industrials
    "Caterpillar": "CAT",
    "General Electric": "GE",
    "GE Aerospace": "GE",
    # Consumer Staples
    "Procter & Gamble": "PG",
    "Procter and Gamble": "PG",
    "Coca-Cola": "KO",
    "Coca Cola": "KO",
    # Utilities
    "NextEra": "NEE",
    "Duke Energy": "DUK",
    # Materials
    "Linde": "LIN",
    "Sherwin-Williams": "SHW",
    "Sherwin Williams": "SHW",
    # Real Estate
    "Prologis": "PLD",
    "American Tower": "AMT",
}

# --- RSS ---------------------------------------------------------------------
# No API key, almost no rate limit, and a standard that has not changed in 20 years
# kind="primary" marks an original source (the actual announcement), not a journalist's report
RSS_FEEDS: list[dict[str, str]] = [
    {"name": "cnbc", "kind": "outlet",
     "url": "https://www.cnbc.com/id/100003114/device/rss/rss.html"},
    {"name": "marketwatch", "kind": "outlet",
     "url": "https://feeds.content.dowjones.io/public/rss/mw_topstories"},
    {"name": "federalreserve", "kind": "primary",
     "url": "https://www.federalreserve.gov/feeds/press_all.xml"},
]
RSS_MAX_PER_FEED = 40

# --- SEC EDGAR ---------------------------------------------------------------
# The SEC requires a User-Agent of "name email" with a reachable address, otherwise HTTP 403
# Set it yourself through an environment variable, e.g. export FINBG_SEC_USER_AGENT="your-project you@your-domain.com"
SEC_USER_AGENT = os.environ.get("FINBG_SEC_USER_AGENT", "").strip()
SEC_MIN_INTERVAL_SEC = 0.15      # The SEC allows about 10 requests per second; we keep a margin

# Only forms that report real events
# Forms 3/4/5 (insider trades) are left out because they are numerous and mostly pre-planned
SEC_FORMS: set[str] = {"8-K", "10-Q", "10-K", "S-1", "DEF 14A", "SC 13D", "SC 13G"}

# 8-K items: they say what a filing is about without opening it
# 8-K headlines used to be just "[8-K] TSLA — 8-K", so labelling had to guess neutral
# even though the SEC already gives the items in the submissions JSON
#
# Severity levels (used by the watch):
# critical: an event that changes the picture of the company at once; replan
# high: important; review it if we hold the stock
# info: logged for the weekly plan to read; no need to wake anyone
SEC_8K_ITEMS: dict[str, tuple[str, str]] = {
    "1.01": ("ทำสัญญาสำคัญ", "info"),
    "1.02": ("ยกเลิกสัญญาสำคัญ", "high"),
    "1.03": ("ล้มละลาย/พิทักษ์ทรัพย์", "critical"),
    "1.04": ("เหตุด้านความปลอดภัยเหมือง", "info"),
    "1.05": ("เหตุการณ์ไซเบอร์ร้ายแรง", "critical"),
    "2.01": ("ซื้อ/ขายกิจการเสร็จสิ้น", "high"),
    "2.02": ("ประกาศผลประกอบการ", "info"),
    "2.03": ("ก่อหนี้สำคัญ", "info"),
    "2.04": ("เหตุผิดนัด/เร่งชำระหนี้", "critical"),
    "2.05": ("ปรับโครงสร้าง/ปลดคน", "high"),
    "2.06": ("ด้อยค่าสินทรัพย์", "high"),
    "3.01": ("ถูกเตือนเรื่องเพิกถอนหุ้น", "critical"),
    "3.02": ("ขายหุ้นเพิ่มทุนแบบไม่จดทะเบียน", "info"),
    "3.03": ("แก้สิทธิของผู้ถือหลักทรัพย์", "high"),
    "4.01": ("เปลี่ยนผู้สอบบัญชี", "high"),
    "4.02": ("งบเดิมเชื่อถือไม่ได้ (ต้องแก้ย้อนหลัง)", "critical"),
    "5.01": ("เปลี่ยนผู้ควบคุมบริษัท", "high"),
    "5.02": ("ผู้บริหาร/กรรมการเข้า-ออก", "high"),
    "5.03": ("แก้ข้อบังคับบริษัท", "info"),
    "5.07": ("ผลโหวตผู้ถือหุ้น", "info"),
    "7.01": ("เปิดเผยข้อมูลตาม Reg FD", "info"),
    "8.01": ("เหตุการณ์อื่น", "info"),
    "9.01": ("เอกสารแนบ", "info"),
}


def watchlist_news_query() -> str:
    """Combine the whole watchlist into one query instead of one request per ticker.

    8 tickers meant 8 requests at first; now it is 1 request.
    Which story belongs to which ticker is worked out afterwards from the headline (free).
    """
    tickers = " OR ".join(f'"{t}"' for t in WATCHLIST)
    return f"({tickers}) (stock OR shares OR earnings)"

USER_AGENT = "finbg-research/0.1 (personal research; contact via github)"

# --- Work queue (L4) ---------------------------------------------------------
# Reading list from the note "08 - LLM x Quant Frontier"
# Used as a safe place to spend spare tokens: not urgent, but never wasted
# and it can be done at any time later, so it sits at the bottom of the priority ladder

PAPERS: list[dict[str, str]] = [
    {"id": "finsaber", "title": "Can LLM-based Financial Investing Strategies Outperform the Market in Long Run?",
     "url": "https://arxiv.org/abs/2505.07078", "tier": "1"},
    {"id": "alphaforgebench", "title": "AlphaForgeBench: Benchmarking End-to-End Trading Strategy Design with LLMs",
     "url": "https://arxiv.org/abs/2602.18481", "tier": "1"},
    {"id": "lookahead-bench", "title": "Look-Ahead-Bench: Standardized Benchmark of Look-ahead Bias",
     "url": "https://arxiv.org/pdf/2601.13770", "tier": "1"},
    {"id": "rd-agent-quant", "title": "R&D-Agent-Quant: Multi-Agent Framework for Factor/Model Joint Optimization",
     "url": "https://arxiv.org/abs/2505.15155", "tier": "2"},
    {"id": "reliable-eval", "title": "Toward Reliable Evaluation of LLM-Based Financial Multi-Agent Systems",
     "url": "https://arxiv.org/pdf/2603.27539", "tier": "2"},
    {"id": "self-driving-portfolio", "title": "The Self Driving Portfolio: Agentic Architecture for Asset Management",
     "url": "https://arxiv.org/pdf/2604.02279", "tier": "2"},
    {"id": "chain-of-alpha", "title": "Chain-of-Alpha: LLMs for Alpha Mining in Quantitative Trading",
     "url": "https://arxiv.org/abs/2508.06312", "tier": "3"},
    {"id": "quantaalpha", "title": "QuantaAlpha: An Evolutionary Framework for LLM-Driven Alpha Mining",
     "url": "https://arxiv.org/pdf/2602.07085", "tier": "3"},
    {"id": "alphaagent", "title": "AlphaAgent: LLM-Driven Alpha Mining with Regularized Exploration",
     "url": "https://arxiv.org/html/2502.16789v2", "tier": "3"},
    {"id": "text-to-alpha", "title": "From Text to Alpha: Can LLMs Track Evolving Signals in Corporate Disclosures?",
     "url": "https://arxiv.org/abs/2510.03195", "tier": "3"},
    {"id": "efs", "title": "EFS: Evolutionary Factor Searching for Sparse Portfolio Optimization",
     "url": "https://arxiv.org/pdf/2507.17211", "tier": "3"},
]

PAPER_NOTES = JOURNAL / "Papers"

# --- Labeling ----------------------------------------------------------------
# The model meant to label the whole corpus, recorded so drift can be checked
# Consistency matters more than top quality: a corpus that mixes models has seams
# that look like real market signals when they are only a change of model
LABEL_MODEL = "claude-opus-5"

# The intended effort level: a value the maintainer declares, not one the model reports
# (a model usually does not know what effort the session was set to; asking it to guess gives junk)
# medium is enough for labelling with a clear schema and uses less quota than high
LABEL_EFFORT = "medium"

# Filter before labelling, for reasons of volume:
# cron collects about 546 items a day, about 16,000 a month, which no model can keep up with
# Filtering down to about 67 a day (about 2,000 a month) makes it sustainable **and improves the corpus**,
# because what gets cut is clickbait and announcements unrelated to our universe
#
# Criterion: an original document (SEC filing, central bank release) **or** a confirmed ticker
# Raw news is still stored in full; only the labelling layer is filtered
LABEL_HIGH_SIGNAL_ONLY = True
LABEL_PRIMARY_TYPES: set[str] = {"primary"}

# Quota per (publication date, sector): 11 sectors plus a macro bucket = at most about 72 items a day
#
# Fixes a problem measured in the real corpus: in the first 105 labels, AMZN/MSFT/AAPL/META took 67 of 98 slots
# Even with tickers for all 11 sectors, big tech news still outnumbers the other groups several times over
# Without a cap the corpus becomes "big tech news", not a cross-section of the market
#
# A ranking model needs **balance** more than volume: 6 items from each of 12 groups
# are worth more than 72 items from 3 groups, though the count is the same
#
# Set to 0 to turn the quota off (the old behaviour: label everything that passes the filter)
LABEL_SECTOR_QUOTA = 6


# --- Google News RSS ---------------------------------------------------------
# Not in the public edition: news.google.com does not allow bots (robots.txt, checked 2026-11-25)

# --- Earnings calendar and analyst estimates --------------------------------------
# yfinance Ticker.calendar gives the next earnings date plus consensus EPS and revenue estimates
# Point-in-time estimate history **cannot be obtained for free** (it is overwritten daily)
# so a snapshot is stored every day: the raw material for a future "earnings surprise" factor
RAW_CALENDAR = RAW / "calendar"

# --- Financial statements (SEC XBRL companyfacts) ----------------------------------------
# Every figure carries its real filing date (filed), a built-in knowledge_date, so there is no look-ahead
# The SEC keeps the full history, so the raw JSON (4 to 8 MB per company) is not stored
# Only the concepts in use are kept, in one small table per round
RAW_FUNDAMENTALS = RAW / "fundamentals"
FUNDAMENTALS_REFRESH_DAYS = 6

# Companies that changed the filing entity (CIK): the history sits under the old CIK, so fetch both and merge
# XOM: restructured in 2026 into the holding company "ExxonMobil Holdings Corp" (CIK 0002115436),
# which has filed only one 10-Q; the whole history is under Exxon Mobil Corporation (CIK 0000034088)
# Found 2026-10-24: XOM's financials row was empty because the ticker now pointed to the new CIK
SEC_PREDECESSOR_CIKS: dict[str, list[str]] = {
    "XOM": ["0000034088"],
}   # Refetch when older than this, or when a new 10-Q/10-K for the watchlist arrives
FUNDAMENTAL_FORMS: set[str] = {"10-Q", "10-K", "10-Q/A", "10-K/A", "10-KT"}

# metric -> concept names tried in order (companies use different names; banks differ the most)
FUNDAMENTAL_CONCEPTS: dict[str, list[tuple[str, str, str]]] = {
    # For revenue, pick "the largest among the fresh ones" (see fundamentals._pick_concept)
    # because some companies tag both the total and a subtotal, e.g. AMT: Revenues = total,
    # RevenueFromContract... = services only; a total can never be smaller than a part
    "revenue": [
        ("us-gaap", "Revenues", "USD"),
        ("us-gaap", "RevenueFromContractWithCustomerExcludingAssessedTax", "USD"),
        ("us-gaap", "RevenuesNetOfInterestExpense", "USD"),
        ("us-gaap", "RevenueFromContractWithCustomerIncludingAssessedTax", "USD"),
        ("us-gaap", "RegulatedAndUnregulatedOperatingRevenue", "USD"),
        ("us-gaap", "SalesRevenueNet", "USD"),
    ],
    "net_income": [
        ("us-gaap", "NetIncomeLoss", "USD"),
        ("us-gaap", "NetIncomeLossAvailableToCommonStockholdersBasic", "USD"),
        ("us-gaap", "ProfitLoss", "USD"),
    ],
    "eps_diluted": [("us-gaap", "EarningsPerShareDiluted", "USD/shares")],
    "operating_income": [("us-gaap", "OperatingIncomeLoss", "USD")],
    "gross_profit": [("us-gaap", "GrossProfit", "USD")],
    "cfo": [("us-gaap", "NetCashProvidedByUsedInOperatingActivities", "USD")],
    "capex": [
        ("us-gaap", "PaymentsToAcquirePropertyPlantAndEquipment", "USD"),
        ("us-gaap", "PaymentsToAcquireProductiveAssets", "USD"),     # NVDA and AMZN use this name
    ],
    "equity": [
        ("us-gaap", "StockholdersEquity", "USD"),
        ("us-gaap", "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest", "USD"),
    ],
    "debt": [
        ("us-gaap", "LongTermDebt", "USD"),
        ("us-gaap", "LongTermDebtNoncurrent", "USD"),
    ],
    "cash": [
        ("us-gaap", "CashAndCashEquivalentsAtCarryingValue", "USD"),
        ("us-gaap", "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents", "USD"),
    ],
    "shares_out": [("dei", "EntityCommonStockSharesOutstanding", "shares")],
    "diluted_shares": [("us-gaap", "WeightedAverageNumberOfDilutedSharesOutstanding", "shares")],
}
# Metrics that are period totals (income statement, cash flow) must be converted to quarters before summing TTM
FUNDAMENTAL_FLOWS: set[str] = {
    "revenue", "net_income", "eps_diluted", "operating_income",
    "gross_profit", "cfo", "capex", "diluted_shares",
}

# --- Portfolio Manager: weekly plan (see note 11) -------------------------------
# The LLM does not trade: it writes a "plan" that code checks and runs as a paper portfolio
# Every rule lives here in code, not in the prompt, because the model cannot edit code but can argue with a prompt
PLANS = DATA / "plans"                 # Plans that passed the checks cannot be recreated later, so back them up
PLAN_REQUESTS = PLANS / "requests"     # The prompt actually sent each time: the record of what the plan knew
JOURNAL_PLANS = JOURNAL / "Plans"

# The model is pinned, for the same reason as LABEL_MODEL: switching models midway would leave a seam
# in the PM's track record that cannot be told apart from skill
PLAN_MODEL = "claude-opus-5-5"
PLAN_EFFORT = "high"                   # A once-a-week job, worth letting it think deeply
PLAN_TIMEOUT_SEC = 1800
PLAN_MAX_BUDGET_USD = 8.0              # Spending cap as a safeguard (on Pro it is an equivalent figure, not real money)
PLAN_MAX_ATTEMPTS = 2                  # A plan that fails the checks gets its errors back and one more try

# Plan window (Thai time): from Saturday morning, after Friday's close and once the data is in
# If the machine is off all weekend, the plan is issued at the first start-up (still before Monday's open)
PLAN_WINDOW_WEEKDAY = 5                # 5 = Saturday
PLAN_WINDOW_HOUR = 5

# --- Mandate: what the PM may change -----------------------------------------------
# The core is a fixed, backtestable rule; the LLM controls only the satellite
# That way the value added or lost by the LLM's decisions is measured directly against a control
# that holds the satellite passively (PM_PASSIVE_SATELLITE) on exactly the same rebalancing schedule
PM_CORE_WEIGHT = 0.70
PM_CORE_MIX: dict[str, float] = {"SPY": 0.60, "AGG": 0.40}
PM_PASSIVE_SATELLITE: dict[str, float] = {"SPY": 1.0}

# What the satellite may hold: index, bond and gold ETFs, sector ETFs, and watchlist stocks
PM_BROAD_ETFS: set[str] = {"SPY", "QQQ", "IWM", "AGG", "TLT", "GLD"}
PM_SATELLITE_UNIVERSE: list[str] = sorted({*PM_BROAD_ETFS, *SECTORS, *WATCHLIST})

# Cap per holding (as a share of the satellite); the satellite is 30% of the portfolio
# Index ETFs 100% (holding all SPY is allowed, which equals the control); sector ETFs 40% (12% of the portfolio)
# Single stocks 25% (7.5% of the portfolio), so one blow-up cannot sink the portfolio
PM_CAP_BROAD = 1.00
PM_CAP_SECTOR = 0.40
PM_CAP_STOCK = 0.25
PM_MAX_POSITIONS = 10

# Turnover per plan (one side = sum of the increases), to prevent the "action flipping" found in research
# 0.5 = half the satellite may change per plan (15% of the portfolio); changes below PM_MIN_CHANGE count as none
PM_MAX_TURNOVER = 0.50
PM_MIN_CHANGE = 0.02

# Trading cost (commission + spread + slippage) per traded value; a conservative estimate for ETFs and large caps
PM_COST_BPS = 10.0

# Predictions in the plan: at least one per plan, so calibration can be measured
PM_MIN_PREDICTIONS = 1
PM_MAX_PREDICTIONS = 5
PM_PREDICTION_MIN_DAYS = 5
PM_PREDICTION_MAX_DAYS = 92

# --- Alerts: watch for strong news and price moves -------------------------------------------
ALERTS = DATA / "alerts"
JOURNAL_ALERTS = JOURNAL / "Alerts"

# Price: z = today's return / 60-day daily volatility
ALERT_SHOCK_Z = 3.0
ALERT_SHOCK_MIN_MOVE = {"stock": 4.0, "etf": 2.0}     # Minimum %, so a very quiet stock does not get a high z from a tiny move
ALERT_MARKET_MOVE = 2.5                                # SPY moves more than this (%) in one day
ALERT_VIX_LEVEL = 30.0
ALERT_VIX_JUMP = 30.0                                  # VIX jumps more than this (%) in one day
ALERT_LABEL_MATERIALITY = 75                           # News labels that count as "strong"

# --- Violent moves: ask the LLM to investigate the cause (finbg/pm/volatility.py) ---------
# Runs once a day after data collection (morning.sh); 0 tokens when nothing is unusual
# z = return / normal volatility, where normal volatility comes from the 120 trading days before the event window
# (the latest 5 days are excluded, so an event does not inflate its own baseline until it looks normal)
# Both z and the minimum % must pass: a very quiet stock (e.g. KO) gets a high z from a 2% move that is not serious
VOL_LOOKBACK = 120
VOL_DAILY_Z = 3.5
VOL_DAILY_MIN_MOVE = {"stock": 5.0, "etf": 2.5}       # % in a single day
VOL_WEEK_Z = 3.0
VOL_WEEK_MIN_MOVE = {"stock": 10.0, "etf": 5.0}       # % cumulative over 5 trading days (catches a steady slide)
VOL_COOLDOWN_SESSIONS = 5          # The same stock is not investigated again within this window unless a stronger day comes
VOL_MAX_TICKERS = 6                # Per investigation (bundled into one session to save quota)
VOL_MAX_SESSIONS_PER_WEEK = 4
INVESTIGATIONS = DATA / "investigations"
JOURNAL_INVESTIGATIONS = JOURNAL / "Investigations"
INVESTIGATE_EFFORT = "high"

# Replan automatically on a critical or high alert, with a cap to stop a loop from burning quota
REPLAN_MAX_PER_WEEK = 2
REPLAN_MIN_HOURS_APART = 12

# --- Notifications ------------------------------------------------------------
# Default: desktop notifications only (notify-send); nothing leaves the machine
# For phone alerts: `uv run finbg notify setup` (ntfy.sh); messages then pass through a public server
# and anyone who knows the topic name can read them, so the user has to turn it on deliberately
NOTIFY_CONFIG = Path.home() / ".config" / "finbg" / "notify.json"
NTFY_SERVER = "https://ntfy.sh"


# --- Whole-market scan for overlooked opportunities (finbg/scan, note 12) ------------------------
# A 4-stage funnel: code collects the whole market, code scores, Sonnet screens, Opus digs deep
# Owner's choice (2026-10-25): all liquid US stocks, at most 5 deep dives a day, an idea portfolio separate from the PM
RAW_SCREENER = RAW / "screener"            # Price and market-cap snapshot of the whole market (cannot be collected later)
RAW_SEC_INDEX = RAW / "sec_index"          # Every SEC filing of the day
RAW_INSIDER = RAW / "insider"              # Parsed Form 4 transactions
RAW_EARNINGS = RAW / "earnings_calendar"   # Whole-market earnings calendar, actual and estimated EPS, number of analysts
OPPORTUNITIES = DATA / "opportunities"     # Ideas, control group, results: cannot be recreated later
JOURNAL_OPPORTUNITIES = JOURNAL / "Opportunities"

# Nasdaq screener and earnings calendar: not in the public edition, because robots.txt of api.nasdaq.com
# does not allow bots (checked 2026-11-25); see finbg/scan/universe.py and finbg/datasource.py

# Universe: stocks a retail investor can really trade; micro caps are cut (wide spreads eat the profit)
SCAN_MIN_MCAP = 300e6
SCAN_MIN_DOLLAR_VOLUME = 2e6               # 20-day median if available, otherwise that day's value
SCAN_BACKFILL_PER_RUN = 600                # Fetch price history for at most this many new tickers per round

SCAN_CANDIDATES = 30                       # Stage 1 -> stage 2 (read by Sonnet)
SCAN_DEEP_DIVES = 5                        # Stage 2 -> stage 3 (Opus deep dive): the budget the owner approved
TRIAGE_MODEL = "claude-sonnet-5-5"
TRIAGE_EFFORT = "medium"
DEEP_DIVE_EFFORT = "high"
INSIDER_LOOKBACK_DAYS = 10
INSIDER_CLUSTER_MIN_BUYERS = 2
INSIDER_CLUSTER_MIN_VALUE = 100_000        # Dollars, summed over everyone in the lookback
INSIDER_SINGLE_BIG_BUY = 250_000
INSIDER_HOLDER_BIG_BUY = 1_000_000         # Holders above 10% (funds) buying more in the market

# Idea portfolio (paper): every idea held equally for the same period, rules fixed before seeing results
IDEA_HOLD_SESSIONS = 20
IDEA_COST_BPS = 30.0                       # Round trip; mid and small caps cost more than large caps
IDEA_SMALL_CAP_BENCHMARK = "IWM"           # Market cap < IDEA_LARGE_CAP -> compare with IWM
IDEA_LARGE_CAP = 10e9
IDEA_LARGE_CAP_BENCHMARK = "SPY"
IDEA_GATE_MIN_RESOLVED = 100               # Decide whether there is an edge once this many ideas exist

# Secondary test (set 2026-11-08, before the first finalist reached 20 days around 2026-11-24; note 12)
# "Does Sonnet's triage score rank the finalists in line with the real outcome?" uses every finalist (about 30 a day), not just about 1 idea a day,
# so it is much more sensitive than the main test; it does not touch the main test's criteria
TRIAGE_IC_MIN_NAMES = 10                   # Per round; with fewer, the correlation is noise
TRIAGE_IC_GATE_SESSIONS = 60               # Decide once this many rounds have results (about 3 months)
TRIAGE_IC_EXTEND_SESSIONS = 120            # If positive at 60 but t < 2, keep collecting up to this many, then decide for the last time
TRIAGE_IC_NW_LAG = IDEA_HOLD_SESSIONS - 1  # Consecutive rounds overlap by 19 days, hence a Newey-West t

# --- Thai track (finbg/th, finlab/thai.py, note 13) ---------------------------------
# Owner's choice 2026-11-08: a Thai track fully separate from the US experiment (no shared criteria, no shared portfolio)
# Stage 1 = daily point-in-time collection (0 tokens), for the same reason as the background track: what cannot be collected later must start first
#
# Each site's rules were checked (2026-11-08):
# Yahoo (stock list and .BK prices)  the same source as the US side (not in the public edition)
# RSS from Thai stock media     robots.txt allows it; RSS is meant to be fetched; stored locally, not republished
# SET company announcements    ❌ not collected: SET's terms allow personal use, but there is no published automated channel
# (the official one is the paid SMART Marketplace), so this waits for the owner's decision
# Form 59 insider trades       ❌ not collected: sec.or.th blocks programs with a WAF, and robots.txt of market.sec.or.th forbids the idisc pages
RAW_TH = RAW / "th"
RAW_TH_UNIVERSE = RAW_TH / "universe"   # Every security on SET/mai on that day, giving a survivorship-free universe from today on
RAW_TH_PRICES = RAW_TH / "prices"       # About one month of daily bars for every Thai common stock
RAW_TH_NEWS = RAW_TH / "news"           # Thai stock news from RSS
TH_CALENDAR_TICKER = "PTT.BK"           # Days on which PTT has a price = SET trading days
TH_BAR_FINAL_HOUR = 17                  # Thai time: SET closes around 16:40 after the closing auction; before that, today's bar is not final
TH_PRICE_PERIOD = "1mo"                 # Daily snapshots one month back (they may overlap; the reader keeps the most recently fetched value)
TH_CHUNK = 50                           # Tickers per request, to lower the chance of hitting Yahoo's rate limit

# "Really tradable" for a retail investor; measured 2026-11-08: 835 companies, but half trade < ฿0.4M a day
TH_TRADABLE_MIN_VALUE = 5e6             # Baht per day, 60-day median; a ฿100k to ฿500k trade = 2 to 10% of a day's volume
TH_TRADABLE_MIN_PRICE = 2.0             # Below this, 1 tick (฿0.01) is >= 0.5% of the price
# SET tick table: (price below, tick size); 1 tick is roughly the minimum spread cost of one buy-and-sell round trip
TH_TICKS = [(2, 0.01), (5, 0.02), (10, 0.05), (25, 0.10), (100, 0.25), (200, 0.50), (400, 1.00), (float("inf"), 2.00)]
TH_COMMISSION_ROUND_TRIP = 0.003        # About 0.15% per side including VAT; depends on the broker

TH_NEWS_FEEDS: list[dict[str, str]] = [
    {"name": "kaohoon", "url": "https://www.kaohoon.com/feed"},
    {"name": "mitihoon", "url": "https://www.mitihoon.com/feed/"},
    {"name": "prachachat-finance", "url": "https://www.prachachat.net/finance/feed"},
    {"name": "infoquest", "url": "https://www.infoquest.co.th/feed"},
]
# Fetch once a day and page back through the feed (?paged=N) until older news appears; complete coverage without frequent jobs (the owner did not want hourly jobs)
TH_NEWS_LOOKBACK_HOURS = 30
TH_NEWS_MAX_PAGES = 40
TH_NEWS_PAGE_DELAY_SEC = 1.5            # Be polite to the source site
# Thai tickers that usually mean something else in news are stored as tickers_hint (unconfirmed), not tickers
# Picked from real headlines 2026-11-08: all 16 "AI" hits meant artificial intelligence, "CPI สหรัฐ" is US inflation, "JV AMC" is an asset management company
# META/BYD in Thai news almost always mean the foreign companies, not the small Thai stocks with the same tickers
TH_AMBIGUOUS_SYMBOLS = {"AI", "IT", "TH", "AMC", "CPI", "PLUS", "PM", "META", "BYD",
                        "ASIA", "GREEN", "POWER", "SOLAR", "CITY", "BIG", "WIN", "SUPER", "PLANET", "HYDRO", "KISS"}
# Indices: Yahoo gives only the latest value, no history (checked 2026-11-08), so we store them daily to build a history for context
# (not used as the idea portfolio's benchmark, because days with the machine off would be missing for good)
TH_INDEXES = ["^SET.BK", "^SET50.BK", "^SSET.BK", "^MAI.BK"]

# Owner's choice 2026-11-08: SET announcements -> use media news instead (C); Form 59 table -> try the InfoQuest article pages (D)
RAW_TH_ARTICLES = RAW_TH / "articles"   # Full article text fetched for reading (only deep-dive stocks and the Form 59 table)
RAW_TH_INSIDER = RAW_TH / "insider"     # Form 59 tables parsed into rows
TH_ARTICLE_TIMEOUT_SEC = 20
TH_ARTICLE_DELAY_SEC = 2.0              # At least this long between pages: read like a person, not a crawler
TH_ARTICLE_MAX_CHARS = 3000             # Per article sent to Opus
TH_FORM59_TITLE = "การเปลี่ยนแปลงการถือหลักทรัพย์ของผู้บริหาร"   # Headline of InfoQuest's daily Form 59 table
TH_FORM59_PER_RUN = 6                   # Fetch at most this many Form 59 pages per round (normally one a day)

# Thai scanner (finbg/th/scan.py, note 13): the same funnel as the US one but smaller (about 150 to 200 tradable stocks)
TH_OPPORTUNITIES = DATA / "opportunities_th"   # Thai idea portfolio, in files strictly separate from the US one
JOURNAL_TH = JOURNAL / "Thai"
TH_SCAN_TRADABLE_DAYS = 20              # Median traded value over the lookback (the daily store started 2026-11-08 and has only about a month)
TH_SCAN_CANDIDATES = 20                 # Stage 1 -> Sonnet (stocks with real events are usually fewer than this)
TH_SCAN_DEEP_DIVES = 2                  # Stage 2 -> Opus, as the owner agreed (Pro quota)
TH_SCAN_NEWS_FRESH_HOURS = 6            # News in the store must be fetched no longer ago than this, otherwise no scan (no decisions on old news)
TH_ARTICLES_PER_PICK = 3                # Fetch full article text for deep-dive stocks, at most this many each
TH_ATTENTION_DAYS = 5                   # "How much media attention" is measured by the number of news items in the period before this
TH_INSIDER_LOOKBACK_DAYS = 10
TH_INSIDER_CLUSTER_MIN_BUYERS = 2
TH_INSIDER_BIG_BUY_THB = 1_000_000
TH_INSIDER_SINGLE_BUY_THB = 200_000
TH_IDEA_HOLD_SESSIONS = 20
TH_IDEA_GATE_MIN_RESOLVED = 100
