"""Entry point for every background-track command.

Every command is designed to be **safe to re-run** (idempotent), because cron may run it again
or we may run it by hand during the day; no command should corrupt data when run twice.

    uv run finbg daily      # cron calls this every day
    uv run finbg brief      # build the situation brief
    uv run finbg predict "topic"
    uv run finbg hypo "topic"
    uv run finbg status
"""

from __future__ import annotations

import argparse
import sys
import traceback
from typing import Any

from . import config, journal, labels, ledger, notify, storage, workqueue
from .pm import plans as plans_module
from .pm import portfolio as pm_portfolio
from .pm import predictions as pm_predictions
from .pm import runner as pm_runner
from .pm import volatility as pm_volatility
from .pm import watch as pm_watch
from .scan import book as scan_book
from .scan import runner as scan_runner
from .th import book as th_book
from .th import collect as th_collect
from .th import scan as th_scan
from .brief import context as context_module
from .brief import render as render_module
from .collect import calendar as calendar_module
from .collect import fundamentals as fundamentals_module
from .collect import news as news_module
from .collect import prices as prices_module


def _print_result(label: str, result: dict[str, Any]) -> bool:
    """Print a short result and return whether it succeeded (used for the exit code)"""
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
    """How big the data store has grown; used to check that cron is running normally"""
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
    """Show where the remaining tokens should go, in order of importance"""
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

    # Provenance first: if the corpus mixes models, every other number becomes harder to interpret
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


def _pm_ledger_summary() -> dict[str, Any]:
    """The PM portfolio is recomputed every day; return only the main numbers so the log stays readable"""
    status = pm_portfolio.build()
    if not status.get("ok"):
        return status
    if not status.get("started"):
        return {"ok": True, "started": False, "pending": status.get("pending_plan") or "-"}
    return {
        "ok": True,
        "llm_pm": f"{status['lines']['llm_pm']['return_pct']:+.2f}%",
        "control": f"{status['lines']['pm_control']['return_pct']:+.2f}%",
        "excess": f"{status['excess_vs_control_pct']:+.2f}",
        "since": status["start"],
    }


def cmd_plan(args: argparse.Namespace) -> int:
    """Weekly plan of the Portfolio Manager (see note 11)"""
    if args.action == "status":
        state = pm_runner.due()
        current = plans_module.latest()
        print(f"🧭 สัปดาห์เป้าหมาย {state['week']} — {state['reason']}")
        if current:
            sat = ", ".join(f"{t} {w:.0%}" for t, w in current["targets"]["satellite"].items())
            print(f"   แผนล่าสุด {current['plan_id']} ({current['kind']}) ออก {current['created_at'][:16]} UTC")
            print(f"   satellite: {sat}" + (f", cash {current['targets']['cash']:.0%}" if current["targets"]["cash"] else ""))
        status = pm_portfolio.build()
        for line in plans_module.status_lines(status):
            if line:
                print(f"   {line}")
        card = pm_predictions.scorecard()
        if card.get("n"):
            print(f"   คำทำนาย: ถูก {card['hit_rate_pct']}% จาก {card['n']} ข้อ · Brier {card['brier']}")
        return 0

    if args.action in ("run", "replan"):
        kind = "weekly" if args.action == "run" else "replan"
        trigger = [args.reason] if args.reason else (["สั่งเองด้วย finbg plan replan"] if kind == "replan" else None)
        print(f"🧭 {'ออกแผนรายสัปดาห์' if kind == 'weekly' else 'วางแผนใหม่'}...")
        result = pm_runner.run(kind, force=args.force, dry_run=args.dry_run, trigger=trigger)
        if result.get("skipped"):
            print(f"  ⏭️  {result['reason']}")
            return 0
        if result.get("dry_run"):
            print(f"  📝 พรอมต์ → {result['request']} ({result['chars']:,} ตัวอักษร · ข่าว {result['news_refs']} ชิ้น)")
            return 0
        if not result.get("ok"):
            print(f"  ❌ {result.get('reason')}")
            if result.get("failure"):
                print(f"     รายละเอียด → {result['failure']}")
            return 1
        sat = ", ".join(f"{t} {w:.0%}" for t, w in result["satellite"].items())
        print(f"  ✅ {result['plan_id']} ({result['stance']}) · satellite: {sat}"
              + (f", cash {result['cash']:.0%}" if result["cash"] else "")
              + f" · turnover {result['turnover']:.0%} · รอบที่ส่ง {result['attempts']}")
        for warning in result.get("warnings") or []:
            print(f"  ⚠️  {warning}")
        print(f"  📄 {result['page']}")
        return 0

    if args.action == "ledger":
        return 0 if _print_result("pm_ledger", _pm_ledger_summary()) else 1

    # resolve
    return 0 if _print_result("predictions", pm_predictions.resolve_all()) else 1


