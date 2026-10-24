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

# หุ้นตัวแทนของแต่ละ sector — โครงสร้างนี้ทำสองหน้าที่พร้อมกัน
#   1. บอกว่าจะไปค้นข่าวของหุ้นตัวไหน
#   2. เป็นแผนที่ข่าวรายตัว → sector เพื่อยุบเป็น cross-section ทีหลัง
#
# ทำไมต้องครบทั้ง 11 sector: งาน cross-sectional คือการ **จัดอันดับ** หลายตัว
# ในวันเดียวกันแล้วซื้อกลุ่มบนขายกลุ่มล่าง ถ้ามีข่าวแค่ 3 sector อีก 8 ช่องว่าง
# ก็จัดอันดับไม่ได้ — เดิมมีแค่ XLK/XLC/XLY จึงติดข้อจำกัดนี้
#
# และเหตุผลที่ต้องรีบ: ข่าวย้อนหลังเก็บไม่ได้ ทุกวันที่ยังไม่ครบ = ข้อมูลหายถาวร
# ส่วนฝั่งราคา/factor ย้อนหลังได้ตลอด จึงไม่ต้องรีบเท่า
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

# แผนที่ย้อนกลับ — ใช้ยุบข่าวรายตัวขึ้นไปเป็นระดับ sector
TICKER_SECTOR: dict[str, str] = {
    ticker: sector for sector, members in SECTOR_MEMBERS.items() for ticker in members
}

# ตัวย่อที่บังเอิญเป็นคำปกติในภาษาอังกฤษ — **ห้ามจับข่าวจากตัวย่อ**
# ให้จับจากชื่อบริษัทใน COMPANY_ALIASES อย่างเดียว
#   CAT = แมว · KO = น็อกเอาต์ · GE/LIN = ส่วนของคำอื่น · PG = เรตหนัง/PG&E
#   AMT = alternative minimum tax (คำที่โผล่ในข่าวการเงินบ่อย) · NEE = née
# ไม่ใส่ META เพราะขอบเขตคำแก้ปัญหา "Metal/Metaverse" ได้แล้ว
# และในข่าวการเงิน คำว่า Meta เดี่ยว ๆ แทบจะหมายถึงบริษัทเสมอ
AMBIGUOUS_SYMBOLS: set[str] = {"CAT", "KO", "GE", "LIN", "PG", "AMT", "NEE"}


def all_tickers() -> list[str]:
    """ทุก ticker ที่ต้องดึงราคา — ไม่ซ้ำ และเรียงเพื่อให้ผลลัพธ์ deterministic"""
    return sorted({*BENCHMARKS, *SECTORS, *WATCHLIST})


# --- Paper ledger ------------------------------------------------------------
# กลยุทธ์ baseline ที่รันคู่ขนานไปตลอด เพื่อเป็นเส้นเปรียบเทียบที่เถียงไม่ได้
# (ดู Journal note "10 - Background Track" → BG-2)

LEDGER_START_CAPITAL = 100_000.0

# ticker ที่มีราคาน้อยกว่านี้ (≈4 ปี) ถือว่า "ยังไม่เคยดึงย้อนหลัง" → ดึง 10 ปีให้อัตโนมัติ
PRICE_BACKFILL_MIN_ROWS = 1000

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
#
# yahoo ถูกปิด (2026-10-23): log รายวันแสดง "yahoo → 0 รายการ" มาตลอด
# ทดสอบแล้วว่า Ticker.news คืน list ว่างทั้ง yfinance 1.5.2 และ 1.7.0 (รุ่นล่าสุด)
# = Yahoo เปลี่ยนฝั่งเซิร์ฟเวอร์ ไม่ใช่บั๊กของเรา โค้ดยังเก็บไว้ เปิดใหม่ได้ถ้ากลับมาใช้ได้
# แทนด้วย gnews (Google News RSS) ที่ค้นรายบริษัท ไม่ต้องมี API key
NEWS_SOURCES: list[str] = ["rss", "sec", "gnews", "gdelt"]

# ชื่อบริษัทที่ข่าวมักใช้แทน ticker — ใช้จับคู่ข่าวกับหุ้นจากพาดหัว
# จับด้วยขอบเขตคำ (\b) ไม่ใช่ substring — ดู tag_tickers()
# สำหรับ ticker ใน AMBIGUOUS_SYMBOLS นี่คือ**ทางเดียว**ที่จะจับข่าวได้
# จึงต้องใส่ให้ครบทุกวิธีเขียนที่พบจริง (มี/ไม่มี &, มี/ไม่มียัติภังค์)
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

