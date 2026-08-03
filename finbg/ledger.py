"""Paper ledger — กลยุทธ์ baseline ที่เดินคู่ขนานไปตลอด (BG-2)

จุดประสงค์: อีก 18 เดือนข้างหน้า เวลาคุณบอกว่า "กลยุทธ์ผมดี"
คุณจะมี **เส้นเปรียบเทียบที่เดินมาพร้อมกันจริง ๆ** ไม่ใช่ backtest ที่เถียงได้

ประเด็นความซื่อสัตย์ที่สำคัญที่สุดในไฟล์นี้:
เราคำนวณย้อนหลังได้ 10 ปีก็จริง แต่ **ช่วงก่อนวันที่เริ่มเก็บจริงคือ backtest**
ส่วนหลังจากนั้นคือ **live** — สองอย่างนี้มีน้ำหนักต่างกันมาก จึงต้องแยกให้ชัด
(`live_start` ในผลลัพธ์) ไม่งั้นเราจะหลอกตัวเองว่ามี track record ยาวกว่าความจริง
"""

from __future__ import annotations

import pandas as pd

from . import config, storage


def _monthly_rebalance_dates(index: pd.DatetimeIndex) -> list[pd.Timestamp]:
    """วันทำการแรกของแต่ละเดือน — ใช้เป็นจังหวะปรับพอร์ต

    เลือกรายเดือนเพราะ turnover ต่ำ (ค่าธรรมเนียมไม่กิน) และเป็นมาตรฐาน
    ที่งานวิจัย momentum ส่วนใหญ่ใช้ → เทียบกับวรรณกรรมได้
    """
    frame = pd.DataFrame(index=index)
    return list(frame.groupby([index.year, index.month]).apply(lambda g: g.index[0]))


def trading_calendar(prices: pd.DataFrame) -> pd.DatetimeIndex:
    """ปฏิทินวันทำการของตลาดหุ้น อ้างอิงจากวันที่ SPY มีราคา

    จำเป็นเพราะตารางราคารวม BTC-USD ซึ่งเทรด 7 วัน/สัปดาห์
    ถ้าไม่กรอง เสาร์-อาทิตย์จะถูกนับเป็นวันทำการด้วย ทำให้จำนวนวันและ
    drawdown ของกลยุทธ์หุ้นเพี้ยน
    """
    if "SPY" not in prices.columns:
        return prices.index
    return prices["SPY"].dropna().index


def _nav_from_weights(
    prices: pd.DataFrame,
    weights: pd.DataFrame,
    start_capital: float,
) -> pd.Series:
    """คำนวณ NAV จากน้ำหนักพอร์ตที่กำหนดในแต่ละวัน

    ใช้ผลตอบแทนรายวัน × น้ำหนัก **ของเมื่อวาน** เสมอ
    เหตุผล: น้ำหนักวันนี้ถูกตัดสินใจจากข้อมูลถึงเมื่อวาน — ถ้าใช้น้ำหนักวันนี้
    คูณผลตอบแทนวันนี้ จะกลายเป็น look-ahead bias ทันที

    ข้อสมมติที่ต้องรู้: การคงน้ำหนักคงที่ทุกวันเท่ากับปรับสมดุลรายวัน
    ซึ่งในความจริงมีค่าธรรมเนียม เรายังไม่คิดตรงนี้ — ยอมรับได้เพราะนี่เป็น
    "เส้นเปรียบเทียบ" ไม่ใช่กลยุทธ์ที่เราจะอ้างว่ามี alpha
    """
    returns = prices.pct_change().fillna(0.0)
    aligned = weights.reindex(returns.index).ffill().fillna(0.0)
    portfolio_returns = (returns * aligned.shift(1).fillna(0.0)).sum(axis=1)
    return start_capital * (1.0 + portfolio_returns).cumprod()


def _buy_hold(prices: pd.DataFrame, ticker: str) -> pd.Series:
    """ถือตัวเดียวยาว ๆ — เส้นที่กลยุทธ์ทุกอันต้องเอาชนะให้ได้ก่อน"""
    if ticker not in prices.columns:
        return pd.Series(dtype=float)
    subset = prices[[ticker]].dropna()
    weights = pd.DataFrame(1.0, index=subset.index, columns=[ticker])
    return _nav_from_weights(subset, weights, config.LEDGER_START_CAPITAL)


def _sixty_forty(prices: pd.DataFrame) -> pd.Series:
    """60% หุ้น / 40% พันธบัตร ปรับสมดุลรายเดือน — benchmark ที่มี risk control"""
    needed = ["SPY", "AGG"]
    if not all(t in prices.columns for t in needed):
        return pd.Series(dtype=float)

    subset = prices.loc[trading_calendar(prices), needed].dropna()
    weights = pd.DataFrame(index=subset.index, columns=needed, dtype=float)

    # ตั้งน้ำหนักใหม่ทุกต้นเดือน แล้วปล่อยให้ลอยตามราคาระหว่างเดือน (ffill)
    for date in _monthly_rebalance_dates(subset.index):
        weights.loc[date] = [0.6, 0.4]

    return _nav_from_weights(subset, weights.ffill(), config.LEDGER_START_CAPITAL)