def cmd_volatility(args: argparse.Namespace) -> int:
    """Watched stocks with violent moves: the LLM investigates the cause and decides (morning.sh calls this daily)"""
    tickers = [t.strip().upper() for t in args.ticker.split(",")] if args.ticker else None
    result = pm_volatility.run(dry_run=args.dry_run, replan=not args.no_replan, force_tickers=tickers)
    stamp = storage.utc_now().astimezone(config.LOCAL_TZ).strftime("%Y-%m-%d %H:%M")
    if not result.get("ok"):
        print(f"❌ {stamp} {result.get('reason')}")
        return 1
    flagged = result.get("flagged") or []
    print(f"🌪️  {stamp} ผันผวนรุนแรง {len(flagged)} ตัว" + (" (dry-run: ไม่เรียก Claude)" if args.dry_run else ""))
    for line in flagged:
        print(f"   · {line}")
    if result.get("skipped"):
        print(f"   ⏭️  {result['skipped']}")
    if result.get("report"):
        decisions = ", ".join(f"{t} → {d}" for t, d in result["decisions"].items())
        print(f"   🔎 ผลสืบ: {decisions} · เทียบเท่า ${result.get('cost_usd') or 0:.2f}")
        print(f"   📄 {result['report']}")
    if result.get("replan"):
        print(f"   🧭 วางแผนใหม่: {result['replan']}")
    return 0


def cmd_scan(args: argparse.Namespace) -> int:
    """Scan the whole market for overlooked opportunities (morning.sh calls this daily; skipped if already scanned that day)"""
    result = scan_runner.run(dry_run=args.dry_run, no_llm=args.no_llm, force=args.force)
    if result.get("skipped"):
        print(f"⏭️  {result['skipped']}")
        return 0
    print(f"🔭 วันทำการ {result['session']} · universe {result['universe']:,} ตัว · filing {result['sec_filings']:,} · "
          f"เหตุการณ์ {result['events']} · คนในซื้อ {result['insider_buyers']} · งบ {result['earnings_reported']} "
          f"→ ได้คะแนน {result['scored']} → เข้ารอบ {result['candidates']}")
    if result.get("backfilled"):
        print(f"   ดึงประวัติราคาเพิ่ม {result['backfilled']} ตัว")
    for line in result.get("top", [])[:10]:
        print(f"   · {line}")
    if args.dry_run:
        print("   (dry-run: ไม่เรียกโมเดล ไม่บันทึกลงพอร์ตไอเดีย)")
        return 0
    if result.get("picks") is not None:
        print(f"   🔎 Sonnet เลือกให้ดำลึก: {', '.join(result['picks']) or 'ไม่มี'}")
    print(f"   ✅ เข้าพอร์ตไอเดีย: {', '.join(result.get('booked') or []) or 'ไม่มี'} · "
          f"กลุ่มควบคุม: {', '.join(result.get('control') or []) or '-'} · เทียบเท่า ${result.get('cost_usd', 0):.2f}")
    for key in ("triage_error", "deepdive_error"):
        if result.get(key):
            print(f"   ⚠️  {key}: {result[key]}")
    print(f"   📄 {result['page']}")
    return 0


