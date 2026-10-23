#!/usr/bin/env python3
"""สร้างกราฟประกอบเอกสาร PDF จากข้อมูลจริงที่โปรเจกต์เก็บไว้

ไม่ดึงรูปจากอินเทอร์เน็ต — ทุกกราฟสร้างจาก data/raw/prices/*.parquet
ซึ่งต้นทางคือ Yahoo Finance ข้อดี: ตรงกับข้อมูลที่ผู้อ่านมีอยู่จริง
สร้างซ้ำได้เสมอ และไม่ติดปัญหาลิขสิทธิ์ภาพ

รัน:  uv run python scripts/make-figures.py
ผลลัพธ์: docs/figures/*.pdf  (PDF เป็น vector จึงคมทุกระดับการซูม)
"""

from __future__ import annotations

import pathlib

import matplotlib
matplotlib.use("Agg")                       # ไม่ต้องมีหน้าจอ — รันบนเซิร์ฟเวอร์ได้
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib import font_manager

ROOT = pathlib.Path(__file__).resolve().parent.parent
OUT = ROOT / "docs" / "figures"

# matplotlib ไม่ได้อ่านฟอนต์ผ่าน fontconfig ของระบบ ต้องชี้ไฟล์ให้ตรง ๆ
# ถ้าข้ามขั้นนี้ ข้อความไทยจะกลายเป็นกล่องว่างโดยไม่มี error ฟ้อง
for _font in sorted(pathlib.Path("/usr/share/fonts/opentype/tlwg").glob("*.otf")):
    font_manager.fontManager.addfont(str(_font))

# Garuda — ตัวไม่มีเชิงที่หนากว่า ตรงกับที่เอกสาร LaTeX ใช้กับหัวข้อและป้ายในไดอะแกรม
# (เนื้อความในเอกสารใช้ Laksaman แต่ป้ายในกราฟต้องการน้ำหนักที่อ่านออกตอนย่อ)
plt.rcParams.update({
    "font.family": "Garuda",
    "font.size": 10,
    "axes.grid": True,
    "grid.alpha": 0.25,
    "axes.spines.top": False,
    "axes.spines.right": False,
    "figure.autolayout": True,
})

INK = "#1a1a1a"
ACCENT = "#c0392b"
BLUE = "#2c5aa0"
GREY = "#95a5a6"


def load_prices() -> pd.DataFrame:
    """ไฟล์ราคาที่ครบที่สุด — เลือกจากขนาด ไม่ใช่วันที่

    เพราะไฟล์รายวันจาก cron มีแค่ช่วงสั้น ๆ ส่วนไฟล์ backfill 10 ปีมีครั้งเดียว
    """
    files = sorted((ROOT / "data/raw/prices").glob("*.parquet"))
    if not files:
        raise SystemExit("ไม่พบไฟล์ราคา — รัน `uv run finbg prices` ก่อน")
    biggest = max(files, key=lambda p: p.stat().st_size)
    df = pd.read_parquet(biggest)
    df["date"] = pd.to_datetime(df["date"])
    return df


def wide(df: pd.DataFrame, field: str = "adj_close") -> pd.DataFrame:
    return df.pivot_table(index="date", columns="ticker", values=field)


def save(fig: plt.Figure, name: str) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / f"{name}.pdf"
    fig.savefig(path, bbox_inches="tight")
    plt.close(fig)
    print(f"  ✅ {path.relative_to(ROOT)}")


