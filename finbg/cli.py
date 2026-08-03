"""จุดเข้าใช้งานทั้งหมดของ background track

ออกแบบให้ทุกคำสั่ง **รันซ้ำได้อย่างปลอดภัย** (idempotent) เพราะ cron อาจรันซ้ำ
หรือเราอาจรันเองระหว่างวัน — ไม่ควรมีคำสั่งไหนที่รันสองครั้งแล้วข้อมูลเพี้ยน

    uv run finbg daily      # ← cron เรียกอันนี้ทุกวัน
    uv run finbg brief      # สร้าง situation brief
    uv run finbg predict "หัวข้อ"
    uv run finbg hypo "หัวข้อ"
    uv run finbg status
"""

from __future__ import annotations

import argparse
import sys
import traceback
from typing import Any

from . import config, journal, labels, ledger, storage, workqueue
from .brief import context as context_module
from .brief import render as render_module
from .collect import news as news_module
from .collect import prices as prices_module


def _print_result(label: str, result: dict[str, Any]) -> bool:
    """แสดงผลแบบสั้น ๆ และคืนว่าสำเร็จหรือไม่ (ใช้กำหนด exit code)"""
    if result.get("ok"):
        details = " · ".join(
            f"{k}={v}" for k, v in result.items()
            if k not in ("ok", "strategies") and not isinstance(v, (dict, list))
        )
        print(f"  ✅ {label}: {details}")
        return True
    print(f"  ❌ {label}: {result.get('reason', 'ไม่ทราบสาเหตุ')}")
    return False


def cmd_prices(args: argparse.Namespace) -> int:
    print("📈 ดึงราคา...")
    return 0 if _print_result("prices", prices_module.collect(period=args.period)) else 1


def cmd_news(args: argparse.Namespace) -> int:
    sources = args.sources.split(",") if args.sources else None
    print(f"📰 ดึงข่าว (แหล่ง: {', '.join(sources or config.NEWS_SOURCES)})...")
    return 0 if _print_result("news", news_module.collect(sources=sources)) else 1


def cmd_ledger(_: argparse.Namespace) -> int:
    print("🎯 คำนวณ paper ledger...")
    result = ledger.rebuild()
    if not result.get("ok"):
        print(f"  ❌ {result.get('reason')}")
        return 1

    print(f"  live_start = {result['live_start']} (ก่อนหน้านี้เป็น backtest)")
    for name, stats in result["strategies"].items():
        full = stats.get("full", {})
        live = stats.get("live", {})
        live_note = ""
        if live.get("return_pct") is not None:
            live_note = f" · live {live['return_pct']:+.2f}% ({live['days']}d)"
        print(
            f"  · {name:14} ตั้งแต่ต้น {full.get('return_pct'):+.2f}% "
            f"maxDD {full.get('max_drawdown_pct'):+.2f}%{live_note}"
        )
    return 0


def cmd_brief(_: argparse.Namespace) -> int:
    print("📊 สร้าง situation brief...")
    result = render_module.build_brief()
    if not result.get("ok"):
        print(f"  ❌ {result.get('reason')}")
        return 1
    print(f"  ✅ brief   → {result['brief']}")
    print(f"  ✅ context → {result['context']}")
    print()
    print("  ขั้นต่อไป (ใช้ token): เปิด Claude Code แล้วสั่ง")
    print(f"    อ่าน {result['context']} แล้วเติมหัวข้อ 'การวิเคราะห์' ใน {result['brief']}")
    return 0


def cmd_predict(args: argparse.Namespace) -> int:
    result = journal.new_prediction(args.title, args.horizon, args.confidence)
    if not result.get("ok"):
        print(f"❌ {result.get('reason')}")
        return 1
    print(f"✅ สร้างคำทำนาย → {result['path']}")
    print(f"   ครบกำหนด {result['resolve_by']} · มั่นใจ {result['confidence']}%")
    print("   อย่าลืมเติมช่อง 'อะไรจะพิสูจน์ว่าผมผิด'")
    return 0


def cmd_hypo(args: argparse.Namespace) -> int:
    result = journal.new_hypothesis(args.title, args.cost)
    print(f"✅ สร้าง {result['id']} → {result['path']}")
    return 0


