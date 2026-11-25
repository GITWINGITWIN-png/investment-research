"""Gate 1 (note 07, phase 0): the harness must reject fakes before it can be trusted on real factors.

"A good measuring system must reject fakes; if the harness says random noise has IC = 0.04,
it is broken, and everything after that is meaningless." This is the unit test of quantitative research.

| # | Test | Passes when |
|---|---|---|
| 1 | 20 random factors | mean IC about 0, at most 15% significant (|t|>2), the best has DSR < 0.95 |
| 2 | a factor that secretly uses the future | caught by lookahead_audit, and real factors are not falsely flagged |
| 3 | momentum 12-1 | monthly Rank IC within the range the literature reports (-0.03 to 0.08) |
| 4 | re-run | identical results to the last digit (deterministic) |
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

import pandas as pd

from finbg import config as fconfig
from finbg import storage

from . import data, experiments, factors, metrics, stats, validation

REPORTS = fconfig.JOURNAL / "Research"


def _ic_hash(prices: pd.DataFrame) -> str:
    ic = metrics.rank_ic(factors.momentum_12_1(prices), data.forward_returns(prices, 21))
    return hashlib.sha256(ic.round(12).to_json().encode()).hexdigest()[:16]


def run(seeds: int = 20) -> dict[str, Any]:
    prices = data.load_prices("adj_close", "stocks")
    period = (str(prices.index[0].date()), str(prices.index[-1].date()))
    names = prices.shape[1]
    result: dict[str, Any] = {"at": storage.utc_stamp(), "period": period, "stocks": names,
                              "universe_warning": data.universe()["warning"], "checks": {}}

    # 1: random factors
    fwd1, fwd5 = data.forward_returns(prices, 1), data.forward_returns(prices, 5)
    rows, spreads = [], {}
    for seed in range(seeds):
        noise = factors.random_noise(prices, seed)
        summary = metrics.ic_summary(metrics.rank_ic(noise, fwd5), 5)
        spreads[f"random_{seed}"] = metrics.quantile_spread(noise, fwd1)
        rows.append({"seed": seed, **summary})
        experiments.log("gate-1", f"random_noise_{seed}", {"seed": seed, "horizon": 5}, summary, names, period)
    random_table = pd.DataFrame(rows)
    performance = pd.DataFrame(spreads)
    per_period_sr = performance.mean() / performance.std()
    best = per_period_sr.idxmax()
    dsr = stats.deflated_sharpe(performance[best], n_trials=seeds, variance_of_sr=float(per_period_sr.var()))
    psr_naive = stats.probabilistic_sharpe(performance[best])
    pbo = stats.pbo_cscv(performance)
    significant = float((random_table["t_stat"].abs() > 2).mean())
    mean_ic = float(random_table["mean_ic"].mean())
    result["checks"]["random"] = {
        "passed": significant <= 0.15 and abs(mean_ic) < 0.005 and dsr < 0.95,
        "mean_ic": mean_ic, "share_significant": significant,
        "best": best, "best_sharpe_annual": stats.sharpe(performance[best]),
        "best_psr_naive": psr_naive, "best_dsr": dsr, "pbo": pbo["pbo"],
    }

    # 2 — look-ahead
    cheat = metrics.lookahead_audit(factors.lookahead_cheat(prices, 5), prices, 5)
    real = {name: metrics.lookahead_audit(fn(prices), prices, 5) for name, fn in factors.REAL.items()}
    result["checks"]["lookahead"] = {
        "passed": cheat["suspicious"] and not any(a["suspicious"] for a in real.values()),
        "cheat": cheat, "real": real,
    }

    # 3: momentum + the reference factor table
    library: dict[str, Any] = {}
    for name, fn in factors.REAL.items():
        factor = fn(prices)
        decay = metrics.decay_curve(factor, prices)
        library[name] = {
            "decay": decay[["mean_ic", "icir", "t_stat", "hit_rate"]].round(4).to_dict(orient="index"),
            "turnover_monthly": metrics.top_quantile_turnover(factor),
        }
        experiments.log("gate-1", name, {"horizons": list(decay.index)},
                        decay.loc[21].to_dict(), names, period)
    momentum_ic = metrics.rank_ic(factors.momentum_12_1(prices), data.forward_returns(prices, 21))
    momentum = metrics.ic_summary(momentum_ic, 21)
    yearly = validation.walk_forward_ic(momentum_ic, validation.walk_forward(prices.index))
    result["checks"]["momentum"] = {
        "passed": -0.03 <= momentum["mean_ic"] <= 0.08,
        "summary": momentum, "walk_forward": yearly.to_dict(orient="records"),
    }
    result["library"] = library

    # 4: reproducible
    first, second = _ic_hash(prices), _ic_hash(data.load_prices("adj_close", "stocks"))
    result["checks"]["reproducible"] = {"passed": first == second, "hash_1": first, "hash_2": second}

    result["passed"] = all(c["passed"] for c in result["checks"].values())
    result["experiments_logged"] = experiments.count("gate-1")
    _write_report(result)
    return result


def _write_report(result: dict[str, Any]) -> None:
    day = result["at"][:10]
    out_json = data.RESEARCH / f"gate-1-{day}.json"
    out_json.write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str), encoding="utf-8")

    c = result["checks"]
    mark = lambda ok: "✅ ผ่าน" if ok else "❌ ไม่ผ่าน"          # noqa: E731
    lines = [
        "---", f"date: {day}", "tags: [research, gate, harness]", "---", "",
        f"# 🚪 ประตู 1 — ผลตรวจ harness ({day})", "",
        f"**ผลรวม: {mark(result['passed'])}** · หุ้น {result['stocks']} ตัว · ช่วง {result['period'][0]} → {result['period'][1]}",
        "",
        f"> [!warning] {result['universe_warning']}",
        "",
        "## 1. factor สุ่มต้องถูกปฏิเสธ — " + mark(c["random"]["passed"]),
        "",
        f"- IC เฉลี่ยของ factor สุ่ม 20 ตัว: **{c['random']['mean_ic']:+.4f}** (ควร ≈ 0)",
        f"- สัดส่วนที่ |t| > 2 โดยบังเอิญ: **{c['random']['share_significant']:.0%}** (คาด ~5%, ยอมได้ ≤ 15%)",
        f"- ตัวที่โชคดีที่สุด ({c['random']['best']}): Sharpe รายปี {c['random']['best_sharpe_annual']:.2f} · "
        f"PSR แบบไม่หักการลองหลายครั้ง {c['random']['best_psr_naive']:.2f} → **DSR {c['random']['best_dsr']:.2f}** (< 0.95 = ไม่น่าเชื่อ ✓)",
        f"- PBO ของการเลือกตัวดีสุดจาก 20 ตัว: **{c['random']['pbo']:.2f}** (ไม่ใช้เป็นเกณฑ์ผ่าน — ดูหมายเหตุ)",
        "",
        "> ข้อนี้คือเหตุผลที่ต้องมี DSR: ถ้าดูแค่ PSR ตัวที่โชคดีที่สุดอาจดูน่าเชื่อ",
        "> แต่เมื่อคิดว่าลองมา 20 ครั้ง ความน่าเชื่อหายไป",
        ">",
        "> **หมายเหตุเรื่อง PBO:** PBO วัดว่าตัวที่ดีที่สุดในครึ่ง in-sample ยังดีในครึ่ง out-of-sample ไหม",
        "> *ภายในข้อมูลชุดเดียวกัน* — factor สุ่มที่บังเอิญดีตลอดทั้งชุดจะดูคงเส้นคงวา PBO จึงต่ำได้",
        "> ทั้งที่เป็น noise ล้วน ๆ (ค่าคาดหวังของ PBO สำหรับ noise คือ ~0.5 แต่ข้อมูลชุดเดียวแกว่งได้มาก)",
        "> PBO จับ overfitting จากการจูนพารามิเตอร์ได้ดี แต่จับ \"โชคทั้งชุด\" ไม่ได้ — งานนั้นเป็นของ DSR",
        "",
        "## 2. factor ที่แอบใช้อนาคตต้องถูกจับ — " + mark(c["lookahead"]["passed"]),
        "",
        f"- factor โกง: IC {c['lookahead']['cheat']['ic']:.3f} → ใช้ค่าเก่า 5 วันเหลือ {c['lookahead']['cheat']['ic_lagged']:.3f}",
        *[f"  - 🚩 {flag}" for flag in c["lookahead"]["cheat"]["flags"]],
        "- factor จริง (ต้องไม่ถูกจับผิด):",
        *[f"  - {name}: IC {a['ic']:+.4f} → {a['ic_lagged']:+.4f} {'🚩' if a['suspicious'] else '✓'}"
          for name, a in c["lookahead"]["real"].items()],
        "",
        "## 3. momentum 12-1 อยู่ในช่วงวรรณกรรม — " + mark(c["momentum"]["passed"]),
        "",
        f"- Rank IC รายเดือนเฉลี่ย **{c['momentum']['summary']['mean_ic']:+.4f}** · ICIR {c['momentum']['summary']['icir']:.2f} · "
        f"t (ไม่ซ้อนทับ) {c['momentum']['summary']['t_stat']:.2f} · เป็นบวก {c['momentum']['summary']['hit_rate']:.0%} ของวัน",
        "- รายปี (walk-forward):",
        "",
        "| ช่วงทดสอบ | IC เฉลี่ย |",
        "|---|---:|",
        *[f"| {row['test_start']} → {row['test_end']} | {row['mean_ic']:+.4f} |" for row in c["momentum"]["walk_forward"]],
        "",
        "## 4. รันซ้ำได้ผลเดิม — " + mark(c["reproducible"]["passed"]),
        "",
        f"- hash รอบ 1 `{c['reproducible']['hash_1']}` · รอบ 2 `{c['reproducible']['hash_2']}`",
        "",
        "## ห้องสมุด factor อ้างอิง (Rank IC ตาม horizon)",
        "",
        "| factor | 1 วัน | 5 วัน | 10 วัน | 21 วัน (1 เดือน) | 63 วัน (1 ไตรมาส) | turnover กลุ่มบน/เดือน |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for name, block in result["library"].items():
        decay = block["decay"]
        cells = " | ".join(f"{decay[h]['mean_ic']:+.4f}" for h in (1, 5, 10, 21, 63))
        lines.append(f"| {name} | {cells} | {block['turnover_monthly']:.0%} |")
    lines += [
        "",
        f"*บันทึกการทดลองแล้ว {result['experiments_logged']} รายการใน data/research/experiments.jsonl "
        "(ใช้คำนวณ Deflated Sharpe ของการทดลองครั้งต่อ ๆ ไป)*",
        "",
        "🔗 [[09 - Evaluation Playbook]] · [[07 - Long-term Roadmap]] · [[11 - Weekly Portfolio Manager]]",
        "",
    ]
    REPORTS.mkdir(parents=True, exist_ok=True)
    (REPORTS / f"gate-1-{day}.md").write_text("\n".join(lines), encoding="utf-8")
