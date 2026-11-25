"""Labeled corpus: turns raw news into structured labels (L2 on the token-use ladder).

## Why this has to happen now, not later

Raw news is collected for free by code, but labels cost tokens, and more importantly:

**If August 2026 news is labelled in 2027, the model already knows the outcome, and the labels are contaminated by hindsight.**

The `novelty` field in particular (is this really new, or a repeat) depends
on "what was known at the time" and cannot be done afterwards at all.

So the labeled corpus is **an asset that cannot be bought or rebuilt later**,
   and it is the raw material for text alpha in phase 3.

## Architecture (designed around the limits of Claude Pro)

The owner uses Claude Pro without a separate API plan, so cron cannot call the LLM itself.
The work is split into 3 steps:

    1. finbg label prepare   -> prepare a batch (plain code, free)
    2. open Claude Code and label  -> the only step that uses tokens
    3. finbg label ingest    -> validate and store (plain code, free)

A side benefit: step 2 can run whenever tokens are left over, which fits the goal
of "using spare tokens before they expire".

## Where the files live

Labels are stored in `data/labels/`, **not `data/derived/`**, on purpose,
because derived data can be deleted and rebuilt, but labels cannot be honestly rebuilt,
so they must always be backed up together with the raw data.
"""

from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

from . import config, storage

SCHEMA_VERSION = 1

# --- Schema definition: the single source of truth ---------------------------------------
# Both the prompt sent to the LLM and the validator are built from here,
# so the two cannot drift apart when the schema changes later

ENUMS: dict[str, list[str]] = {
    "regime": ["risk_on", "risk_off", "neutral"],
    "novelty": ["new", "developing", "repetition"],
    "topic": [
        "monetary_policy",
        "macro_data",
        "earnings",
        "company_specific",
        "geopolitics",
        "market_structure",
        "other",
    ],
    "horizon": ["intraday", "days", "weeks", "months"],
}

# Accepted effort levels; optional, because in some environments the model does not know this value
EFFORT_LEVELS = ["low", "medium", "high", "xhigh", "max"]

# No whitelist of model names, because new models keep coming out;
# enforcing a list would break the system with every release, so only the format is checked
MODEL_MAX_LEN = 64

FIELD_HELP: dict[str, str] = {
    "model": "**model ID ที่แน่นอนของคุณเอง** เช่น claude-opus-5 · "
             "จำเป็นต่อการตรวจ drift ของคลัง — ห้ามเดา ห้ามใส่ชื่อเล่น",
    "effort": "ระดับ effort ที่โปรเจกต์ตั้งไว้ — ใส่ตามที่ระบุในหัวข้อ 'สิ่งที่ต้องทำ' ห้ามเดาเอง",
    "regime": "ข่าวนี้ผลักตลาดไปทาง risk_on (กล้าเสี่ยง) หรือ risk_off (หนีความเสี่ยง)",
    "novelty": "new = ข้อมูลใหม่จริง · developing = เรื่องเดิมที่คืบหน้า · repetition = ย้ำของเดิม",
    "topic": "หมวดหลักของข่าว",
    "topic_free": "หัวข้อจริงแบบสั้น ๆ 2-6 คำ (ภาษาไทยหรืออังกฤษก็ได้) "
                  "— ใส่ไว้กันข้อมูลหายเวลาที่ topic ถูกยัดเป็น other",
    "horizon": "คาดว่าผลกระทบกินเวลานานแค่ไหน",
    "materiality": "0-100 · ข่าวส่วนใหญ่ควรต่ำกว่า 30 · เกิน 70 คือข่าวที่ขยับตลาดจริง",
    "entities": "หุ้นที่ข่าวนี้กระทบ พร้อมทิศทาง (positive/negative/neutral) และความมั่นใจ 0-100",
    "rationale": "เหตุผลสั้น ๆ 1 บรรทัด ว่าทำไมถึง label แบบนี้",
}

ENTITY_DIRECTIONS = ["positive", "negative", "neutral"]


