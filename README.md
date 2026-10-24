# finbg — ระบบวิจัยการลงทุนอัตโนมัติ

ระบบเก็บข้อมูล → ให้ LLM วางแผนการลงทุนรายสัปดาห์ → รันแผนบนพอร์ตกระดาษ → เฝ้าข่าวแรงทุกชั่วโมง
และ harness สำหรับทดสอบ algo กับข้อมูลย้อนหลังแบบไม่หลอกตัวเอง

| ส่วน | ทำอะไร | อ่านเพิ่ม |
|---|---|---|
| **ข้อมูล** (`finbg/collect`) | ราคา 10 ปี · ข่าว 4 แหล่ง + label · งบ SEC XBRL · ปฏิทินงบ | [10 - Background Track](AI-Investing-Research/10%20-%20Background%20Track.md) |
| **Portfolio Manager** (`finbg/pm`) | LLM เขียนแผนสัปดาห์ละครั้ง โค้ดตรวจกฎและรันบนพอร์ตกระดาษ เทียบ control | [11 - Weekly Portfolio Manager](AI-Investing-Research/11%20-%20Weekly%20Portfolio%20Manager.md) |
| **เฝ้าระวัง** (`finbg/pm/watch.py`) | กฎในโค้ดจับ 8-K/ราคาช็อก/VIX/ข่าวแรง → แจ้งเตือน → วางแผนใหม่ | โน้ต 11 |
| **Harness** (`finlab`) | IC/ICIR, walk-forward, purged CV, Deflated Sharpe, PBO — ผ่านประตู 1 แล้ว | [09 - Evaluation Playbook](AI-Investing-Research/09%20-%20Evaluation%20Playbook.md) |

---

## แนวคิดหลัก

แยกงานเป็น 2 ชั้นอย่างเด็ดขาด:

| ชั้น | ทำอะไร | ต้นทุน |
|------|--------|--------|
| **1. รวบรวมข้อเท็จจริง** (โค้ดล้วน) | ดึงราคา/ข่าว, คำนวณสถิติ, สร้างตาราง | ฟรี · ทำซ้ำได้เป๊ะ |
| **2. ตีความ** (Claude) | เชื่อมโยง, ตั้งสมมติฐาน, ประเมินความเสี่ยง | ใช้ token เฉพาะตรงนี้ |

เหตุผล: การให้ LLM นั่งคำนวณ % เปลี่ยนแปลง = เผา token กับงานที่ pandas ทำได้ฟรี
และยังเสี่ยงคำนวณผิด **ชั้นที่ 1 บีบข้อมูลให้เหลือแต่แก่นก่อน แล้วค่อยส่งให้ชั้นที่ 2**

---

## คำสั่ง

```bash
uv run finbg daily               # งานประจำวัน (cron เรียกอันนี้)
uv run finbg daily --skip-news   # เร็วกว่ามาก ถ้าไม่ต้องการข่าว

uv run finbg prices              # ดึงราคาอย่างเดียว
uv run finbg news                # ดึงข่าวอย่างเดียว
uv run finbg ledger              # คำนวณ paper baseline ใหม่
uv run finbg brief               # สร้าง situation brief
uv run finbg status              # ดูว่าคลังข้อมูลโตแค่ไหน

uv run finbg predict "หัวข้อ" --horizon 90 --confidence 60
uv run finbg hypo "หัวข้อ" --cost low

uv run finbg plan status        # แผนปัจจุบัน + ผลงานเทียบ control
uv run finbg plan run           # ออกแผนรายสัปดาห์ (ปกติออกเองเช้าวันเสาร์)
uv run finbg plan run --dry-run # ดูพรอมต์ที่ PM จะได้อ่าน โดยไม่เรียกโมเดล
uv run finbg plan replan --reason "..."  # วางแผนใหม่เอง
uv run finbg watch --dry-run    # ตอนนี้มีเหตุการณ์แรงอะไรบ้าง
uv run finbg notify setup       # (เลือกเปิด) แจ้งเตือนบนมือถือผ่าน ntfy

uv run finlab gate              # ตรวจ harness: ปฏิเสธ factor ปลอมได้ไหม
uv run finlab eval momentum_12_1

uv run finbg queue              # ← token เหลือ ใช้ทำอะไรดี
uv run finbg label prepare      # เตรียม batch (ฟรี)
uv run finbg label ingest       # เก็บผล + ตรวจความถูกต้อง (ฟรี)
uv run finbg label stats        # provenance + คำเตือน drift/hindsight
uv run finbg label backfill --dry-run   # ซ่อม days_after_publish ที่หายไป
```