def cmd_status(_: argparse.Namespace) -> int:
    """ภาพรวมว่าคลังข้อมูลโตแค่ไหนแล้ว — ใช้ดูว่า cron ทำงานปกติไหม"""
    storage.ensure_dirs()

    price_files = sorted(config.RAW_PRICES.glob("*.parquet"))
    news_files = sorted(config.RAW_NEWS.glob("*.jsonl"))
    briefs = sorted(config.BRIEFS.glob("*.md"))
    predictions = sorted(config.PREDICTIONS.glob("*.md"))
    hypotheses = sorted(config.HYPOTHESES.glob("*.md"))

    print("📦 สถานะคลังข้อมูล")
    print(f"  ราคา       {len(price_files):4} snapshot" +
          (f"  (ล่าสุด {price_files[-1].stem})" if price_files else ""))
    print(f"  ข่าว       {len(news_files):4} วัน" +
          (f"  (ล่าสุด {news_files[-1].stem})" if news_files else ""))
    print(f"  brief      {len(briefs):4} ฉบับ")
    print(f"  คำทำนาย    {len(predictions):4} ข้อ")
    print(f"  สมมติฐาน   {len(hypotheses):4} ใบ")

    due = journal.due_predictions()
    if due:
        print()
        print(f"  ⏰ คำทำนายครบกำหนดแล้ว {len(due)} ข้อ — ไปบันทึกผล:")
        for item in due:
            print(f"     · {item['file']} (ครบ {item['resolve_by']})")

    if config.MANIFEST.exists():
        lines = config.MANIFEST.read_text(encoding="utf-8").strip().splitlines()
        print(f"\n  manifest {len(lines)} รายการ")
    return 0


def cmd_queue(args: argparse.Namespace) -> int:
    """แสดงว่า token ที่เหลือควรไปลงตรงไหน เรียงตามความสำคัญ"""
    items = workqueue.build()
    if not items:
        print("✅ ไม่มีงานค้าง — คิวว่าง")
        return 0

    stats = workqueue.summary()
    print(f"🗂️  งานค้าง {stats['total']} รายการ "
          f"({stats['perishable']} รายการเน่าตามเวลา ⏳)")
    print()

    icons = {"L1": "📝", "L2": "🏷️", "L3": "🔬", "L4": "📚"}
    shown = items if args.all else items[: args.limit]

    for i, item in enumerate(shown, start=1):
        perish = " ⏳" if item["perishable"] else ""
        print(f"{i:2}. {icons.get(item['level'], '·')} [{item['level']}]{perish} "
              f"{item['what']}")
        print(f"      ต้นทุน: {item['cost']}")
        print(f"      ทำไม: {item['why']}")
        print(f"      วิธี: {item['how']}")
        print()

    if not args.all and len(items) > len(shown):
        print(f"   … อีก {len(items) - len(shown)} รายการ (ใช้ --all เพื่อดูทั้งหมด)")
        print()

    print("💡 ⏳ = เน่าตามเวลา ทำก่อน · L4 ไม่เน่า ใช้เป็นที่ทิ้ง token ส่วนเกินได้")
    if args.export:
        print(f"📄 เขียนคิวลง {workqueue.export()}")
    return 0