def schema_prompt() -> str:
    """Build the schema description that goes into the labelling instructions.

    Built directly from ENUMS/FIELD_HELP, so the prompt follows any schema change made in one place.
    """
    lines = ["ฟิลด์ที่ต้องใส่ให้ครบทุกบทความ:", ""]
    for field, help_text in FIELD_HELP.items():
        if field in ENUMS:
            allowed = " | ".join(ENUMS[field])
            lines.append(f"- `{field}` — เลือก 1 จาก: {allowed}")
            lines.append(f"    {help_text}")
        else:
            lines.append(f"- `{field}` — {help_text}")
    return "\n".join(lines)


# --- Validation ------------------------------------------------------

def validate(record: dict[str, Any]) -> list[str]:
    """Validate one label; returns a list of errors (empty = pass).

    Strict on purpose: a malformed label found after it entered the store cannot be fixed
    (labelling it again would already be contaminated by hindsight).
    """
    errors: list[str] = []

    if not record.get("url"):
        errors.append("ไม่มี url (ใช้เป็น key เชื่อมกลับไปข่าวดิบ)")

    # Provenance: always know which model produced a label
    # If the corpus mixes models unnoticed, there are discontinuities that look like real signals
    # but are only a change of model, and they cannot be debugged afterwards
    model = record.get("model")
    if not model or not str(model).strip():
        errors.append("ขาด model (ต้องระบุ model ID ที่ label ข่าวนี้)")
    elif len(str(model)) > MODEL_MAX_LEN:
        errors.append(f"model ยาวเกิน {MODEL_MAX_LEN} ตัวอักษร — น่าจะใส่ผิดช่อง")

    effort = record.get("effort")
    if effort is not None and effort not in EFFORT_LEVELS:
        errors.append(f"effort='{effort}' ไม่อยู่ใน {EFFORT_LEVELS} (เว้นว่างได้ถ้าไม่รู้)")

    for field, allowed in ENUMS.items():
        value = record.get(field)
        if value is None:
            errors.append(f"ขาดฟิลด์ {field}")
        elif value not in allowed:
            errors.append(f"{field}='{value}' ไม่อยู่ใน {allowed}")

    topic_free = record.get("topic_free")
    if not topic_free or not str(topic_free).strip():
        errors.append("ขาด topic_free")
    elif len(str(topic_free)) > 80:
        errors.append("topic_free ยาวเกิน 80 ตัวอักษร (ควรสั้น 2-6 คำ)")

    materiality = record.get("materiality")
    if not isinstance(materiality, (int, float)) or not 0 <= materiality <= 100:
        errors.append("materiality ต้องเป็นตัวเลข 0-100")

    if not record.get("rationale"):
        errors.append("ขาด rationale")

    entities = record.get("entities")
    if not isinstance(entities, list):
        errors.append("entities ต้องเป็น list (ใส่ [] ได้ถ้าไม่เกี่ยวกับหุ้นตัวไหน)")
    else:
        for i, entity in enumerate(entities):
            if not isinstance(entity, dict):
                errors.append(f"entities[{i}] ต้องเป็น object")
                continue
            if not entity.get("ticker"):
                errors.append(f"entities[{i}] ขาด ticker")
            if entity.get("direction") not in ENTITY_DIRECTIONS:
                errors.append(f"entities[{i}].direction ต้องอยู่ใน {ENTITY_DIRECTIONS}")
            confidence = entity.get("confidence")
            if not isinstance(confidence, (int, float)) or not 0 <= confidence <= 100:
                errors.append(f"entities[{i}].confidence ต้องเป็น 0-100")

    return errors


# --- Label store --------------------------------------------------------------

def _labels_dir() -> Path:
    path = config.DATA / "labels"
    path.mkdir(parents=True, exist_ok=True)
    return path