---

## 📘 ไม่เข้าใจว่าทั้งหมดนี้คืออะไร — เริ่มที่คู่มือ

[`docs/guide.pdf`](docs/guide.pdf) — 22 หน้า อธิบายตั้งแต่ "หุ้นคืออะไร"
ถึงเหตุผลเบื้องหลังทุกการตัดสินใจในโปรเจกต์ สำหรับคนที่ไม่เคยลงทุนมาก่อนเลย

```bash
uv run python scripts/make-figures.py      # สร้างกราฟ 8 รูปจากข้อมูลจริง
cd docs && xelatex guide.tex && xelatex guide.tex   # คอมไพล์ (รันสองรอบให้สารบัญถูก)
```

ต้องมี XeLaTeX + ฟอนต์ตระกูล TLWG (`fonts-tlwg`) — ฟอนต์ไทยหลายตัวเช่น Noto Serif Thai
ไม่มี glyph ภาษาอังกฤษ คำอังกฤษที่ปนในประโยคไทยจะกลายเป็นกล่องว่าง

---

## 🤖 Label อัตโนมัติ — ทำงานเองทุกวัน ไม่ต้องเปิด session ทิ้งไว้

```
ครั้งแรกที่เครื่องเปิดของวัน (anacron)  →  scripts/morning.sh
    daily.sh       เก็บราคา + ข่าว + ปฏิทินงบ + งบ (ถ้าถึงรอบ) + ledger + brief
    label-auto.sh  มีข่าวรอไหม? (0 token) → ถ้ามี เปิด claude -p แบบ headless
    finbg plan run แผนรายสัปดาห์ (เฉพาะเมื่อถึงเวลา — ปกติคือเช้าวันเสาร์)
ทุกชั่วโมง  →  scripts/watch.sh   เฝ้าข่าว/ราคาแรง (0 token จนกว่าจะเจอเรื่องใหญ่)
```

ปิด Claude Code ปิดหน้าจอได้ตามปกติ — cron ของระบบเป็นคนเรียก ไม่ผูกกับ session ใด

**ทำไมเปลี่ยนจาก cron 06:00 เป็น anacron (2026-10-24):** เดิมถ้าเครื่องปิดตอน 06:00
งานวันนั้นหายทั้งวัน — เกิดจริง 27 จาก 59 วันทำการ และข่าวย้อนหลังเก็บไม่ได้
anacron จำว่าวันนี้รันแล้วหรือยัง แล้วรันให้ตอนเปิดเครื่อง (ถ้าเครื่องเปิดค้างไว้ก็รัน 05:05)

**ทำไมไม่ใช้ CronCreate ในตัว Claude Code:** งานนั้นอยู่ในหน่วยความจำของ session
ปิด session แล้วหาย ทำงานเฉพาะตอน REPL ว่าง และหมดอายุเองใน 7 วัน

### ตรวจตอนเช้า

```bash
tail -20 data/logs/label-$(date +%Y-%m).log
```

| บรรทัด | ความหมาย |
|---|---|
| `SUMMARY rounds=2 accepted=29 ... note=ok` | ปกติ |
| `⏭️ ไม่มีข่าวรอ label` | ปกติ — ไม่มีงานก็ไม่เปิด session |
| `🚨 ถูกปฏิเสธสิทธิ์ N ครั้ง` | **ต้องดู** มีอะไรพยายามออกนอกกรง |
| `🚨 โมเดลไม่ตรง!` | **ต้องดู** label รอบนั้นปนเปื้อน provenance |

JSON ดิบของทุกรอบเก็บไว้ที่ `data/logs/label-runs/` (90 วัน)

### 🔐 ทำไมงานนี้ปลอดภัยพอจะรันตอนไม่มีคนดู

พาดหัวข่าวคือข้อความจากคนอื่นที่ไม่ผ่านการตรวจ — เท่ากับป้อน prompt injection
ให้โมเดลที่มีสิทธิ์รันคำสั่ง จึงล้อมสามชั้น:

