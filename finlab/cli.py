"""จุดเข้าใช้งาน harness

    uv run finlab universe        เลือก universe (บริษัทใหญ่สุด 300 แห่ง)
    uv run finlab prices          ดาวน์โหลดราคา 10 ปี (ครั้งแรก ~2-3 นาที)
    uv run finlab gate            ตรวจประตู 1 — harness ปฏิเสธของปลอมได้ไหม
    uv run finlab eval momentum_12_1
"""

from __future__ import annotations

import argparse
import sys

from . import data, experiments, factors, gate, metrics


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="finlab", description="evaluation harness")
    sub = parser.add_subparsers(dest="command", required=True)
    p_universe = sub.add_parser("universe")
    p_universe.add_argument("--size", type=int, default=300)
    p_prices = sub.add_parser("prices")
    p_prices.add_argument("--refresh", action="store_true")
    sub.add_parser("gate")
    p_eval = sub.add_parser("eval")
    p_eval.add_argument("factor", choices=sorted(factors.REAL))
    args = parser.parse_args(argv)

    if args.command == "universe":
        u = data.build_universe(args.size)
        print(f"universe: หุ้น {len(u['stocks'])} ตัว + ETF {len(u['etfs'])} ตัว")
        print(f"⚠️  {u['warning']}")
        return 0
    if args.command == "prices":
        r = data.download_prices(refresh=args.refresh)
        print(r)
        return 0 if r.get("ok") else 1
    if args.command == "gate":
        r = gate.run()
        for name, check in r["checks"].items():
            print(f"{'✅' if check['passed'] else '❌'} {name}")
        print(f"รายงาน → Journal/Research/gate-1-{r['at'][:10]}.md")
        return 0 if r["passed"] else 1

    prices = data.load_prices("adj_close", "stocks")
    factor = factors.REAL[args.factor](prices)
    decay = metrics.decay_curve(factor, prices)
    print(decay.round(4).to_string())
    experiments.log("manual", args.factor, {}, decay.loc[21].to_dict(), prices.shape[1],
                    (str(prices.index[0].date()), str(prices.index[-1].date())))
    print(f"(การทดลองที่บันทึกแล้วทั้งหมด: {experiments.count()})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