def published_at_index() -> dict[str, str]:
    """Map url -> published_at from the **whole** raw news store (including labelled items).

    The whole store must be scanned, not only unlabelled news, because:
    - on a repeated ingest the item is already labelled, so it is not in the "unlabelled" list
    - a backfill needs the dates of items labelled long ago
    Using only the unlabelled list would give empty values, and days_after_publish would silently become null.
    """
    index: dict[str, str] = {}
    for path in sorted(config.RAW_NEWS.glob("*.jsonl")):
        with path.open(encoding="utf-8") as fh:
            for line in fh:
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                url = record.get("url", "")
                published = record.get("published_at", "") or record.get("seendate", "")
                if url and published:
                    index.setdefault(url, published)
    return index


def days_between(published_raw: str, labeled_raw: str) -> int | None:
    """Days between publication and labelling; None if a date cannot be parsed.

    A separate function so ingest and backfill use exactly the same logic;
    otherwise backfilled values might not match the values computed originally.
    """
    if not published_raw or not labeled_raw:
        return None
    try:
        published = datetime.fromisoformat(published_raw)
        labeled = datetime.fromisoformat(labeled_raw)
    except ValueError:
        return None
    if published.tzinfo is None:
        published = published.replace(tzinfo=config.UTC)
    if labeled.tzinfo is None:
        labeled = labeled.replace(tzinfo=config.UTC)
    return (labeled - published).days


def labeled_urls() -> set[str]:
    """URLs already labelled, used to avoid labelling twice (wasted tokens)"""
    urls: set[str] = set()
    for path in _labels_dir().glob("*.jsonl"):
        with path.open(encoding="utf-8") as fh:
            for line in fh:
                try:
                    urls.add(json.loads(line).get("url", ""))
                except json.JSONDecodeError:
                    continue
    urls.discard("")
    return urls


def is_high_signal(record: dict[str, Any]) -> bool:
    """Whether this news item is worth labelling.

    Criterion: an original document **or** confirmed to be about a watchlist stock.

    Why filter (see config.LABEL_HIGH_SIGNAL_ONLY): cron collects many times more news
    than can be labelled, and most of what is cut is clickbait
    and announcements unrelated to our universe, which would get low materiality anyway.
    So filtering makes the corpus **better**, not worse.
    """
    if record.get("source_type") in config.LABEL_PRIMARY_TYPES:
        return True
    return bool(record.get("tickers"))


_SOURCE_TYPE_RANK = {"primary": 0, "outlet": 1, "aggregator": 2}


def sector_of(record: dict[str, Any]) -> str:
    """Sector of a news item, from its first confirmed ticker.

    `tickers` is already sorted in normalize(), so the result is the same on every run.
    A story about several sectors counts for the first sector alphabetically, a decision
    that is imperfect but consistent; it is recorded as a known limitation.

    News tied to no stock (Fed, economic data, SEC filings outside the watchlist)
    goes into a "macro" bucket with its own quota, so Fed releases cannot crowd out stock news.
    """
    for ticker in record.get("tickers") or []:
        sector = config.TICKER_SECTOR.get(ticker)
        if sector:
            return sector
    return "macro"


def _label_priority(record: dict[str, Any]) -> tuple[int, str, str]:
    """Labelling priority within one group.

    At selection time we **do not know the materiality yet**, because that is what labelling produces,
    so a proxy known in advance is used: original document > outlet > aggregator,
    then time, and finally url, so the result is the same on every run.
    """
    return (
        _SOURCE_TYPE_RANK.get(record.get("source_type", ""), 9),
        record.get("published_at", ""),
        record.get("url", ""),
    )


def _quota_eligible(records: list[dict[str, Any]], quota: int) -> set[str]:
    """URLs that pass the quota: at most `quota` items per (publication date, sector).

    ## Why a permanent cap, not just an ordering

    If each batch were only balanced, the loop would keep going until everything was labelled anyway,
    which means (a) cost grows with the number of tickers and (b) the mix tilts back towards big tech,
    because that group naturally has many times more news than the others.

    ## Why the ranking includes already labelled news

    Ranking only unlabelled news would let the cap expand every round:
    label 6 items and the next 6 move up into the top 6, forever.
    """
    buckets: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        day = (record.get("published_at") or "")[:10]
        buckets[(day, sector_of(record))].append(record)

    eligible: set[str] = set()
    for group in buckets.values():
        group.sort(key=_label_priority)
        eligible.update(r["url"] for r in group[:quota])
    return eligible