1. **`deny` ใน [`scripts/label-loop-settings.json`](scripts/label-loop-settings.json)** — ปิด `cd`/`python3`/`curl`/`git`/`rm`/`Edit`
2. **hook [`scripts/guard-queue-write.py`](scripts/guard-queue-write.py)** — เขียนได้เฉพาะ `data/queue/` (กัน `../` ด้วย realpath)
3. **`permission_denials` ใน audit log** — เห็นย้อนหลังว่ามีอะไรพยายามอะไร

ทดสอบแล้วว่าโหมด headless **ปฏิเสธแล้วไปต่อ ไม่ค้างรอคนกด** จึงไม่แขวน cron

> ⚠️ **อย่าเก็บ `deny` ไว้ใน `.claude/settings.local.json`**
> ระบบเขียนทับไฟล์นั้นทุกครั้งที่คุณกดอนุมัติคำสั่ง และเคยทำ deny block หายทั้งก้อน
> พร้อมพากฎอันตรายกลับเข้า allow ใหม่ — ใช้ `.claude/settings.json` แทน

### สั่งเองในเซสชัน

ถ้าอยากรันเดี๋ยวนั้นแทนที่จะรอ cron ใช้ [`scripts/loop-prompt.md`](scripts/loop-prompt.md) กับ `/loop`
หรือเรียกสคริปต์ตรง ๆ: `./scripts/label-auto.sh`

---

## 🪙 บันไดการใช้ token

หลักการเดียวที่ใช้จัดลำดับ:

> **ใช้ token กับสิ่งที่ย้อนหลังทำไม่ได้ก่อน แล้วค่อยลงมาที่สิ่งที่ทำทีหลังได้**

| ระดับ | งาน | ย้อนหลังทำได้ไหม |
|-------|-----|-----------------|
| **L0** | เก็บข้อมูลดิบ (cron ทำเอง) | ❌ — 0 token |
| **L1** | แผนรายสัปดาห์ของ PM (ออกเองเช้าวันเสาร์) | ❌ ปนเปื้อน hindsight ทันที |
| **L2** | label ข่าว | ❌ ปนเปื้อน hindsight ทันที |
| **L3** | ทดสอบสมมติฐาน | ⚠️ ได้ แต่ช้าลง |
| **L4** | สรุป paper เป็นโน้ต | ✅ ได้ตลอด |

L1/L2 **เน่าตามเวลา** — ทิ้งไว้นานยิ่งไร้ค่า เพราะเราเริ่มรู้ผลลัพธ์แล้ว
L4 ไม่เน่า → เป็น **"ที่ทิ้ง token" ที่ปลอดภัย** เมื่อเหลือเยอะใกล้หมดรอบ

```bash
uv run finbg queue      # เรียงให้แล้ว ⏳ = เน่าตามเวลา ทำก่อน
```

---

## รอบการทำงาน

| ความถี่ | ทำอะไร | token |
|---------|--------|-------|
| ทุกวัน (อัตโนมัติ) | เก็บข้อมูล + label ข่าว (L2) + พอร์ต PM + ตัดสินคำทำนาย | น้อย (label ~$0.5 เทียบเท่า) |
| ทุกชั่วโมง (อัตโนมัติ) | เฝ้าระวัง — เรียก LLM เฉพาะเมื่อเจอเหตุการณ์ high/critical | 0 ส่วนใหญ่ |
| ทุกสัปดาห์ (อัตโนมัติ) | แผนรายสัปดาห์ของ PM (L1) — เสาร์เช้า | กลาง (1 session) |
| ทุกสัปดาห์ (คุณ) | อ่านแผนใน `Journal/Plans/` · ดูว่ามีอะไรค้านได้ไหม | 0 |
| ทุกเดือน | ทดสอบ hypothesis 1 ใบด้วย `finlab` (L3) | หลัก |
| เมื่อ token เหลือ | สรุป paper (L4) | เท่าไหร่ก็ได้ |

### L1 — แผนรายสัปดาห์ (แทน "เติมบทวิเคราะห์ใน brief" เดิม)

เดิม L1 คือให้คนสั่ง Claude เติมบทวิเคราะห์ใน brief ทุกวัน — ผ่านไป 45 ฉบับ ไม่ได้เติมเลยสักฉบับ
ตอนนี้ PM เขียนแผนเองสัปดาห์ละครั้ง (มีมุมมอง เหตุผล หลักฐาน คำทำนายที่ตัดสินได้)
และ brief รายวันเป็นข้อเท็จจริงล้วน + สรุปแผนที่ใช้อยู่ — รายละเอียดที่ [โน้ต 11](AI-Investing-Research/11%20-%20Weekly%20Portfolio%20Manager.md)

