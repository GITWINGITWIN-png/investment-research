# finbg — Background Track

ระบบเก็บข้อมูลและสรุปสถานการณ์แบบอัตโนมัติ สำหรับโปรเจกต์ AI Investing
พื้นหลังและเหตุผลอยู่ที่ [`AI-Investing-Research/10 - Background Track.md`](AI-Investing-Research/10%20-%20Background%20Track.md)

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
06:00  daily.sh        เก็บราคา + ข่าว + ledger + brief
06:30  label-auto.sh   มีข่าวรอไหม? (0 token) → ถ้ามี เปิด claude -p แบบ headless
```

ปิด Claude Code ปิดหน้าจอได้ตามปกติ — cron ของระบบเป็นคนเรียก ไม่ผูกกับ session ใด

**ทำไม 06:30 ไม่ใช่กลางคืน:** ข่าวเข้าคลังตอน 06:00 เท่านั้น รันห่างจากนั้นเท่าไหร่
ข่าวก็เก่าเท่านั้น · 06:30 → อายุเฉลี่ย ~12.5 ชม. · 23:10 → ~29 ชม.

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
| **L1** | เติมบทวิเคราะห์ใน brief | ❌ ปนเปื้อน hindsight ทันที |
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
| ทุกวัน | cron รัน `daily` เอง | 0 |
| ทุกสัปดาห์ | เขียน prediction + เติมวิเคราะห์ใน brief (L1) + label ข่าว (L2) | น้อย-กลาง |
| ทุกเดือน | ทดสอบ hypothesis 1 ใบ (L3) | หลัก |
| เมื่อ token เหลือ | สรุป paper (L4) | เท่าไหร่ก็ได้ |

### L1 — ให้ Claude เติมส่วนวิเคราะห์

หลัง `finbg brief` เสร็จ เปิด Claude Code แล้วสั่ง:

```
อ่าน data/derived/context/YYYY-MM-DD.json
แล้วเติมหัวข้อ "การวิเคราะห์" ใน Journal/Briefs/YYYY-MM-DD.md
```

Claude อ่าน JSON (กระชับ ออกแบบมาให้ LLM อ่าน) แทนการไล่อ่านไฟล์ดิบทั้งหมด

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

## cron ที่ติดตั้งแล้ว

```cron
0 6 * * *   /home/you/investment-research/scripts/daily.sh    # ทุกวัน ตี 6
30 7 * * 0  /home/you/investment-research/scripts/backup.sh   # อาทิตย์ 07:30
```

ตรวจสอบว่าทำงาน:

```bash
tail -f data/logs/daily-$(date +%Y-%m).log
tail -f data/logs/backup-$(date +%Y).log
```

---

## โครงสร้าง

```
finbg/
  config.py          ค่าตั้งทั้งหมดอยู่ที่เดียว
  storage.py         ชั้นเก็บข้อมูล + บังคับ fetched_at ทุกไฟล์
  ledger.py          paper baseline 3 กลยุทธ์
  journal.py         สร้าง prediction / hypothesis
  cli.py             จุดเข้าใช้งาน
  collect/
    prices.py        yfinance
    news.py          GDELT (ไม่ต้องมี API key)
  brief/
    context.py       สร้าง context pack (JSON) — ไม่เรียก LLM
    render.py        แปลงเป็น Markdown สำหรับ Obsidian

data/
  raw/prices/*.parquet     snapshot ราคา ห้ามแก้
  raw/news/*.jsonl         ข่าวดิบ ห้ามแก้
  raw/_manifest.jsonl      audit trail ว่าดึงอะไรตอนไหน
  derived/                 คำนวณใหม่ได้เสมอ ลบทิ้งได้
  logs/                    log ของ cron

Journal/                   โฟลเดอร์ที่ Obsidian อ่าน
  Briefs/                  situation brief รายวัน
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

`scripts/backup.sh` รันอัตโนมัติทุกวันอาทิตย์ สำรอง 3 อย่างที่ **สร้างย้อนหลังไม่ได้**:

| สำรอง | เพราะ |
|-------|-------|
| `data/raw/` | ราคาที่ปรับแล้วเปลี่ยนย้อนหลัง · URL ข่าวเน่าตามเวลา |
| `data/labels/` | label ที่ทำย้อนหลัง = ปนเปื้อน hindsight |
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