# ── 1. ราคาหุ้นหน้าตาเป็นอย่างไร ────────────────────────────────────────────
def fig_price_history(px: pd.DataFrame) -> None:
    spy = px["SPY"].dropna()
    fig, ax = plt.subplots(figsize=(7, 3.4))
    ax.plot(spy.index, spy.values, color=BLUE, lw=1.1)
    ax.set_ylabel("ราคา (ดอลลาร์)")
    ax.set_title("SPY — กองทุนที่ถือหุ้นใหญ่ 500 ตัวของสหรัฐฯ", loc="left", weight="bold")

    # ทำเครื่องหมายช่วงที่ราคาตกแรง เพื่อให้เห็นว่า "ขึ้นยาว" ไม่ได้แปลว่า "ไม่เจ็บ"
    peak = spy.cummax()
    dd = spy / peak - 1
    worst = dd.idxmin()
    ax.annotate(
        f"ตกหนักสุด {dd.min():.0%}",
        xy=(worst, spy.loc[worst]),
        xytext=(0.30, 0.72), textcoords="axes fraction",
        color=ACCENT, ha="left",
        arrowprops=dict(arrowstyle="->", color=ACCENT, lw=1,
                        connectionstyle="arc3,rad=-0.2"),
    )
    save(fig, "01-price-history")


# ── 2. ผลตอบแทนรายวันไม่ได้กระจายแบบระฆังคว่ำ ───────────────────────────────
def fig_return_distribution(px: pd.DataFrame) -> None:
    ret = px["SPY"].dropna().pct_change().dropna()
    fig, ax = plt.subplots(figsize=(7, 3.4))

    ax.hist(ret, bins=120, density=True, color=BLUE, alpha=0.65, label="ผลตอบแทนจริงของ SPY")

    # เส้นระฆังคว่ำที่มีค่าเฉลี่ยและส่วนเบี่ยงเบนเท่ากัน — เพื่อเทียบให้เห็นหางที่หนากว่า
    grid = np.linspace(ret.min(), ret.max(), 400)
    normal = np.exp(-0.5 * ((grid - ret.mean()) / ret.std()) ** 2) / (ret.std() * np.sqrt(2 * np.pi))
    ax.plot(grid, normal, color=ACCENT, lw=1.6, label="ระฆังคว่ำที่ค่าเฉลี่ย/ความผันผวนเท่ากัน")

    ax.set_xlabel("ผลตอบแทนรายวัน")
    ax.set_ylabel("ความหนาแน่น")
    ax.set_title("วันที่ตลาดเหวี่ยงแรง เกิดบ่อยกว่าที่คณิตศาสตร์พื้นฐานทำนาย",
                 loc="left", weight="bold")
    ax.legend(frameon=False, fontsize=8.5)

    beyond = (ret.abs() > 3 * ret.std()).sum()
    ax.text(0.02, 0.55,
            f"วันที่เหวี่ยงเกิน 3 เท่าของปกติ: {beyond} วัน\n"
            f"ถ้าเป็นระฆังคว่ำจริงควรมีราว {len(ret) * 0.0027:.0f} วัน",
            transform=ax.transAxes, fontsize=8.5, color=INK)
    save(fig, "02-return-distribution")


# ── 3. ขาดทุนสะสมจากจุดสูงสุด (drawdown) ───────────────────────────────────
def fig_drawdown(px: pd.DataFrame) -> None:
    spy = px["SPY"].dropna()
    dd = spy / spy.cummax() - 1
    fig, ax = plt.subplots(figsize=(7, 2.9))
    ax.fill_between(dd.index, dd.values, 0, color=ACCENT, alpha=0.5)
    ax.plot(dd.index, dd.values, color=ACCENT, lw=0.7)
    ax.set_ylabel("ห่างจากจุดสูงสุด")
    ax.yaxis.set_major_formatter(lambda v, _: f"{v:.0%}")
    ax.set_title("ต่อให้ระยะยาวขึ้น ระหว่างทางก็ติดลบเป็นปี ๆ", loc="left", weight="bold")
    save(fig, "03-drawdown")