### L2 — labeled corpus (สินทรัพย์ที่ซื้อไม่ได้)

```bash
uv run finbg label prepare        # 1. เตรียม batch — ฟรี
#   เปิด Claude Code: "ทำตามคำสั่งใน data/queue/label-request-*.md"   ← 2. ใช้ token
uv run finbg label ingest         # 3. ตรวจ + เก็บ — ฟรี
```

**ทำไมต้อง label ตอนนี้ไม่ใช่ทีหลัง:** ถ้า label ข่าวปี 2026 ในปี 2027
โมเดลรู้ผลลัพธ์ไปแล้ว → `novelty` (ใหม่จริงหรือย้ำของเดิม) ปนเปื้อน hindsight
ฟิลด์ `days_after_publish` บันทึกไว้ให้กรองทิ้งได้ทีหลังถ้า label ช้าเกินไป

schema v1: `regime` · `novelty` · `topic` + `topic_free` · `horizon` ·
`materiality` · `entities[]` · `rationale`
แก้ schema ที่ [`finbg/labels.py`](finbg/labels.py) ที่เดียว — prompt และ validator สร้างจากตรงนั้น

---

## ตารางงานที่ติดตั้งแล้ว

ไฟล์จริงอยู่ที่ [`scripts/crontab.txt`](scripts/crontab.txt) (ติดตั้งด้วย `crontab scripts/crontab.txt`)
และ [`scripts/anacrontab`](scripts/anacrontab)

```cron
5 5-23 * * *  anacron -s -t scripts/anacrontab ...   # ทุกชั่วโมง 05:05–23:05
@reboot       sleep 120; anacron ...                 # ตอนบูต (ถ้าหลัง 05:00)
35 * * * *    scripts/watch.sh                       # เฝ้าระวังทุกชั่วโมง
```

| งานใน anacron | รอบ | ทำอะไร |
|---|---|---|
| `finbg-morning` | ทุกวัน | `scripts/morning.sh` (เก็บข้อมูล → label → แผนถ้าถึงเวลา) |
| `finbg-backup` | ทุก 7 วัน | `scripts/backup.sh` |

ไม่เรียก anacron ช่วง 00:00–04:59 เพราะเป็นช่วงที่ตลาดสหรัฐยังเปิด (ข้อมูลวันนั้นยังไม่ครบ)
และถ้ารันตอนตลาดเปิด ตัวเก็บราคาก็ตัดแท่งของวันนั้นทิ้งอยู่แล้ว (`finbg/market.py`)

ตรวจสอบว่าทำงาน:

```bash
tail -5 data/logs/morning-$(date +%Y-%m).log    # daily=0 label=0 plan=0|skip
tail -20 data/logs/plan-$(date +%Y-%m).log
tail -20 data/logs/watch-$(date +%Y-%m).log
```

---

## โครงสร้าง