def _candidates(high_signal_only: bool) -> list[dict[str, Any]]:
    """All news that passes the filter, including labelled items (needed for the quota ranking).

    The raw news store is a "log of fetches", not a unique table, on purpose:
    point-in-time data must not be overwritten, so every cron fetch is a full snapshot with its own fetched_at.
    The same story therefore appears in several files as normal, and the reader deduplicates, not the writer.
    The first one found is kept (files sorted by name = by time), because the earliest fetched_at
    is when we really learned about it, which is the value point-in-time needs.
    """
    seen: set[str] = set()
    out: list[dict[str, Any]] = []

    for path in sorted(config.RAW_NEWS.glob("*.jsonl")):
        with path.open(encoding="utf-8") as fh:
            for line in fh:
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                url = record.get("url", "")
                if not url or url in seen:
                    continue
                seen.add(url)
                if high_signal_only and not is_high_signal(record):
                    continue
                out.append({
                    "url": url,
                    "title": record.get("title", ""),
                    "publisher": record.get("publisher", "") or record.get("domain", ""),
                    "source_type": record.get("source_type", ""),
                    "published_at": record.get("published_at", "") or record.get("seendate", ""),
                    "tickers": record.get("tickers", []),
                    "tickers_hint": record.get("tickers_hint", []),
                    "fetched_at": record.get("fetched_at", ""),
                    "source_file": path.name,
                })
    return out


def unlabeled_articles(
    limit: int | None = None,
    high_signal_only: bool | None = None,
    quota: int | None = None,
) -> list[dict[str, Any]]:
    """Unlabelled news that passes the per-sector quota, oldest first.

    Oldest first on purpose: unlabelled old news keeps "decaying",
    because the longer it waits, the more likely we already know the outcome.

    `quota=0` = quota off (label everything that passes the filter), the behaviour before sectors were added.
    """
    if high_signal_only is None:
        high_signal_only = config.LABEL_HIGH_SIGNAL_ONLY
    if quota is None:
        quota = config.LABEL_SECTOR_QUOTA

    candidates = _candidates(high_signal_only)
    eligible = _quota_eligible(candidates, quota) if quota else None

    done = labeled_urls()
    pending = [
        record for record in candidates
        if record["url"] not in done and (eligible is None or record["url"] in eligible)
    ]
    pending.sort(key=lambda r: (r.get("published_at", ""), r["url"]))

    return pending[:limit] if limit else pending


