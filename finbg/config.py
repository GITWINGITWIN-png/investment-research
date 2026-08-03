"""ค่าตั้งต้นทั้งหมดของ background track อยู่ที่ไฟล์เดียวนี้

เหตุผลที่รวมไว้ที่เดียว: เวลาปรับ universe หรือย้าย path จะได้ไม่ต้องไล่แก้หลายไฟล์
และเวลาอ่านย้อนหลังใน 6 เดือน จะได้รู้ทันทีว่าตอนนั้นตั้งค่าอะไรไว้
"""

from __future__ import annotations

from pathlib import Path
from zoneinfo import ZoneInfo

# --- Paths -------------------------------------------------------------------
# ทุก path อ้างอิงจาก root ของโปรเจกต์ เพื่อให้รันจาก cron ที่ไหนก็ได้
ROOT = Path(__file__).resolve().parent.parent

DATA = ROOT / "data"
RAW = DATA / "raw"                  # ข้อมูลดิบ ห้ามแก้ ห้ามเขียนทับ
DERIVED = DATA / "derived"          # ของที่คำนวณใหม่ได้เสมอ ลบทิ้งได้

RAW_PRICES = RAW / "prices"
RAW_NEWS = RAW / "news"
MANIFEST = RAW / "_manifest.jsonl"  # log ว่าดึงอะไรตอนไหน = knowledge_date

JOURNAL = ROOT / "Journal"          # โฟลเดอร์ที่ Obsidian อ่าน
PREDICTIONS = JOURNAL / "Predictions"
HYPOTHESES = JOURNAL / "Hypotheses"
BRIEFS = JOURNAL / "Briefs"

# --- Time --------------------------------------------------------------------
# เก็บทุก timestamp เป็น UTC เสมอ (ตลาดคนละ timezone กัน จะได้ไม่สับสน)
# แต่ตอนแสดงผลใน brief ใช้เวลาไทยเพราะคนอ่านคือคุณ
UTC = ZoneInfo("UTC")
LOCAL_TZ = ZoneInfo("Asia/Bangkok")

# --- Universe ----------------------------------------------------------------
# ชุดนี้คือ "สภาพตลาด" ไม่ใช่ universe สำหรับหา factor
# universe จริงสำหรับงาน quant จะมาตอนเฟส 1 — ตอนนี้เอาแค่พอเห็นภาพรวม

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

# หุ้นที่คุณสนใจเป็นพิเศษ — แก้ได้ตามใจ ไม่กระทบส่วนอื่น
WATCHLIST: list[str] = ["NVDA", "MSFT", "AAPL", "GOOGL", "AMZN", "META", "TSLA", "AVGO"]


def all_tickers() -> list[str]:
    """ทุก ticker ที่ต้องดึงราคา — ไม่ซ้ำ และเรียงเพื่อให้ผลลัพธ์ deterministic"""
    return sorted({*BENCHMARKS, *SECTORS, *WATCHLIST})


# --- Paper ledger ------------------------------------------------------------
# กลยุทธ์ baseline ที่รันคู่ขนานไปตลอด เพื่อเป็นเส้นเปรียบเทียบที่เถียงไม่ได้
# (ดู Journal note "10 - Background Track" → BG-2)

LEDGER_START_CAPITAL = 100_000.0

# 12-1 momentum จะเลือกจากกลุ่มนี้ (sector rotation แบบคลาสสิก)
MOMENTUM_POOL: list[str] = list(SECTORS)
MOMENTUM_TOP_N = 3

# --- News --------------------------------------------------------------------
# GDELT DOC 2.0 API ใช้ฟรี ไม่ต้องมี API key — เหมาะกับการเริ่มวันแรก
# แต่เขาขอให้ยิงไม่เกิน 1 request ทุก 5 วินาที เราจึงต้อง throttle
GDELT_ENDPOINT = "https://api.gdeltproject.org/api/v2/doc/doc"
GDELT_MAX_RECORDS = 250          # ขอทีละเยอะ ๆ จะได้ยิง request น้อยครั้ง
GDELT_TIMEOUT_SEC = 45

# บทเรียนจากการทดสอบจริง: GDELT เข้มกว่าที่เอกสารบอกมาก
# ยิงถี่แม้เว้น 6 วินาทีก็ยังโดน 429 และเมื่อโดนแล้วจะติด "penalty box" นานพอควร
# → เว้นห่างไว้ก่อน ดีกว่าโดนแบนแล้วไม่ได้ข้อมูลเลย
GDELT_MIN_INTERVAL_SEC = 15.0

# เพดานเวลาของงานเก็บข่าว — งานที่ cron เรียกต้องจบภายในเวลาที่คาดเดาได้เสมอ
# ถ้าเกิน ให้เก็บเท่าที่ได้แล้วจบ (พรุ่งนี้ค่อยเก็บใหม่ ไม่ใช่เรื่องคอขาดบาดตาย)
GDELT_TIME_BUDGET_SEC = 240.0

# โดน 429 แล้วลองใหม่แค่ครั้งเดียว — เพราะนี่คืองานรายวัน
# พลาดไปวันนึงเสียหายน้อยกว่าการปล่อยให้ job ค้างครึ่งชั่วโมง
GDELT_RETRY_ON_LIMIT = 1
GDELT_BACKOFF_SEC = 30.0