# หัวข้อของ 8-K (Item) — ตัวบอกว่า filing นั้น "เรื่องอะไร" โดยไม่ต้องเปิดอ่าน
# เดิมพาดหัว 8-K เป็นแค่ "[8-K] TSLA — 8-K" ทำให้ตอน label ต้องเดาว่าเป็น neutral
# ทั้งที่ SEC บอกหัวข้อมาให้แล้วในฟิลด์ items ของ submissions JSON
#
# ระดับความรุนแรง (ใช้ตอนเฝ้าระวัง):
#   critical — เหตุการณ์ที่เปลี่ยนภาพบริษัททันที ต้องวางแผนใหม่
#   high     — สำคัญ ควรทบทวนถ้าถือหุ้นตัวนั้นอยู่
#   info     — บันทึกไว้ให้แผนรายสัปดาห์อ่าน ไม่ต้องปลุกใคร
SEC_8K_ITEMS: dict[str, tuple[str, str]] = {
    "1.01": ("ทำสัญญาสำคัญ", "info"),
    "1.02": ("ยกเลิกสัญญาสำคัญ", "high"),
    "1.03": ("ล้มละลาย/พิทักษ์ทรัพย์", "critical"),
    "1.05": ("เหตุการณ์ไซเบอร์ร้ายแรง", "critical"),
    "2.01": ("ซื้อ/ขายกิจการเสร็จสิ้น", "high"),
    "2.02": ("ประกาศผลประกอบการ", "info"),
    "2.03": ("ก่อหนี้สำคัญ", "info"),
    "2.04": ("เหตุผิดนัด/เร่งชำระหนี้", "critical"),
    "2.05": ("ปรับโครงสร้าง/ปลดคน", "high"),
    "2.06": ("ด้อยค่าสินทรัพย์", "high"),
    "3.01": ("ถูกเตือนเรื่องเพิกถอนหุ้น", "critical"),
    "3.02": ("ขายหุ้นเพิ่มทุนแบบไม่จดทะเบียน", "info"),
    "4.01": ("เปลี่ยนผู้สอบบัญชี", "high"),
    "4.02": ("งบเดิมเชื่อถือไม่ได้ (ต้องแก้ย้อนหลัง)", "critical"),
    "5.02": ("ผู้บริหาร/กรรมการเข้า-ออก", "high"),
    "5.03": ("แก้ข้อบังคับบริษัท", "info"),
    "5.07": ("ผลโหวตผู้ถือหุ้น", "info"),
    "7.01": ("เปิดเผยข้อมูลตาม Reg FD", "info"),
    "8.01": ("เหตุการณ์อื่น", "info"),
    "9.01": ("เอกสารแนบ", "info"),
}


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

# โควตาต่อ (วันที่ข่าวออก, sector) — 11 sector + ถัง macro = สูงสุด ~72 ชิ้น/วัน
#
# แก้ปัญหาที่วัดได้จากคลังจริง: label 105 ใบแรก มี AMZN/MSFT/AAPL/META กิน 67 จาก 98 ครั้ง
# ต่อให้เพิ่ม ticker ครบ 11 sector แล้ว ข่าว big tech ก็ยังมีมากกว่ากลุ่มอื่นหลายเท่า
# ถ้าไม่กำหนดเพดาน คลังจะกลายเป็น "ข่าว big tech" ไม่ใช่ cross-section ของตลาด
#
# โมเดลจัดอันดับต้องการ **ความสมดุล** มากกว่าปริมาณ — 6 ชิ้นจาก 12 กลุ่ม
# มีค่ากว่า 72 ชิ้นที่มาจาก 3 กลุ่ม ทั้งที่จำนวนเท่ากัน
#
# ตั้งเป็น 0 เพื่อปิดโควตา (กลับไปพฤติกรรมเดิม: label ทุกชิ้นที่ผ่านตัวกรอง)
LABEL_SECTOR_QUOTA = 6