def cmd_ideas(args: argparse.Namespace) -> int:
    """Results of the idea portfolio against the control group"""
    if args.action == "resolve":
        print(scan_book.resolve())
    board = scan_book.scoreboard()
    for name, key in (("ไอเดียของ Claude", "ideas"), ("กลุ่มควบคุม", "control"), ("ผู้เข้ารอบทั้งหมด", "candidates")):
        s = board[key]
        print(f"{name:18} n={s['n']:4}  เฉลี่ย {s['mean_pct'] if s['mean_pct'] is not None else '—':>6}%  "
              f"ชนะ {s['hit_pct'] if s['hit_pct'] is not None else '—'}%  t={s['t']}")
    diff = board.get("ideas_minus_control")
    print(f"Claude − สุ่ม: {diff if diff else 'ยังไม่พอคำนวณ'} · ยังถือ {board['open_ideas']} · {board['gate']}")
    tri = board["triage_ic"]
    print(f"การทดสอบรอง (คะแนน Sonnet): IC {tri['sonnet']['mean_ic']} t={tri['sonnet']['t_nw']} · "
          f"เพิ่มจากโค้ด IC {tri['sonnet_beyond_code']['mean_ic']} · {tri['verdict']}")
    return 0


def cmd_th(args: argparse.Namespace) -> int:
    """Thai track: collect data (collect), scan for opportunities (scan), idea portfolio results (ideas), what the store holds (status)"""
    if args.action == "scan":
        r = th_scan.run(dry_run=args.dry_run, no_llm=args.no_llm, force=args.force)
        if r.get("skipped"):
            print(f"⏭️  {r['skipped']}")
            return 0
        print(f"🇹🇭 วันทำการ {r['session']} · ซื้อขายได้ {r['tradable']} ตัว · มีข่าวใหม่ {r['with_news']} · "
              f"ผู้บริหารซื้อ {r['with_insider_buys']} → มีเหตุการณ์ {r['events']}")
        print(f"   หน้าต่างข่าว {r['window']}")
        for line in r.get("top", [])[:20]:
            print(f"   · {line}")
        if not r.get("dry_run"):
            print(f"   ดำลึก: {', '.join(r['picks']) or '—'} · เข้าพอร์ตไอเดีย: {', '.join(r['booked']) or 'ไม่มี'} · "
                  f"กลุ่มสุ่ม: {', '.join(r['control']) or '—'} · ต้นทุนเทียบเท่า ${r['cost_usd']}")
            for key in ("triage_error", "deepdive_error"):
                if r.get(key):
                    print(f"   ⚠️  {key}: {r[key]}")
            print(f"   หน้า → {r['page']}")
        return 0
    if args.action == "ideas":
        print(th_book.resolve())
        board = th_book.scoreboard()
        for name, key in (("ไอเดียของ Claude", "ideas"), ("กลุ่มควบคุม", "control"), ("ผู้เข้ารอบทั้งหมด", "candidates")):
            s = board[key]
            print(f"{name:18} n={s['n']:4}  เฉลี่ย {s['mean_pct'] if s['mean_pct'] is not None else '—':>6}%  t={s['t']}")
        print(f"Claude − สุ่ม: {board['ideas_minus_control'] or 'ยังไม่พอคำนวณ'} · ยังถือ {board['open_ideas']} · {board['gate']}")
        print(f"การทดสอบรอง (คะแนน Sonnet): {board['triage_ic']['verdict']}")
        return 0
    if args.action == "status":
        s = th_collect.status()
        u, p, n = s["universe"], s["prices"], s["news"]
        print(f"🇹🇭 รายชื่อ: {u['snapshots']} snapshot (ล่าสุด {u['latest']}) · หุ้นสามัญตอนนี้ {u['common_stocks_now']} · "
              f"เคยอยู่ในคลัง {u['ever_listed']}")
        print(f"   ราคา: {p['snapshots']} snapshot · {p['tickers']} ตัว · แท่งล่าสุด {p['last_bar']}")
        print(f"   ข่าว: ทั้งหมด {n['unique_items']} ชิ้น · 7 วัน {n['last_7d']} ชิ้น (ติดป้ายหุ้น {n['last_7d_tagged']}) · "
              f"หุ้นที่ถูกพูดถึง {n['stocks_mentioned_7d']} ตัว")
        print(f"   ถูกพูดถึงมากสุด: {', '.join(f'{t} {c}' for t, c in n['top'])}")
        print(f"   หุ้นที่ซื้อขายได้จริง {n['tradable_now']} ตัว · ถูกพูดถึงต่อวัน: "
              + " · ".join(f"{d[5:]} {c}" for d, c in n["tradable_mentioned_per_day"].items()))
        i = s["insider"]
        print(f"   ตาราง 59: อ่านแล้ว {i['posts_read']} ฉบับ · {i['rows']} รายการ (ซื้อ {i['buys']})")
        return 0
    result = th_collect.run(news_hours=args.news_hours)
    stamp = storage.utc_now().astimezone(config.LOCAL_TZ).strftime("%Y-%m-%d %H:%M")
    print(f"🇹🇭 {stamp} เก็บข้อมูลหุ้นไทย")
    for step in ("universe", "prices", "news", "insider"):
        r = result[step]
        detail = {k: v for k, v in r.items() if k not in ("ok", "feeds")}
        print(f"   {'✅' if r.get('ok') else '❌'} {step}: {detail}")
    for name, feed in (result["news"].get("feeds") or {}).items():
        print(f"      {name}: {feed['items']} ชิ้น · {feed['pages']} หน้า · ย้อนครบ 30 ชม. {'✓' if feed['reached_cutoff'] else '✗'}")
    return 0 if result["ok"] else 1