# ── 4. ความผันผวนมาเป็นกลุ่ม ไม่ได้กระจายสม่ำเสมอ ──────────────────────────
def fig_volatility_clustering(px: pd.DataFrame) -> None:
    ret = px["SPY"].dropna().pct_change()
    vol = ret.rolling(21).std() * np.sqrt(252)
    fig, ax = plt.subplots(figsize=(7, 3.0))
    ax.plot(vol.index, vol.values, color=BLUE, lw=1.0, label="ความผันผวนย้อนหลัง 1 เดือน (ต่อปี)")
    if "^VIX" in px.columns:
        vix = px["^VIX"].dropna() / 100
        ax.plot(vix.index, vix.values, color=GREY, lw=0.8, alpha=0.8,
                label="VIX — ความผันผวนที่ตลาดคาดล่วงหน้า")
    ax.yaxis.set_major_formatter(lambda v, _: f"{v:.0%}")
    ax.set_title("ช่วงที่ตลาดปั่นป่วนมักมาติด ๆ กัน แล้วสงบยาว", loc="left", weight="bold")
    ax.legend(frameon=False, fontsize=8.5)
    save(fig, "04-volatility-clustering")


# ── 5. เปรียบเทียบกลยุทธ์พื้นฐาน 3 แบบ ──────────────────────────────────────
def fig_baselines() -> None:
    path = ROOT / "data/derived/ledger.parquet"
    if not path.exists():
        print("  ⏭️  ข้าม 05 — ยังไม่มี ledger.parquet")
        return
    led = pd.read_parquet(path)
    led["date"] = pd.to_datetime(led["date"])
    led = led.set_index("date")

    labels = {
        "buy_hold_spy": "ซื้อแล้วถือ SPY เฉย ๆ",
        "sixty_forty": "หุ้น 60% พันธบัตร 40%",
        "mom_12_1": "ไล่ตามกลุ่มที่กำลังมาแรง",
    }
    fig, ax = plt.subplots(figsize=(7, 3.4))
    for col, color in zip(labels, [BLUE, GREY, ACCENT]):
        if col in led.columns:
            series = led[col].dropna()
            ax.plot(series.index, series.values / series.iloc[0], color=color, lw=1.2,
                    label=f"{labels[col]} (×{series.iloc[-1] / series.iloc[0]:.1f})")
    ax.set_ylabel("เงินเติบโตกี่เท่า")
    # แกน log ทำให้เปอร์เซ็นต์การเปลี่ยนแปลงเท่ากันมีระยะห่างเท่ากัน ซึ่งถูกต้องกว่าสำหรับผลตอบแทน
    # แต่ป้ายเริ่มต้นของ matplotlib เขียนว่า "4 × 10^0" ซึ่งอ่านไม่รู้เรื่อง — เปลี่ยนเป็น "4 เท่า"
    ax.set_yscale("log")
    ax.set_yticks([1, 1.5, 2, 3, 4, 5])
    ax.yaxis.set_major_formatter(lambda v, _: f"{v:g} เท่า")
    ax.yaxis.set_minor_formatter(lambda v, _: "")
    ax.set_title("กลยุทธ์ที่ซับซ้อนกว่า ไม่ได้แปลว่าดีกว่าเสมอ", loc="left", weight="bold")
    ax.legend(frameon=False, fontsize=8.5, loc="upper left")
    save(fig, "05-baselines")


# ── 6. หุ้นเคลื่อนไหวไปด้วยกันแค่ไหน ────────────────────────────────────────
def fig_correlation(px: pd.DataFrame) -> None:
    sectors = [c for c in px.columns if c.startswith("XL")]
    extras = [t for t in ("SPY", "TLT", "GLD", "BTC-USD") if t in px.columns]
    cols = sectors + extras
    corr = px[cols].pct_change().corr()

    fig, ax = plt.subplots(figsize=(6.4, 5.6))
    im = ax.imshow(corr.values, cmap="RdBu_r", vmin=-1, vmax=1)
    ax.set_xticks(range(len(cols)), cols, rotation=90, fontsize=7.5)
    ax.set_yticks(range(len(cols)), cols, fontsize=7.5)
    ax.grid(False)
    fig.colorbar(im, ax=ax, shrink=0.72, label="ค่าสหสัมพันธ์")
    ax.set_title("ยิ่งแดง ยิ่งขึ้นลงพร้อมกัน", loc="left", weight="bold")
    save(fig, "06-correlation")