```
finbg/
  config.py          ค่าตั้งทั้งหมดอยู่ที่เดียว (รวม mandate ของ PM และเกณฑ์แจ้งเตือน)
  storage.py         ชั้นเก็บข้อมูล + บังคับ fetched_at ทุกไฟล์
  market.py          เวลาตลาดสหรัฐ: แท่งที่ปิดแล้ว · แผนมีผลเมื่อไหร่
  ledger.py          paper baseline 3 กลยุทธ์
  headless.py        เรียก Claude แบบไม่มีเครื่องมือ ได้ JSON ตาม schema
  notify.py          แจ้งเตือน (เดสก์ท็อป · ntfy ถ้าเปิด)
  journal.py         สร้าง prediction / hypothesis
  cli.py             จุดเข้าใช้งาน
  collect/
    prices.py        yfinance (+ ดึงย้อนหลังให้ ticker ที่ประวัติสั้น)
    news.py          รวมข่าวจาก sources/ (rss · sec · gnews · gdelt)
    calendar.py      วันประกาศงบ + ประมาณการนักวิเคราะห์ (snapshot รายวัน)
    fundamentals.py  งบ SEC XBRL แบบ point-in-time → TTM, margin, P/E, FCF
  pm/                Portfolio Manager — ดูโน้ต 11
    context.py       ทุกอย่างที่ PM ได้อ่าน (0 token)
    schema.py        รูปแบบแผน + ตัวตรวจกติกา
    runner.py        ถึงเวลาไหม → เรียก → ตรวจ → เก็บ → แจ้ง
    portfolio.py     พอร์ตกระดาษ llm_pm vs pm_control
    predictions.py   ตัดสินคำทำนายด้วยราคา + calibration
    watch.py         เฝ้าระวังรายชั่วโมง
  brief/
    context.py       สร้าง context pack (JSON) — ไม่เรียก LLM
    render.py        แปลงเป็น Markdown สำหรับ Obsidian

finlab/              harness วิจัย (IC, walk-forward, purged CV, DSR, PBO, ประตู 1)

data/
  raw/prices/*.parquet     snapshot ราคา ห้ามแก้
  raw/news/*.jsonl         ข่าวดิบ ห้ามแก้
  raw/calendar/*.jsonl     ปฏิทินงบ + ประมาณการ (ย้อนหลังหาฟรีไม่ได้)
  raw/fundamentals/        fact จาก SEC XBRL (วันที่ยื่น = knowledge date)
  raw/_manifest.jsonl      audit trail ว่าดึงอะไรตอนไหน
  labels/                  label ข่าว (สร้างย้อนหลังไม่ได้)
  plans/                   แผนที่ผ่านการตรวจ + requests/ (พรอมต์จริง) + resolutions.jsonl
  alerts/                  ประวัติการแจ้งเตือน
  research/                ราคาของ universe วิจัย + experiments.jsonl (ดาวน์โหลดใหม่ได้)
  derived/                 คำนวณใหม่ได้เสมอ ลบทิ้งได้ (รวม pm_ledger.parquet)
  logs/                    log ของงานอัตโนมัติ

Journal/                   โฟลเดอร์ที่ Obsidian อ่าน
  Briefs/                  situation brief รายวัน
  Plans/                   แผนรายสัปดาห์ของ PM
  Alerts/                  การแจ้งเตือนรายวัน
  Research/                ผลจาก harness
  Predictions/             คำทำนาย (BG-1)
  Hypotheses/              สมมติฐานรอทดสอบ (BG-4)
```

---

## หลักการที่ยึดในโค้ด

1. **ทุกไฟล์ที่เขียนต้องมี `fetched_at`** — คือ `knowledge_date` ที่ป้องกัน look-ahead bias
2. **ไม่เขียนทับข้อมูลดิบ** — ไฟล์ชื่อซ้ำจะเติม `__v2` แทน เพราะข้อมูลที่เก็บแล้วคือหลักฐาน
3. **ทุกคำสั่งรันซ้ำได้ปลอดภัย** — cron อาจรันซ้ำ ต้องไม่ทำให้ข้อมูลเพี้ยน
4. **ledger คำนวณใหม่ทั้งหมดทุกครั้ง** — ถ้าเคยรันพลาดไปวันนึง ระบบซ่อมตัวเองได้
5. **ขั้นตอนที่พึ่งบริการภายนอกต้องขาดได้** — ข่าวดึงไม่ได้ ต้องไม่ทำให้ราคาเสียไปด้วย

---

## แหล่งข่าว — 4 แหล่ง ไม่ต้องใช้ API key เลย

> [!note] เปลี่ยน 2026-10-24: ปิด `yahoo` (คืนค่าว่างทุกวันทั้ง yfinance 1.5.2 และ 1.7.0)
> แทนด้วย `gnews` — Google News RSS ค้นรายบริษัททั้ง 24 ตัว (aggregator, ≤ 15 ชิ้น/บริษัท/วัน)
> SEC ได้หัวข้อ 8-K (Item) ในพาดหัว และใช้เวลาที่ SEC รับเอกสารจริงแทนวันที่ยื่น

ออกแบบให้ **แหล่งหนึ่งล้มไม่กระทบแหล่งอื่น** (แต่ละ source ห่อด้วย try/except แยกกัน)
ตอนทดสอบ GDELT โดนแบนทั้งวัน แต่ยังเก็บข่าวได้ 126 รายการจาก 3 แหล่งที่เหลือ

| แหล่ง | ชนิด | จุดแข็ง | จุดอ่อน |
|-------|------|---------|---------|
| `yahoo` | outlet | ปริมาณดี เร็ว ใช้ dep ที่มีอยู่ | ปนบทความชวนคลิก · **ผูก ticker มั่ว** |
| `rss` | outlet + **primary** | เสถียรสุด แทบไม่มี rate limit | จำนวนจำกัด |
| `sec` | **primary** | **8-K = เหตุการณ์สำคัญตามกฎหมาย** | ไม่ใช่ "ข่าว" |
| `gdelt` | aggregator | กว้างสุด นานาชาติ | rate limit เข้มมาก |

