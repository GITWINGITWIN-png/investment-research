"""กรอบการลงทุน (mandate) — แปลงค่าใน config เป็นคำถามที่โค้ดส่วนอื่นถามได้ตรง ๆ

พอร์ตทั้งหมด = core 70% (กฎตายตัว SPY 60 / AGG 40) + satellite 30% (PM เลือก)
น้ำหนักในแผนเขียนเป็น "สัดส่วนของ satellite" (รวมกับเงินสดแล้ว = 1)
เพื่อให้ PM คิดในกรอบที่ตัวเองคุมได้จริง แล้วโค้ดค่อยแปลงเป็นน้ำหนักทั้งพอร์ต
"""

from __future__ import annotations

from .. import config

CASH = "CASH"


def kind_of(ticker: str) -> str:
    """broad = ETF ดัชนี/พันธบัตร/ทอง · sector = ETF รายหมวด · stock = หุ้นรายตัว"""
    if ticker in config.PM_BROAD_ETFS:
        return "broad"
    if ticker in config.SECTORS:
        return "sector"
    return "stock"


def cap_for(ticker: str) -> float:
    return {
        "broad": config.PM_CAP_BROAD,
        "sector": config.PM_CAP_SECTOR,
        "stock": config.PM_CAP_STOCK,
    }[kind_of(ticker)]


def in_universe(ticker: str) -> bool:
    return ticker in config.PM_SATELLITE_UNIVERSE


def passive_satellite() -> tuple[dict[str, float], float]:
    """satellite ของ control — และเป็นจุดตั้งต้นของ PM ก่อนแผนแรก"""
    return dict(config.PM_PASSIVE_SATELLITE), 0.0


def full_weights(satellite: dict[str, float], cash: float) -> dict[str, float]:
    """แปลงน้ำหนักของ satellite เป็นน้ำหนักทั้งพอร์ต (core ถูกเติมให้อัตโนมัติ)"""
    sleeve = 1.0 - config.PM_CORE_WEIGHT
    weights: dict[str, float] = {
        ticker: config.PM_CORE_WEIGHT * share for ticker, share in config.PM_CORE_MIX.items()
    }
    for ticker, share in satellite.items():
        weights[ticker] = weights.get(ticker, 0.0) + sleeve * share
    if cash > 0:
        weights[CASH] = sleeve * cash
    return weights


def turnover(old: dict[str, float], new: dict[str, float]) -> float:
    """turnover ขาเดียว = ครึ่งหนึ่งของผลรวมการเปลี่ยนแปลงสัมบูรณ์ (รวมเงินสดด้วย)

    ย้าย 20% จาก SPY ไป XLE → |−0.2| + |+0.2| = 0.4 → turnover = 0.2
    """
    keys = set(old) | set(new)
    return 0.5 * sum(abs(new.get(k, 0.0) - old.get(k, 0.0)) for k in keys)


def describe() -> list[str]:
    """บรรยายกรอบเป็นข้อ ๆ สำหรับใส่ในพรอมต์ — สร้างจาก config จึงไม่มีทางขัดกับตัวตรวจ"""
    core = " / ".join(f"{t} {w:.0%}" for t, w in config.PM_CORE_MIX.items())
    sleeve = 1.0 - config.PM_CORE_WEIGHT
    return [
        f"- พอร์ตทั้งหมด = **core {config.PM_CORE_WEIGHT:.0%}** (กฎตายตัว: {core} — คุณแก้ไม่ได้) "
        f"+ **satellite {sleeve:.0%}** (คุณเลือก)",
        "- น้ำหนักทุกตัวในแผนเป็น **สัดส่วนของ satellite** และ `ผลรวมน้ำหนัก + cash = 1.0`",
        f"- เพดานต่อตัว: ETF ดัชนี/พันธบัตร/ทอง ({', '.join(sorted(config.PM_BROAD_ETFS))}) ≤ {config.PM_CAP_BROAD:.0%} · "
        f"ETF รายหมวด ≤ {config.PM_CAP_SECTOR:.0%} · หุ้นรายตัว ≤ {config.PM_CAP_STOCK:.0%} "
        f"(= {config.PM_CAP_STOCK * sleeve:.1%} ของพอร์ต)",
        f"- ถือได้ไม่เกิน {config.PM_MAX_POSITIONS} ตัว · long only · ห้าม leverage · เงินสดถือได้ไม่จำกัด (ผลตอบแทน 0%)",
        f"- turnover ต่อแผน ≤ {config.PM_MAX_TURNOVER:.0%} ของ satellite (ขาเดียว) — "
        f"การเปลี่ยนที่เล็กกว่า {config.PM_MIN_CHANGE:.0%} จะถูกปัดคืนเป็นน้ำหนักเดิม",
        f"- ต้นทุนซื้อขาย {config.PM_COST_BPS:.0f} bps ของมูลค่าที่ซื้อขาย · ปรับพอร์ตที่ราคาปิดของวันทำการแรก"
        " ที่ตลาดปิด *หลัง* แผนออก",
        "- control (ตัวเทียบ) = พอร์ตเดียวกันทุกประการ แต่ satellite ถือ "
        + ", ".join(f"{t} {w:.0%}" for t, w in config.PM_PASSIVE_SATELLITE.items())
        + " ตลอด — ส่วนต่างระหว่างคุณกับ control คือฝีมือของคุณล้วน ๆ",
    ]