def cmd_watch(args: argparse.Namespace) -> int:
    """One watch round; normally run once a week before issuing the plan (--weekly)"""
    if args.weekly:
        is_due, reason = pm_watch.weekly_due()
        if not is_due:
            print(f"⏭️  {reason}")
            return 0
        print(f"🗓️  รอบสัปดาห์: {reason}")
    result = pm_watch.run(dry_run=args.dry_run, escalate=not args.no_replan)
    if args.weekly and not args.dry_run:
        pm_watch.mark_weekly()
    stamp = storage.utc_now().astimezone(config.LOCAL_TZ).strftime("%Y-%m-%d %H:%M")
    print(f"👁️  {stamp} ตรวจพบ {result['checked']} เหตุการณ์ · ใหม่ {result['new']}"
          + (" (dry-run: ไม่แจ้ง ไม่บันทึก)" if args.dry_run else ""))
    for line in result["alerts"]:
        print(f"   {line}")
    for error in result["errors"]:
        print(f"   ⚠️  {error}")
    if result.get("replan"):
        print(f"   🧭 วางแผนใหม่: {result['replan']}")
    return 0


def cmd_notify(args: argparse.Namespace) -> int:
    if args.action == "setup":
        result = notify.setup()
        print("📱 ntfy " + ("สร้าง topic ใหม่แล้ว" if result["created"] else "ตั้งค่าไว้แล้ว"))
        print(f"   topic : {result['topic']}")
        print(f"   ติดตาม: ติดตั้งแอป ntfy บนมือถือ → Subscribe → ใส่ topic ข้างบน (server {result['server']})")
        print(f"   ตั้งค่าเก็บที่ {config.NOTIFY_CONFIG} — ลบไฟล์นี้เพื่อปิด")
        print("   ⚠️ ใครรู้ชื่อ topic อ่านข้อความได้ อย่าแชร์")
        return 0
    delivered = notify.send("🔔 finbg ทดสอบ", "ถ้าเห็นข้อความนี้ การแจ้งเตือนทำงานแล้ว", "info")
    print("ส่งแล้ว: " + ", ".join(f"{k}={'✅' if v else '❌'}" for k, v in delivered.items()))
    return 0