def cmd_label(args: argparse.Namespace) -> int:
    if args.action == "prepare":
        high_signal = None if not args.all else False
        result = labels.prepare(batch_size=args.batch_size, high_signal_only=high_signal)
        if not result.get("ok"):
            print(f"❌ {result.get('reason')}")
            return 1
        scope = "เฉพาะสัญญาณสูง" if result["filtered"] else "ทุกข่าว"
        print(f"✅ เตรียม batch {result['articles']} บทความ ({scope})")
        print(f"   โมเดลที่ตั้งใจใช้ → {result['model_expected']}")
        print(f"   คำสั่ง  → {result['request']}")
        print(f"   คาดหวัง → {result['response_expected']}")
        print(f"   เหลืออีก {result['remaining_after']} ชิ้นหลัง batch นี้")
        print()
        print("   ขั้นต่อไป (ใช้ token): เปิด Claude Code แล้วสั่ง")
        print(f"     ทำตามคำสั่งใน {result['request']}")
        return 0

    if args.action == "ingest":
        result = labels.ingest(args.file)
        if not result.get("ok"):
            print(f"❌ {result.get('reason')}")
            for detail in result.get("rejected", [])[:5]:
                print(f"   บรรทัด {detail.get('line')}: {'; '.join(detail['errors'][:3])}")
            return 1
        print(f"✅ เก็บ {result['accepted']} label → {result['path']}")
        if result["rejected"]:
            print(f"⚠️  ปฏิเสธ {result['rejected']} รายการ:")
            for detail in result["reject_details"][:5]:
                print(f"   บรรทัด {detail.get('line')} {detail.get('url', '')}: "
                      f"{'; '.join(detail['errors'][:3])}")
        print(f"   เหลือยังไม่ label {result['remaining_unlabeled']} ชิ้น")
        return 0

    if args.action == "backfill":
        result = labels.backfill(dry_run=args.dry_run)
        mode = "จำลอง (ไม่เขียนจริง)" if result["dry_run"] else "เขียนจริง"
        print(f"🔧 backfill days_after_publish — {mode}")
        print(f"   ตรวจทั้งหมด {result['total']} label")
        print(f"   เติมได้      {result['filled']}")
        if result["still_null"]:
            print(f"   ⚠️  ยังเป็น null {result['still_null']} "
                  f"(ไม่พบ url ในคลังข่าวดิบ หรือแหล่งไม่ส่งวันที่มา)")
        if result["files"]:
            print(f"   ไฟล์ที่แตะ: {', '.join(result['files'])}")
        elif not result["filled"]:
            print("   ✅ ไม่มีอะไรต้องเติม")
        return 0

    # stats
    stats = labels.stats()
    print("🏷️  คลัง label")
    print(f"   ทั้งหมด        {stats['total']}")
    print(f"   ยังไม่ label   {stats['unlabeled']}")

    if not stats["total"]:
        return 0

    # provenance ขึ้นก่อน — ถ้าคลังปนโมเดล ตัวเลขอื่นทั้งหมดตีความยากขึ้นทันที
    print()
    print("   ── ที่มา (provenance) ──")
    for model, count in stats["by_model"].items():
        pct = count / stats["total"] * 100
        print(f"   {model:24} {count:5}  ({pct:.0f}%)")
    if stats["by_effort"]:
        print("   effort: " + " · ".join(f"{k}={v}" for k, v in stats["by_effort"].items()))
    if stats["schema_versions"]:
        print("   schema: " + " · ".join(f"v{k}={v}" for k, v in stats["schema_versions"].items()))

    for warning in stats["warnings"]:
        print(f"   ⚠️  {warning}")

    print()
    print("   ── เนื้อหา ──")
    print(f"   label ภายใน 2 วัน {stats['labeled_within_2_days']} (ยิ่งมากยิ่งซื่อสัตย์)")
    print(f"   materiality ≥70   {stats['high_materiality']}")
    for field in ("regime", "novelty", "topic"):
        if stats.get(field):
            inner = " · ".join(f"{k}={v}" for k, v in stats[field].items())
            print(f"   {field:9} {inner}")
    return 0