# --- Google News RSS (แทน Yahoo) ---------------------------------------------
# ค้นรายบริษัท: ("ชื่อบริษัท" OR TICKER) stock when:2d — ฟรี ไม่ต้องมี key
# จัดเป็น aggregator เพราะ Google รวบรวมมาจากหลายสำนัก (ลำดับความน่าเชื่อถือต่ำสุด)
# ลิงก์เป็นลิงก์ของ Google ที่ redirect ไปบทความจริง — คงที่ ใช้เป็น key ได้
GNEWS_ENDPOINT = "REMOVED in the public edition: news.google.com disallows automated access (robots.txt)"
GNEWS_MAX_PER_QUERY = 15        # 24 บริษัท × 15 = เพดาน 360 ชิ้น/วัน ก่อนตัดซ้ำ
GNEWS_MIN_INTERVAL_SEC = 1.5    # สุภาพกับเซิร์ฟเวอร์ — ไม่มีเอกสาร rate limit ทางการ
GNEWS_WINDOW = "2d"

# --- ปฏิทินงบ + ประมาณการของนักวิเคราะห์ --------------------------------------
# yfinance Ticker.calendar ให้วันประกาศงบครั้งถัดไป + ค่าเฉลี่ย EPS/รายได้ที่นักวิเคราะห์คาด
# ประมาณการแบบ point-in-time **ย้อนหลังหาฟรีไม่ได้** (ถูกเขียนทับทุกวัน)
# จึงเก็บ snapshot ทุกวัน — วัตถุดิบของ factor "earnings surprise" ในอนาคต
RAW_CALENDAR = RAW / "calendar"

# --- งบการเงิน (SEC XBRL companyfacts) ----------------------------------------
# ทุกตัวเลขมีวันที่ยื่นจริง (filed) = knowledge_date ในตัว → ไม่มี look-ahead
# SEC เก็บย้อนหลังทั้งหมด จึงไม่ต้องเก็บ JSON ดิบ (4–8 MB ต่อบริษัท)
# เก็บเฉพาะ concept ที่ใช้ ลงตารางเดียวต่อรอบ (เล็กมาก)
RAW_FUNDAMENTALS = RAW / "fundamentals"
FUNDAMENTALS_REFRESH_DAYS = 6

# บริษัทที่เปลี่ยนนิติบุคคลผู้ยื่น (CIK) — ประวัติงบอยู่ใต้ CIK เก่า ต้องดึงทั้งสองแล้วรวมกัน
# XOM: ปี 2026 ปรับโครงสร้างเป็นบริษัทโฮลดิ้ง "ExxonMobil Holdings Corp" (CIK 0002115436)
# ซึ่งเพิ่งยื่น 10-Q ฉบับเดียว — ประวัติทั้งหมดอยู่ที่ Exxon Mobil Corporation (CIK 0000034088)
# พบเมื่อ 2026-10-24: งบ XOM ว่างทั้งแถว เพราะ ticker ชี้ไป CIK ใหม่แล้ว
SEC_PREDECESSOR_CIKS: dict[str, list[str]] = {
    "XOM": ["0000034088"],
}   # ดึงใหม่เมื่อเก่ากว่านี้ หรือมี 10-Q/10-K ใหม่ของ watchlist
FUNDAMENTAL_FORMS: set[str] = {"10-Q", "10-K", "10-Q/A", "10-K/A", "10-KT"}