def prepare(batch_size: int = 40, high_signal_only: bool | None = None) -> dict[str, Any]:
    """Prepare one batch file of labelling instructions; no tokens used yet.

    Only the data needed for labelling (headline, source, date) is included, not the whole raw record,
    because every character in the batch is a token to pay for.
    """
    storage.ensure_dirs()
    articles = unlabeled_articles(limit=batch_size, high_signal_only=high_signal_only)
    if not articles:
        return {"ok": False, "reason": "ไม่มีข่าวที่รอ label"}

    queue_dir = config.DATA / "queue"
    queue_dir.mkdir(parents=True, exist_ok=True)

    stamp = storage.utc_now().strftime("%Y-%m-%dT%H%M")
    request_path = queue_dir / f"label-request-{stamp}.md"
    response_path = queue_dir / f"label-response-{stamp}.jsonl"

    payload = "\n".join(
        json.dumps(
            {k: a[k] for k in
             ("url", "title", "publisher", "source_type", "published_at",
              "tickers", "tickers_hint")},
            ensure_ascii=False,
        )
        for a in articles
    )

    request_path.write_text(
        f"""# คำสั่ง label ข่าว — batch {stamp}

**บทความ {len(articles)} ชิ้น · schema v{SCHEMA_VERSION}**

| ตั้งค่าโปรเจกต์ | ค่า |
|---|---|
| โมเดลที่ตั้งใจใช้ | `{config.LABEL_MODEL}` |
| effort ที่ตั้งไว้ | `{config.LABEL_EFFORT}` ← ใส่ค่านี้ในทุกบรรทัด |

## สิ่งที่ต้องทำ

อ่านบทความข้างล่าง แล้วเขียนไฟล์ `{response_path.name}` ในโฟลเดอร์เดียวกัน
เป็น JSONL — **1 บรรทัดต่อ 1 บทความ** ครบทุกชิ้น ไม่ข้าม

{schema_prompt()}

## กฎสำคัญ

1. **ตัดสินจากสิ่งที่รู้ ณ วันที่ข่าวออกเท่านั้น** ห้ามใช้ความรู้ว่าเกิดอะไรขึ้นภายหลัง
   — นี่คือหัวใจของ labeled corpus ถ้าละเมิดข้อนี้ ข้อมูลทั้งชุดใช้ไม่ได้
2. `materiality` ให้เข้มงวด — ข่าวส่วนใหญ่ไม่สำคัญ ควรต่ำกว่า 30
3. `entities` ใส่เฉพาะหุ้นที่ข่าว**กระทบจริง** ไม่ใช่แค่ถูกเอ่ยชื่อผ่าน ๆ
4. ถ้าไม่แน่ใจ ให้เลือกค่ากลาง (`neutral`) ดีกว่าเดา
5. `rationale` สั้น ๆ 1 บรรทัด อย่ายาว
6. **`tickers_hint` คือคำใบ้ที่ยังไม่ยืนยัน** — แหล่งข่าวบอกมาว่าอาจเกี่ยวข้อง
   แต่มักผิด (เช่น Yahoo แสดงข่าวตลาดทั่วไปบนหน้าของหุ้นแต่ละตัว)
   **อย่าเชื่อโดยอัตโนมัติ** ให้ตัดสินจากพาดหัวเอง ส่วน `tickers` คือที่ยืนยันแล้ว
7. `source_type: primary` = เอกสารต้นทาง (SEC filing, ประกาศธนาคารกลาง)
   เป็นข้อเท็จจริงที่ต้องแจ้งตามกฎหมาย ไม่ใช่การตีความ — น่าเชื่อถือกว่าข่าวทั่วไป
8. **`model` ต้องเป็น model ID จริงของคุณ** และต้องเหมือนกันทุกบรรทัด
   โปรเจกต์นี้ตั้งใจใช้ **`{config.LABEL_MODEL}`** ตลอดทั้งคลัง
   - ถ้าคุณคือโมเดลนั้น → ใส่ค่านั้น
   - **ถ้าคุณเป็นโมเดลอื่น → ใส่ ID จริงของคุณ แล้วบอกผู้ใช้ว่าไม่ตรงกับที่ตั้งใจไว้**
     อย่าคัดลอกค่าข้างบนถ้ามันไม่ใช่คุณ — ทั้งหมดนี้มีไว้เพื่อตรวจ drift
   - ไม่แน่ใจว่า ID ตัวเองคืออะไร → **ถามผู้ใช้ อย่าเดา**

## ตัวอย่าง 1 บรรทัด

```json
{{"url":"https://...","model":"{config.LABEL_MODEL}","effort":"{config.LABEL_EFFORT}","regime":"risk_off","novelty":"new","topic":"monetary_policy","topic_free":"Fed ส่งสัญญาณคงดอกเบี้ยนานขึ้น","horizon":"weeks","materiality":68,"entities":[{{"ticker":"SPY","direction":"negative","confidence":60}}],"rationale":"ถ้อยแถลงเข้มกว่าที่ตลาดคาด"}}
```

> ℹ️ **ความสม่ำเสมอสำคัญกว่าคุณภาพสูงสุด**
> คลัง label ที่ปนหลายโมเดลจะมีรอยต่อที่หน้าตาเหมือนสัญญาณตลาดจริง
> ทั้งที่เป็นแค่การเปลี่ยนโมเดล — ใช้โมเดลเดิมตลอดทั้งคลังถ้าเป็นไปได้

## บทความ

```jsonl
{payload}
```
""",
        encoding="utf-8",
    )

    return {
        "ok": True,
        "articles": len(articles),
        "model_expected": config.LABEL_MODEL,
        "filtered": high_signal_only if high_signal_only is not None
        else config.LABEL_HIGH_SIGNAL_ONLY,
        "request": str(request_path.relative_to(config.ROOT)),
        "response_expected": str(response_path.relative_to(config.ROOT)),
        "remaining_after": len(unlabeled_articles(high_signal_only=high_signal_only))
        - len(articles),
    }