# รวมหลายคำเข้าเป็น query เดียวด้วย OR เพื่อลดจำนวน request
# (GDELT คิด rate limit ต่อ request ไม่ใช่ต่อจำนวนผลลัพธ์)
NEWS_QUERIES: list[str] = [
    '("federal reserve" OR "interest rate" OR inflation OR CPI) (stocks OR market)',
    '("stock market" OR "S&P 500" OR Nasdaq) (rally OR selloff OR record OR correction)',
    '(earnings OR guidance OR outlook) (beat OR miss OR cut OR raised) stock',
]


# --- แหล่งข่าว: เปิดใช้แหล่งไหนบ้าง ------------------------------------------
# เรียงจากเร็ว/เชื่อถือได้มากที่สุด ไปหาช้าที่สุด
# ตัดแหล่งไหนออกก็แค่ลบชื่อออกจาก list นี้
NEWS_SOURCES: list[str] = ["yahoo", "rss", "sec", "gdelt"]

# ชื่อบริษัทที่ข่าวมักใช้แทน ticker — ใช้จับคู่ข่าวกับหุ้นจากพาดหัว
COMPANY_ALIASES: dict[str, str] = {
    "Nvidia": "NVDA",
    "Microsoft": "MSFT",
    "Apple": "AAPL",
    "Google": "GOOGL",
    "Alphabet": "GOOGL",
    "Amazon": "AMZN",
    "Meta": "META",
    "Facebook": "META",
    "Tesla": "TSLA",
    "Broadcom": "AVGO",
}

# --- RSS ---------------------------------------------------------------------
# ไม่ต้องมี API key แทบไม่มี rate limit และมาตรฐานไม่เปลี่ยนมา 20 ปี
# kind="primary" คือแหล่งต้นทาง (ประกาศจริง) ไม่ใช่การรายงานของนักข่าว
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
# SEC บังคับให้ User-Agent เป็น "ชื่อ อีเมล" ที่ติดต่อได้จริง ไม่งั้น HTTP 403
SEC_USER_AGENT = "finbg-research you@example.com"
SEC_MIN_INTERVAL_SEC = 0.15      # SEC อนุญาต ~10 req/s เราเว้นเผื่อไว้

# เอาเฉพาะฟอร์มที่บอก "เหตุการณ์" จริง
# ตัด Form 3/4/5 (ซื้อขายของผู้บริหาร) ออกเพราะมีเยอะและส่วนใหญ่ตามแผนที่ตั้งไว้ล่วงหน้า
SEC_FORMS: set[str] = {"8-K", "10-Q", "10-K", "S-1", "DEF 14A", "SC 13D", "SC 13G"}


def watchlist_news_query() -> str:
    """รวม watchlist ทั้งหมดเป็น query เดียว แทนที่จะยิงทีละตัว

    8 ticker = 8 requests ตอนแรก → เหลือ 1 request
    เราจับคู่ว่าข่าวไหนเกี่ยวกับ ticker ไหนทีหลังจากพาดหัวได้เอง (ฟรี)
    """
    tickers = " OR ".join(f'"{t}"' for t in WATCHLIST)
    return f"({tickers}) (stock OR shares OR earnings)"

USER_AGENT = "finbg-research/0.1 (personal research; contact via github)"

# --- Work queue (L4) ---------------------------------------------------------
# รายการอ่านจากโน้ต "08 - LLM x Quant Frontier"
# ใช้เป็น "ที่ทิ้ง token" ที่ปลอดภัย: ไม่เร่งด่วน แต่ไม่มีวันเสียเปล่า
# และเป็นงานที่ทำย้อนหลังได้ → จึงอยู่ท้ายบันไดความสำคัญ

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
# โมเดลที่ตั้งใจใช้ label ทั้งคลัง — บันทึกไว้เพื่อให้ตรวจ drift ได้
# ความสม่ำเสมอสำคัญกว่าคุณภาพสูงสุด: คลังที่ปนหลายโมเดลจะมีรอยต่อ
# ที่หน้าตาเหมือนสัญญาณตลาดจริง ทั้งที่เป็นแค่การเปลี่ยนโมเดล
LABEL_MODEL = "claude-opus-5"

# ระดับ effort ที่ตั้งใจใช้ — เป็นค่าที่ "ผู้ดูแลประกาศ" ไม่ใช่ค่าที่โมเดลรายงานเอง
# (โมเดลมักไม่รู้แน่ชัดว่า session ตั้ง effort ไว้เท่าไหร่ การให้เดาจะได้ข้อมูลขยะ)
# medium เพียงพอสำหรับงาน label ที่มี schema ชัดเจน และประหยัดโควตากว่า high
LABEL_EFFORT = "medium"

# กรองก่อน label — เหตุผลเชิงปริมาณ:
# cron เก็บข่าว ~546 รายการ/วัน = ~16,000/เดือน ซึ่ง label ไม่ทันไม่ว่าจะใช้โมเดลไหน
# การกรองเหลือ ~67/วัน (~2,000/เดือน) ทำให้ยั่งยืน **และคลังคุณภาพดีขึ้น**
# เพราะที่ตัดออกคือข่าวชวนคลิกและประกาศที่ไม่เกี่ยวกับ universe ของเรา
#
# เกณฑ์: เป็นเอกสารต้นทาง (SEC filing, ประกาศธนาคารกลาง) **หรือ** ยืนยัน ticker ได้
# ข่าวดิบยังเก็บครบเหมือนเดิม — กรองเฉพาะชั้น label เท่านั้น
LABEL_HIGH_SIGNAL_ONLY = True
LABEL_PRIMARY_TYPES: set[str] = {"primary"}