เปิด/ปิดแหล่งได้ที่ `NEWS_SOURCES` ใน `config.py` หรือชั่วคราว:

```bash
uv run finbg news --sources yahoo,rss,sec    # ข้าม gdelt
```

### `source_type` สำคัญกว่าที่คิด

- `primary` — เอกสารต้นทาง (SEC filing, ประกาศ Fed) = **ข้อเท็จจริงที่ต้องแจ้งตามกฎหมาย**
- `outlet` — สื่อรายงานเอง = การตีความ
- `aggregator` — รวบรวมต่อ

ไม่ควรนับน้ำหนักเท่ากัน — brief จึงแสดง primary ก่อนเสมอ
และตอนตัดข่าวซ้ำ **เก็บ record จากแหล่งที่น่าเชื่อถือกว่าไว้** (primary > outlet > aggregator)

### ⚠️ `tickers` กับ `tickers_hint`

บทเรียนจากข้อมูลจริง: Yahoo คืนข่าว "P&G Guided Below Estimates" มาในผลของ **NVDA**
เพราะมันคือ "ข่าวที่แสดงบนหน้า NVDA" ไม่ใช่ "ข่าวเกี่ยวกับ NVDA"

| ฟิลด์ | ความหมาย |
|-------|----------|
| `tickers` | **ยืนยันแล้ว** — ชื่อย่อหรือชื่อบริษัทปรากฏในพาดหัวจริง |
| `tickers_hint` | แหล่งบอกมาแต่ยังไม่ยืนยัน — ใช้เป็นคำใบ้เท่านั้น |

ถ้าปล่อยให้การผูกผิดเข้าคลัง งาน text alpha ที่สร้างบนคลังนี้จะเสียทั้งหมด
ตอน label จึงย้ำในคำสั่งว่าห้ามเชื่อ `tickers_hint` โดยอัตโนมัติ

### GDELT rate limit

จากการทดสอบ: ยิงห่าง 6 วินาทียังโดน HTTP 429 และเมื่อโดนแล้วติดค้างนานหลายสิบนาที
ระบบจึงเว้น **15 วินาที** · รวมคำค้นด้วย `OR` เหลือ **4 requests/วัน** ·
มี **circuit breaker** (คำแรกไม่ผ่าน หยุดทั้งรอบ) · **เพดานเวลา 240 วินาที**

ถ้าอยากได้ปริมาณมากกว่านี้ GDELT แนะนำ BigQuery แทน DOC API

---

## ⚠️ การสำรองข้อมูล

`scripts/backup.sh` รันอัตโนมัติทุก 7 วัน (anacron) สำรองของที่ **สร้างย้อนหลังไม่ได้**:

| สำรอง | เพราะ |
|-------|-------|
| `data/raw/` | ราคาที่ปรับแล้วเปลี่ยนย้อนหลัง · URL ข่าวเน่าตามเวลา |
| `data/labels/` | label ที่ทำย้อนหลัง = ปนเปื้อน hindsight |
| `data/plans/` | แผนรายสัปดาห์ + พรอมต์ที่ใช้จริง + ผลคำทำนาย = track record ของ PM |
| `data/alerts/` | ประวัติการแจ้งเตือน (เหตุผลของทุก replan) |
| `Journal/` | คำทำนาย บทวิเคราะห์ สมมติฐาน — งานสมองล้วน |

ไม่สำรอง `data/derived/` เพราะคำนวณใหม่ได้จาก raw เสมอ

> [!warning]
> ปลายทางเริ่มต้นคือ `~/backups/finbg` ซึ่ง **อยู่บนดิสก์เดียวกัน**
> — กันลบพลาดได้ แต่**กันดิสก์พังไม่ได้**
>
> ควรเปลี่ยนเป็นที่อื่น:
> ```bash
> export FINBG_BACKUP_DIR=/mnt/external/finbg     # external drive
> ```
> หรือแก้ค่าเริ่มต้นใน `scripts/backup.sh`

สคริปต์ **ไม่ใช้ `--delete`** โดยตั้งใจ — ถ้าไฟล์ต้นทางหายเพราะอุบัติเหตุ
สำเนาสำรองต้องยังอยู่