def _fundamentals_if_due() -> dict[str, Any]:
    """Financials change quarterly, so refetch only when they are stale or a new 10-Q/10-K exists.

    Each round downloads about 100 MB of JSON from SEC, so it should not run every day without a reason.
    """
    is_due, reason = fundamentals_module.due()
    if not is_due:
        return {"ok": True, "skipped": reason}
    print(f"  ↻ ดึงใหม่เพราะ: {reason}")
    return fundamentals_module.collect()


def cmd_daily(args: argparse.Namespace) -> int:
    """Daily job called by cron: collect data, compute the ledger, build the brief.

    It is meant **not to abort the whole run** when one step fails,
    because failing to fetch news should not waste prices that were already fetched.
    """
    print(f"🔄 งานประจำวัน — {storage.utc_stamp()}")
    failures: list[str] = []
    warnings: list[str] = []

    # `required=False` means the step may fail without failing the whole job
    # News is optional because the GDELT rate limit is outside our control,
    # and missing one day does less harm than a cron job that emails a warning every day until we stop noticing
    steps: list[tuple[str, Any, bool]] = [
        ("prices", lambda: prices_module.collect(), True),
        ("calendar", lambda: calendar_module.collect(), False),
        ("fundamentals", _fundamentals_if_due, False),
        ("ledger", lambda: ledger.rebuild(), True),
        ("pm_ledger", _pm_ledger_summary, False),
        ("predictions", lambda: pm_predictions.resolve_all(), False),
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
        description="Background track: collects data and summarises the market situation automatically",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_prices = sub.add_parser("prices", help="Fetch prices")
    p_prices.add_argument("--period", default=None, help="e.g. 1mo, 1y, 10y (empty = automatic)")
    p_prices.set_defaults(func=cmd_prices)

    p_news = sub.add_parser("news", help="Fetch news from several sources")
    p_news.add_argument("--sources", default=None,
                        help="comma-separated, e.g. rss,sec (empty = all sources)")
    p_news.set_defaults(func=cmd_news)

    sub.add_parser("ledger", help="Compute the paper ledger").set_defaults(func=cmd_ledger)
    sub.add_parser("brief", help="Build the situation brief").set_defaults(func=cmd_brief)
    sub.add_parser("status", help="Show the state of the data store").set_defaults(func=cmd_status)

    p_predict = sub.add_parser("predict", help="Create a new prediction")
    p_predict.add_argument("title")
    p_predict.add_argument("--horizon", type=int, default=90, help="days until it is due")
    p_predict.add_argument("--confidence", type=int, default=50, help="confidence 0-100")
    p_predict.set_defaults(func=cmd_predict)

    p_hypo = sub.add_parser("hypo", help="Create a hypothesis card")
    p_hypo.add_argument("title")
    p_hypo.add_argument("--cost", default="medium", choices=["low", "medium", "high"])
    p_hypo.set_defaults(func=cmd_hypo)

    p_queue = sub.add_parser("queue", help="Show what the remaining tokens should be spent on")
    p_queue.add_argument("--all", action="store_true", help="show everything")
    p_queue.add_argument("--limit", type=int, default=6)
    p_queue.add_argument("--export", action="store_true", help="write the queue as JSON for the LLM to read")
    p_queue.set_defaults(func=cmd_queue)

    p_label = sub.add_parser("label", help="Manage the labeled corpus")
    p_label.add_argument("action", nargs="?", default="stats",
                         choices=["prepare", "ingest", "stats", "backfill"])
    p_label.add_argument("--dry-run", action="store_true",
                         help="backfill: show how many items would be filled in, without writing files")
    p_label.add_argument("--batch-size", type=int, default=40)
    p_label.add_argument("--all", action="store_true",
                         help="no filter: include low-signal news too (normally only primary sources or confirmed tickers)")
    p_label.add_argument("--file", default=None, help="answer file (empty = latest)")
    p_label.set_defaults(func=cmd_label)

    p_plan = sub.add_parser("plan", help="Weekly plan of the Portfolio Manager")
    p_plan.add_argument("action", nargs="?", default="status",
                        choices=["status", "run", "replan", "ledger", "resolve"])
    p_plan.add_argument("--force", action="store_true", help="issue a plan even if it is not due yet or the cap is reached")
    p_plan.add_argument("--dry-run", action="store_true", help="build the prompt only, do not call the model")
    p_plan.add_argument("--reason", default=None, help="replan: the reason for replanning")
    p_plan.set_defaults(func=cmd_plan)

    p_watch = sub.add_parser("watch", help="summarise 8-Ks, strong news and VIX (normally once a week)")
    p_watch.add_argument("--dry-run", action="store_true", help="check only: no alerts, nothing saved")
    p_watch.add_argument("--no-replan", action="store_true", help="alert only: do not ask the LLM to replan")
    p_watch.add_argument("--weekly", action="store_true", help="run only when the weekly round is due (used in morning.sh)")
    p_watch.set_defaults(func=cmd_watch)

    p_scan = sub.add_parser("scan", help="Scan the whole market for overlooked opportunities (daily)")
    p_scan.add_argument("--dry-run", action="store_true", help="collect data and score, but do not call the model or save the portfolio")
    p_scan.add_argument("--no-llm", action="store_true", help="save the finalists, but do not call the model")
    p_scan.add_argument("--force", action="store_true", help="scan again even if already scanned today")
    p_scan.set_defaults(func=cmd_scan)

    p_ideas = sub.add_parser("ideas", help="Results of the idea portfolio against the control group")
    p_ideas.add_argument("action", nargs="?", default="status", choices=["status", "resolve"])
    p_ideas.set_defaults(func=cmd_ideas)

    p_th = sub.add_parser("th", help="Thai track: collect (0 tokens), scan, ideas, status; see note 13")
    p_th.add_argument("action", nargs="?", default="collect", choices=["collect", "scan", "ideas", "status"])
    p_th.add_argument("--news-hours", type=int, default=None,
                      help="look further back for news than usual (once, at the start; e.g. 168 = 7 days)")
    p_th.add_argument("--dry-run", action="store_true", help="scan: show the finalists without calling the model or saving")
    p_th.add_argument("--no-llm", action="store_true", help="scan: save the finalists without calling the model")
    p_th.add_argument("--force", action="store_true", help="scan: scan again even if this trading day was already scanned")
    p_th.set_defaults(func=cmd_th)

    p_vol = sub.add_parser("volatility", help="Watched stocks with violent moves: the LLM investigates the cause (daily)")
    p_vol.add_argument("--dry-run", action="store_true", help="show which stocks qualify, without calling the LLM")
    p_vol.add_argument("--no-replan", action="store_true", help="investigate only, do not ask the PM to replan")
    p_vol.add_argument("--ticker", default=None, help="investigate these tickers regardless of the thresholds, e.g. GE,META")
    p_vol.set_defaults(func=cmd_volatility)

    p_notify = sub.add_parser("notify", help="Configure or test notifications")
    p_notify.add_argument("action", nargs="?", default="test", choices=["test", "setup"])
    p_notify.set_defaults(func=cmd_notify)

    p_daily = sub.add_parser("daily", help="Daily job (for cron)")
    p_daily.add_argument("--skip-news", action="store_true", help="skip news (much faster)")
    p_daily.set_defaults(func=cmd_daily)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