# metric → รายชื่อ concept ที่ลองตามลำดับ (บริษัทต่างกันใช้ชื่อต่างกัน — ธนาคารต่างมาก)
FUNDAMENTAL_CONCEPTS: dict[str, list[tuple[str, str, str]]] = {
    # revenue เลือก "ตัวที่ใหญ่ที่สุดในบรรดาที่สดอยู่" (ดู fundamentals._pick_concept)
    # เพราะบางบริษัทแท็กทั้งยอดรวมและยอดย่อย เช่น AMT: Revenues = รวม,
    # RevenueFromContract... = แค่ส่วนบริการ — ยอดรวมไม่มีทางเล็กกว่าส่วนย่อย
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
        ("us-gaap", "PaymentsToAcquireProductiveAssets", "USD"),     # NVDA, AMZN ใช้ชื่อนี้
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
# metric ที่เป็น "ยอดสะสมช่วงเวลา" (งบกำไรขาดทุน/กระแสเงินสด) — ต้องแปลงเป็นรายไตรมาสก่อนรวม TTM
FUNDAMENTAL_FLOWS: set[str] = {
    "revenue", "net_income", "eps_diluted", "operating_income",
    "gross_profit", "cfo", "capex", "diluted_shares",
}

# --- Portfolio Manager: แผนรายสัปดาห์ (ดูโน้ต 11) -------------------------------
# LLM ไม่ได้เทรด — มันเขียน "แผน" ที่โค้ดตรวจและนำไปรันเป็นพอร์ตกระดาษ
# กติกาทั้งหมดอยู่ที่นี่ในโค้ด ไม่ใช่ในพรอมต์ เพราะโมเดลแก้โค้ดไม่ได้แต่เถียงพรอมต์ได้
PLANS = DATA / "plans"                 # แผนที่ผ่านการตรวจ — สร้างย้อนหลังไม่ได้ ต้องสำรอง
PLAN_REQUESTS = PLANS / "requests"     # พรอมต์ที่ส่งจริงทุกครั้ง = หลักฐานว่าแผนรู้อะไร
JOURNAL_PLANS = JOURNAL / "Plans"

# โมเดลล็อกตายตัว เหตุผลเดียวกับ LABEL_MODEL: ถ้าเปลี่ยนโมเดลกลางทาง ผลงานของ PM
# จะมีรอยต่อที่แยกไม่ออกว่าเป็นฝีมือหรือเป็นแค่การเปลี่ยนสมอง
PLAN_MODEL = "claude-opus-5-5"
PLAN_EFFORT = "high"                   # งานสัปดาห์ละครั้ง คุ้มที่จะให้คิดลึก
PLAN_TIMEOUT_SEC = 1800
PLAN_MAX_BUDGET_USD = 8.0              # เพดานกันหลุด (บน Pro เป็นตัวเลขเทียบเท่า ไม่ได้ตัดเงินจริง)
PLAN_MAX_ATTEMPTS = 2                  # แผนไม่ผ่านการตรวจ → ส่งข้อผิดพลาดกลับให้แก้ได้อีก 1 ครั้ง

# ช่วงเวลาออกแผน (เวลาไทย): ตั้งแต่เช้าวันเสาร์ หลังตลาดวันศุกร์ปิดและข้อมูลเข้าแล้ว
# ถ้าเครื่องปิดทั้งสุดสัปดาห์ แผนจะออกตอนเปิดเครื่องครั้งแรก (ยังทันก่อนตลาดเปิดวันจันทร์)
PLAN_WINDOW_WEEKDAY = 5                # 5 = เสาร์
PLAN_WINDOW_HOUR = 5

# --- Mandate: กรอบที่ PM ขยับได้ -----------------------------------------------
# แกน (core) เป็นกฎตายตัวที่ทดสอบย้อนหลังได้ · LLM คุมเฉพาะส่วน satellite
# แบบนี้วัดได้ตรง ๆ ว่า "ส่วนที่ LLM ตัดสินใจ" เพิ่มหรือลดค่า โดยเทียบกับ control
# ที่ถือ satellite แบบเฉย ๆ (PM_PASSIVE_SATELLITE) บนตารางปรับพอร์ตเดียวกันทุกประการ
PM_CORE_WEIGHT = 0.70
PM_CORE_MIX: dict[str, float] = {"SPY": 0.60, "AGG": 0.40}
PM_PASSIVE_SATELLITE: dict[str, float] = {"SPY": 1.0}

# ตัวที่ satellite ถือได้ — ETF ดัชนี/พันธบัตร/ทอง + ETF รายหมวด + หุ้นใน watchlist
PM_BROAD_ETFS: set[str] = {"SPY", "QQQ", "IWM", "AGG", "TLT", "GLD"}
PM_SATELLITE_UNIVERSE: list[str] = sorted({*PM_BROAD_ETFS, *SECTORS, *WATCHLIST})

# เพดานต่อตัว (สัดส่วนของ satellite) — satellite = 30% ของพอร์ต
#   ETF ดัชนี 100% (= ถือ SPY ทั้งก้อนได้ = control) · ETF หมวด 40% (= 12% ของพอร์ต)
#   หุ้นรายตัว 25% (= 7.5% ของพอร์ต) — ตัวเดียวพังไม่ทำให้พอร์ตพัง
PM_CAP_BROAD = 1.00
PM_CAP_SECTOR = 0.40
PM_CAP_STOCK = 0.25
PM_MAX_POSITIONS = 10

# turnover ต่อแผน (ขาเดียว = ผลรวมของส่วนที่เพิ่มขึ้น) — กัน "action flipping" ที่งานวิจัยพบ
# 0.5 = เปลี่ยน satellite ได้ครึ่งเดียวต่อครั้ง (15% ของพอร์ต) · ต่ำกว่า PM_MIN_CHANGE ถือว่าไม่เปลี่ยน
PM_MAX_TURNOVER = 0.50
PM_MIN_CHANGE = 0.02

# ต้นทุนซื้อขาย (ค่าคอม + spread + slippage) ต่อมูลค่าที่ซื้อขาย — ETF/หุ้นใหญ่ ประมาณการแบบระมัดระวัง
PM_COST_BPS = 10.0

# คำทำนายในแผน — อย่างน้อย 1 ข้อทุกแผน เพื่อวัด calibration ได้
PM_MIN_PREDICTIONS = 1
PM_MAX_PREDICTIONS = 5
PM_PREDICTION_MIN_DAYS = 5
PM_PREDICTION_MAX_DAYS = 92

# --- Alerts: เฝ้าระวังข่าว/ราคาแรง -------------------------------------------
ALERTS = DATA / "alerts"
JOURNAL_ALERTS = JOURNAL / "Alerts"

# ราคา: z = ผลตอบแทนวันนี้ / ความผันผวนรายวัน 60 วัน
ALERT_SHOCK_Z = 3.0
ALERT_SHOCK_MIN_MOVE = {"stock": 4.0, "etf": 2.0}     # % ขั้นต่ำ กันหุ้นนิ่งมากแกว่งนิดเดียวก็ z สูง
ALERT_MARKET_MOVE = 2.5                                # SPY ขยับเกินนี้ (%) ในวันเดียว
ALERT_VIX_LEVEL = 30.0
ALERT_VIX_JUMP = 30.0                                  # VIX พุ่งเกินนี้ (%) ในวันเดียว
ALERT_LABEL_MATERIALITY = 75                           # label ข่าวที่ถือว่า "แรง"

# --- ความผันผวนรุนแรง → เรียก Claude มาสืบสาเหตุ (finbg/pm/volatility.py) ---------
# รันวันละครั้งหลังเก็บข้อมูล (morning.sh) · 0 token ถ้าไม่มีอะไรผิดปกติ
# z = ผลตอบแทน / ความผันผวนปกติ — ความผันผวนปกติคิดจาก 120 วันทำการ "ก่อน" ช่วงเหตุการณ์
# (ตัด 5 วันล่าสุดออก ไม่ให้เหตุการณ์ไปเพิ่มฐานของตัวเองจนดูไม่ผิดปกติ)
# ต้องผ่านทั้ง z และ % ขั้นต่ำ: หุ้นนิ่งมาก (เช่น KO) แกว่ง 2% ก็ได้ z สูง แต่ไม่ใช่เรื่องรุนแรง
VOL_LOOKBACK = 120
VOL_DAILY_Z = 3.5
VOL_DAILY_MIN_MOVE = {"stock": 5.0, "etf": 2.5}       # % ในวันเดียว
VOL_WEEK_Z = 3.0
VOL_WEEK_MIN_MOVE = {"stock": 10.0, "etf": 5.0}       # % สะสม 5 วันทำการ (จับการไหลลงต่อเนื่อง)
VOL_COOLDOWN_SESSIONS = 5          # หุ้นตัวเดิมไม่สืบซ้ำภายในนี้ เว้นแต่มีวันที่แรงกว่าเดิม
VOL_MAX_TICKERS = 6                # ต่อการสืบหนึ่งครั้ง (รวมเป็นเซสชันเดียว ประหยัดโควตา)
VOL_MAX_SESSIONS_PER_WEEK = 4
INVESTIGATIONS = DATA / "investigations"
JOURNAL_INVESTIGATIONS = JOURNAL / "Investigations"
INVESTIGATE_EFFORT = "high"

# วางแผนใหม่อัตโนมัติเมื่อมี alert ระดับ critical/high — มีเพดานกันวนลูปเผาโควตา
REPLAN_MAX_PER_WEEK = 2
REPLAN_MIN_HOURS_APART = 12

# --- การแจ้งเตือน ------------------------------------------------------------
# ค่าเริ่มต้น = แจ้งบนเดสก์ท็อปเท่านั้น (notify-send) ไม่ส่งอะไรออกนอกเครื่อง
# อยากได้บนมือถือ: `uv run finbg notify setup` (ntfy.sh) — ข้อความจะผ่านเซิร์ฟเวอร์สาธารณะ
# และใครรู้ชื่อ topic ก็อ่านได้ จึงให้ผู้ใช้เปิดเองโดยตั้งใจเท่านั้น
NOTIFY_CONFIG = Path.home() / ".config" / "finbg" / "notify.json"
NTFY_SERVER = "https://ntfy.sh"