def _momentum_12_1(prices: pd.DataFrame) -> pd.Series:
    """12-1 momentum บน sector ETF — factor คลาสสิกที่รู้จักกันดีที่สุด

    "12-1" = ดูผลตอบแทน 12 เดือนย้อนหลัง แต่ **ข้ามเดือนล่าสุด**
    เหตุผลที่ข้าม: ระยะสั้น 1 เดือนมักมี short-term reversal ซึ่งกลบสัญญาณ momentum
    """
    pool = [t for t in config.MOMENTUM_POOL if t in prices.columns]
    if len(pool) < config.MOMENTUM_TOP_N:
        return pd.Series(dtype=float)

    # จำกัดเฉพาะวันทำการของตลาดหุ้น ไม่ให้เสาร์-อาทิตย์จาก BTC ปนเข้ามา
    subset = prices.loc[trading_calendar(prices), pool].dropna(how="all")
    weights = pd.DataFrame(0.0, index=subset.index, columns=pool)

    for date in _monthly_rebalance_dates(subset.index):
        window_end = date - pd.DateOffset(months=1)     # ข้ามเดือนล่าสุด
        window_start = date - pd.DateOffset(months=12)

        history = subset.loc[:window_end]
        if history.empty:
            continue
        start_prices = subset.loc[:window_start]
        if start_prices.empty:
            continue

        momentum = (history.iloc[-1] / start_prices.iloc[-1] - 1.0).dropna()
        if len(momentum) < config.MOMENTUM_TOP_N:
            continue

        winners = momentum.nlargest(config.MOMENTUM_TOP_N).index
        weights.loc[date, :] = 0.0
        weights.loc[date, winners] = 1.0 / config.MOMENTUM_TOP_N

    # ก่อนถึง rebalance ครั้งแรกยังไม่มีน้ำหนัก → ถือเงินสด (NAV คงที่)
    weights = weights.where(weights.ne(0).any(axis=1), other=pd.NA).ffill().fillna(0.0)
    return _nav_from_weights(subset, weights, config.LEDGER_START_CAPITAL)


def _live_start() -> pd.Timestamp | None:
    """วันแรกที่เราเริ่มเก็บข้อมูลเอง = เส้นแบ่ง backtest กับ live"""
    files = sorted(config.RAW_PRICES.glob("*.parquet"))
    if not files:
        return None
    # ชื่อไฟล์คือวันที่รัน เช่น 2026-08-03.parquet
    return pd.Timestamp(files[0].stem.split("__")[0])


def _summarise(nav: pd.Series, live_start: pd.Timestamp | None) -> dict[str, object]:
    """สรุปตัวเลขที่จำเป็น โดยแยก live ออกจาก backtest อย่างชัดเจน"""
    if nav.empty:
        return {"ok": False}

    def stats(series: pd.Series) -> dict[str, float | None]:
        if len(series) < 2:
            return {"return_pct": None, "max_drawdown_pct": None, "days": len(series)}
        total = series.iloc[-1] / series.iloc[0] - 1.0
        drawdown = (series / series.cummax() - 1.0).min()
        return {
            "return_pct": round(total * 100, 2),
            "max_drawdown_pct": round(drawdown * 100, 2),
            "days": len(series),
        }

    result: dict[str, object] = {"ok": True, "full": stats(nav)}
    if live_start is not None:
        result["live"] = stats(nav.loc[nav.index >= live_start])
    return result


def rebuild() -> dict[str, object]:
    """คำนวณ ledger ใหม่ทั้งหมดจากคลังราคาที่เก็บไว้

    ตั้งใจให้ **คำนวณใหม่ทุกครั้ง** แทนที่จะเขียนต่อท้าย เพราะ:
    - ทำซ้ำได้ผลเดิมเสมอ (deterministic)
    - ถ้าเคยรันพลาดไปวันนึง ระบบซ่อมตัวเองได้ ไม่มีรูโหว่ถาวร
    """
    storage.ensure_dirs()
    prices = storage.price_matrix("adj_close")
    if prices.empty:
        return {"ok": False, "reason": "ยังไม่มีข้อมูลราคา — รัน collect ก่อน"}

    strategies = {
        "buy_hold_spy": _buy_hold(prices, "SPY"),
        "sixty_forty": _sixty_forty(prices),
        "mom_12_1": _momentum_12_1(prices),
    }
    strategies = {name: nav for name, nav in strategies.items() if not nav.empty}
    if not strategies:
        return {"ok": False, "reason": "คำนวณกลยุทธ์ไม่ได้เลย (ข้อมูลไม่พอ)"}

    nav_table = pd.DataFrame(strategies)
    nav_table.index.name = "date"

    out_path = config.DERIVED / "ledger.parquet"
    nav_table.reset_index().to_parquet(out_path, index=False)

    live_start = _live_start()
    return {
        "ok": True,
        "strategies": {name: _summarise(nav, live_start) for name, nav in strategies.items()},
        "live_start": str(live_start.date()) if live_start is not None else None,
        "rows": len(nav_table),
        "path": str(out_path.relative_to(config.ROOT)),
    }


def load() -> pd.DataFrame:
    """อ่าน ledger ที่คำนวณไว้แล้ว (ใช้ตอนสร้าง brief)"""
    path = config.DERIVED / "ledger.parquet"
    if not path.exists():
        return pd.DataFrame()
    df = pd.read_parquet(path)
    return df.set_index("date").sort_index()