def ingest(response_path: Path | str | None = None) -> dict[str, Any]:
    """Read an answer file, validate it, and store it.

    Labels that fail validation are **not stored**, and the reasons are reported.
    Losing the tokens already spent is better than letting bad data into a permanent store.
    """
    queue_dir = config.DATA / "queue"

    if response_path is None:
        candidates = sorted(queue_dir.glob("label-response-*.jsonl"))
        if not candidates:
            return {"ok": False, "reason": "ไม่พบไฟล์คำตอบใน data/queue/"}
        target = candidates[-1]
    else:
        target = Path(response_path)
        if not target.is_absolute():
            target = config.ROOT / target

    if not target.exists():
        return {"ok": False, "reason": f"ไม่พบไฟล์ {target}"}

    published_at = published_at_index()

    # Last gate before the permanent write: no second label for the same URL
    # Needed because ingest appends; once a duplicate is in, the file has to be fixed by hand
    # Duplicates can come two ways: (a) the answer file has the same URL on two lines, (b) the same file is ingested twice
    # Moving files into done/ only prevents (b) in the normal case, not when --path is given by hand
    already = labeled_urls()
    seen_in_file: set[str] = set()

    accepted: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    now = storage.utc_now()

    with target.open(encoding="utf-8") as fh:
        for line_no, line in enumerate(fh, start=1):
            line = line.strip()
            if not line or line.startswith("```"):
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError as exc:
                rejected.append({"line": line_no, "errors": [f"JSON เสีย: {exc}"]})
                continue

            errors = validate(record)
            if errors:
                rejected.append({"line": line_no, "url": record.get("url", "")[:60], "errors": errors})
                continue

            # Duplicates are checked after validation passes, so url is known to exist and be a string
            url = record["url"]
            if url in already:
                rejected.append({"line": line_no, "url": url[:60], "errors": ["ซ้ำ: URL นี้อยู่ในคลังแล้ว"]})
                continue
            if url in seen_in_file:
                rejected.append({"line": line_no, "url": url[:60], "errors": ["ซ้ำ: URL นี้โผล่มาแล้วในไฟล์เดียวกัน"]})
                continue
            seen_in_file.add(url)

            # How many days after publication the label was made; the more days, the higher the risk of hindsight contamination
            labeled_at = storage.utc_stamp()
            days_after = days_between(published_at.get(record["url"], ""), labeled_at)
            if days_after is None:
                print(f"  ⚠️  ไม่พบวันที่ของ {record['url'][:60]} → เติมภายหลังด้วย backfill ได้")

            accepted.append({
                "schema_version": SCHEMA_VERSION,
                "labeled_at": labeled_at,
                "days_after_publish": days_after,
                **record,
            })

    if not accepted:
        return {"ok": False, "reason": "ไม่มี label ที่ผ่านการตรวจ", "rejected": rejected[:10]}

    out_path = _labels_dir() / f"{now.strftime('%Y-%m-%d')}.jsonl"
    with out_path.open("a", encoding="utf-8") as fh:
        for record in accepted:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")

    storage.log_manifest("labels", out_path, len(accepted), source=target.name)

    # Move ingested files out of the queue so they are not ingested twice
    done_dir = queue_dir / "done"
    done_dir.mkdir(exist_ok=True)
    target.rename(done_dir / target.name)

    return {
        "ok": True,
        "accepted": len(accepted),
        "rejected": len(rejected),
        "reject_details": rejected[:10],
        "path": str(out_path.relative_to(config.ROOT)),
        "remaining_unlabeled": len(unlabeled_articles()),
    }