# ── 7. โมเมนตัมคืออะไร ──────────────────────────────────────────────────────
def fig_momentum(px: pd.DataFrame) -> None:
    sectors = [c for c in px.columns if c.startswith("XL")]
    if not sectors:
        print("  ⏭️  ข้าม 07 — ไม่มีข้อมูล sector")
        return
    sub = px[sectors].dropna()
    asof = sub.index[-1]
    window_end = asof - pd.DateOffset(months=1)
    window_start = asof - pd.DateOffset(months=12)
    seg = sub.loc[(sub.index >= window_start) & (sub.index <= window_end)]
    perf = (seg.iloc[-1] / seg.iloc[0] - 1).sort_values()

    fig, ax = plt.subplots(figsize=(7, 3.2))
    colors = [ACCENT if v < 0 else BLUE for v in perf.values]
    ax.barh(perf.index, perf.values, color=colors, alpha=0.85)
    ax.xaxis.set_major_formatter(lambda v, _: f"{v:.0%}")
    ax.axvline(0, color=INK, lw=0.8)
    ax.set_title("ผลตอบแทน 12 เดือน (เว้นเดือนล่าสุด) — กลยุทธ์โมเมนตัมซื้อ 3 แท่งบนสุด",
                 loc="left", weight="bold", fontsize=10)
    save(fig, "07-momentum")


# ── 8. คลัง label ที่โปรเจกต์สะสมอยู่ ───────────────────────────────────────
def fig_corpus() -> None:
    import json
    import sys
    sys.path.insert(0, str(ROOT))
    from finbg import config, labels

    recs = [
        json.loads(line)
        for f in sorted((ROOT / "data/labels").glob("*.jsonl"))
        for line in f.read_text(encoding="utf-8").splitlines() if line.strip()
    ]
    if not recs:
        print("  ⏭️  ข้าม 08 — ยังไม่มี label")
        return

    url_sector = {r["url"]: labels.sector_of(r) for r in labels._candidates(True)}
    order = list(config.SECTOR_MEMBERS) + ["macro"]
    counts = pd.Series(
        [url_sector.get(r["url"], "ไม่ทราบ") for r in recs]
    ).value_counts().reindex(order).fillna(0)

    fig, axes = plt.subplots(1, 2, figsize=(7.4, 3.0))

    axes[0].bar(counts.index, counts.values, color=BLUE, alpha=0.85)
    axes[0].set_xticks(range(len(counts)), counts.index, rotation=90, fontsize=7.5)
    axes[0].set_ylabel("จำนวน label")
    axes[0].set_title(f"กระจายตามกลุ่มธุรกิจ (รวม {len(recs)})", loc="left",
                      weight="bold", fontsize=9.5)

    mat = [r["materiality"] for r in recs]
    axes[1].hist(mat, bins=20, color=ACCENT, alpha=0.7)
    axes[1].set_xlabel("คะแนนความสำคัญ (0-100)")
    axes[1].set_title("ข่าวส่วนใหญ่ไม่สำคัญ — และนั่นถูกแล้ว", loc="left",
                      weight="bold", fontsize=9.5)
    save(fig, "08-corpus")


def main() -> None:
    print("📊 สร้างกราฟจากข้อมูลจริงในโปรเจกต์")
    df = load_prices()
    px = wide(df)
    print(f"   ราคา {px.shape[0]} วัน · {px.shape[1]} ticker "
          f"({px.index.min():%Y-%m-%d} → {px.index.max():%Y-%m-%d})")

    fig_price_history(px)
    fig_return_distribution(px)
    fig_drawdown(px)
    fig_volatility_clustering(px)
    fig_baselines()
    fig_correlation(px)
    fig_momentum(px)
    fig_corpus()
    print("เสร็จ")


if __name__ == "__main__":
    main()