def cmd_daily(args: argparse.Namespace) -> int:
    """งานประจำวันที่ cron เรียก — เก็บข้อมูล คำนวณ ledger สร้าง brief

    ตั้งใจให้ **ไม่ล้มทั้งกระบวนการ** ถ้าขั้นตอนใดขั้นตอนหนึ่งพัง
    เพราะข่าวดึงไม่ได้ ไม่ควรทำให้ราคาที่ดึงมาแล้วสูญเปล่า
    """
    print(f"🔄 งานประจำวัน — {storage.utc_stamp()}")
    failures: list[str] = []
    warnings: list[str] = []

    # `required=False` = ขาดได้ ไม่ทำให้ทั้ง job ถือว่าล้มเหลว
    # ข่าวถูกจัดเป็นไม่บังคับเพราะ GDELT rate limit อยู่นอกการควบคุมเรา
    # และการพลาดไป 1 วันเสียหายน้อยกว่าการให้ cron ส่งอีเมลเตือนทุกวันจนเราชิน
    steps: list[tuple[str, Any, bool]] = [
        ("prices", lambda: prices_module.collect(), True),
        ("ledger", lambda: ledger.rebuild(), True),
    ]
    if not args.skip_news:
        steps.insert(1, ("news", lambda: news_module.collect(), False))

    for name, action, required in steps:
        print(f"\n▶ {name}")
        try:
            if not _print_result(name, action()):
                (failures if required else warnings).append(name)
        except Exception:
            print(f"  ❌ {name} เกิดข้อผิดพลาด:")
            traceback.print_exc(limit=3)
            (failures if required else warnings).append(name)

    print("\n▶ brief")
    try:
        result = render_module.build_brief()
        if result.get("ok"):
            print(f"  ✅ {result['brief']}")
        else:
            print(f"  ❌ {result.get('reason')}")
            failures.append("brief")
    except Exception:
        print("  ❌ brief เกิดข้อผิดพลาด:")
        traceback.print_exc(limit=3)
        failures.append("brief")

    due = journal.due_predictions()
    if due:
        print(f"\n⏰ มีคำทำนายครบกำหนด {len(due)} ข้อรอบันทึกผล")

    if warnings:
        print(f"\n⚠️  ขั้นตอนที่ข้ามไป (ไม่บังคับ): {', '.join(warnings)}")
    if failures:
        print(f"\n❌ ขั้นตอนที่ล้มเหลว: {', '.join(failures)}")
        return 1
    print("\n✅ เสร็จสมบูรณ์")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="finbg",
        description="Background track — เก็บข้อมูลและสรุปสถานการณ์แบบอัตโนมัติ",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_prices = sub.add_parser("prices", help="ดึงราคา")
    p_prices.add_argument("--period", default=None, help="เช่น 1mo, 1y, 10y (ปล่อยว่าง = อัตโนมัติ)")
    p_prices.set_defaults(func=cmd_prices)

    p_news = sub.add_parser("news", help="ดึงข่าวจากหลายแหล่ง")
    p_news.add_argument("--sources", default=None,
                        help="คั่นด้วยจุลภาค เช่น yahoo,rss,sec (ปล่อยว่าง = ทุกแหล่ง)")
    p_news.set_defaults(func=cmd_news)

    sub.add_parser("ledger", help="คำนวณ paper ledger").set_defaults(func=cmd_ledger)
    sub.add_parser("brief", help="สร้าง situation brief").set_defaults(func=cmd_brief)
    sub.add_parser("status", help="ดูสถานะคลังข้อมูล").set_defaults(func=cmd_status)

    p_predict = sub.add_parser("predict", help="สร้างคำทำนายใหม่")
    p_predict.add_argument("title")
    p_predict.add_argument("--horizon", type=int, default=90, help="กี่วันจึงครบกำหนด")
    p_predict.add_argument("--confidence", type=int, default=50, help="ความมั่นใจ 0-100")
    p_predict.set_defaults(func=cmd_predict)

    p_hypo = sub.add_parser("hypo", help="สร้าง hypothesis card")
    p_hypo.add_argument("title")
    p_hypo.add_argument("--cost", default="medium", choices=["low", "medium", "high"])
    p_hypo.set_defaults(func=cmd_hypo)

    p_queue = sub.add_parser("queue", help="ดูว่า token ที่เหลือควรใช้ทำอะไร")
    p_queue.add_argument("--all", action="store_true", help="แสดงทั้งหมด")
    p_queue.add_argument("--limit", type=int, default=6)
    p_queue.add_argument("--export", action="store_true", help="เขียนคิวเป็น JSON ให้ Claude อ่าน")
    p_queue.set_defaults(func=cmd_queue)

    p_label = sub.add_parser("label", help="จัดการ labeled corpus")
    p_label.add_argument("action", nargs="?", default="stats",
                         choices=["prepare", "ingest", "stats", "backfill"])
    p_label.add_argument("--dry-run", action="store_true",
                         help="backfill: ดูว่าจะเติมกี่รายการ โดยยังไม่เขียนไฟล์")
    p_label.add_argument("--batch-size", type=int, default=40)
    p_label.add_argument("--all", action="store_true",
                         help="ไม่กรอง — รวมข่าวสัญญาณต่ำด้วย (ปกติกรองเฉพาะ primary/ยืนยัน ticker)")
    p_label.add_argument("--file", default=None, help="ไฟล์คำตอบ (ปล่อยว่าง = ล่าสุด)")
    p_label.set_defaults(func=cmd_label)

    p_daily = sub.add_parser("daily", help="งานประจำวัน (สำหรับ cron)")
    p_daily.add_argument("--skip-news", action="store_true", help="ข้ามข่าว (เร็วขึ้นมาก)")
    p_daily.set_defaults(func=cmd_daily)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