def backfill(dry_run: bool = False) -> dict[str, Any]:
    """Backfill `days_after_publish` where it is null.

    ## Why this is allowed (when label content cannot be redone later)

    `days_after_publish` is not a judgement but **a number computed from two stored values**:
        days_after_publish = labeled_at - published_at
    Both are in the store (labeled_at in the label, published_at in the raw news),
    so it can always be recomputed, unlike `novelty`/`materiality`, which are already contaminated if labelled late.

    ## Safety

    - Touches only `days_after_publish` fields that are null; nothing else
    - Writes a temporary file and renames it over the original (atomic), so the file cannot break midway
    - Uses each record's own `labeled_at`, not the current time
      (otherwise a label made 3 days ago would get today's value, which is wrong)
    """
    index = published_at_index()
    total = filled = still_null = 0
    touched_files: list[str] = []

    for path in sorted(_labels_dir().glob("*.jsonl")):
        records: list[dict[str, Any]] = []
        changed = False

        with path.open(encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue

                total += 1
                if record.get("days_after_publish") is None:
                    days = days_between(
                        index.get(record.get("url", ""), ""),
                        record.get("labeled_at", ""),
                    )
                    if days is not None:
                        record["days_after_publish"] = days
                        filled += 1
                        changed = True
                    else:
                        still_null += 1
                records.append(record)

        if changed and not dry_run:
            tmp = path.with_suffix(".jsonl.tmp")
            with tmp.open("w", encoding="utf-8") as fh:
                for record in records:
                    fh.write(json.dumps(record, ensure_ascii=False) + "\n")
            tmp.replace(path)                       # atomic: no half-written file state
            touched_files.append(path.name)
        elif changed:
            touched_files.append(path.name)

    return {
        "ok": True,
        "dry_run": dry_run,
        "total": total,
        "filled": filled,
        "still_null": still_null,
        "files": touched_files,
    }


def stats() -> dict[str, Any]:
    """Summary of the label store, to see how much the asset has grown"""
    records: list[dict[str, Any]] = []
    for path in sorted(_labels_dir().glob("*.jsonl")):
        with path.open(encoding="utf-8") as fh:
            for line in fh:
                try:
                    records.append(json.loads(line))
                except json.JSONDecodeError:
                    continue

    if not records:
        return {"total": 0, "unlabeled": len(unlabeled_articles())}

    def counts(field: str) -> dict[str, int]:
        out: dict[str, int] = {}
        for record in records:
            value = record.get(field)
            if value is not None:
                out[str(value)] = out.get(str(value), 0) + 1
        return dict(sorted(out.items(), key=lambda kv: -kv[1]))

    fresh = [r for r in records if isinstance(r.get("days_after_publish"), int)
             and r["days_after_publish"] <= 2]

    by_model = counts("model")

    # Warn when the corpus mixes models, because the seams between models look like real market signals
    # in later analysis when they are not; this must be known before anything is built on the corpus
    warnings: list[str] = []
    if len(by_model) > 1:
        share = " · ".join(f"{m} {n}" for m, n in by_model.items())
        warnings.append(f"คลังปน {len(by_model)} โมเดล ({share}) — รอยต่ออาจดูเหมือนสัญญาณปลอม")

    stale = [r for r in records if isinstance(r.get("days_after_publish"), int)
             and r["days_after_publish"] > 7]
    if stale:
        warnings.append(
            f"{len(stale)} label ทำหลังข่าวออกเกิน 7 วัน — เสี่ยงปนเปื้อน hindsight"
        )

    return {
        "total": len(records),
        "unlabeled": len(unlabeled_articles()),
        "labeled_within_2_days": len(fresh),
        "by_model": by_model,
        "by_effort": counts("effort"),
        "schema_versions": counts("schema_version"),
        "warnings": warnings,
        "regime": counts("regime"),
        "novelty": counts("novelty"),
        "topic": counts("topic"),
        "high_materiality": len([r for r in records if (r.get("materiality") or 0) >= 70]),
    }
