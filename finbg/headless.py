"""เรียก Claude แบบ headless ที่ "ไม่มีเครื่องมือเลย" แล้วรับผลเป็น JSON ตาม schema

## ทำไมต่างจาก label-auto.sh

label-auto ให้โมเดลมีเครื่องมือ (Write + `uv run finbg label ...`) แล้วล้อมด้วย deny list
และ hook — จำเป็นเพราะงานนั้นวนหลายรอบในเซสชันเดียว

งานวางแผนไม่ต้องแตะไฟล์อะไรเลย: โค้ดเตรียมข้อมูลทั้งหมดใส่พรอมต์ โมเดลตอบเป็น JSON
แล้วโค้ดตรวจเอง จึงปิดเครื่องมือทั้งหมด (`--tools ""`) และบังคับรูปแบบคำตอบด้วย
`--json-schema` → **พาดหัวข่าวที่แฝงคำสั่ง (prompt injection) ไม่มีอะไรให้สั่ง**
ผลร้ายที่สุดที่ทำได้คือทำให้แผนบนกระดาษแย่ลง ภายในกรอบที่โค้ดบังคับอยู่แล้ว

## ทำไมล้าง environment ก่อนเรียก

ถ้าเรียกจากในเซสชัน Claude Code (เช่น ตอนเราทดสอบ) จะมีตัวแปร CLAUDECODE ติดมา
ซึ่งทำให้ CLI ปฏิเสธการเปิดเซสชันซ้อน — การส่ง environment แบบเดียวกับ cron
(มีแค่ HOME/PATH/LANG) ทำให้ผลเหมือนกันไม่ว่าจะเรียกจากไหน

## หลักฐานเรื่องโมเดล

ใช้ `modelUsage` ที่ harness คืนมา (โมเดลปลอมค่านี้ไม่ได้) — บทเรียนเดียวกับ label-auto
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

from . import config, storage


def _claude_binary() -> str | None:
    found = shutil.which("claude")
    if found:
        return found
    fallback = Path.home() / ".local" / "bin" / "claude"
    return str(fallback) if fallback.exists() else None


def _clean_env() -> dict[str, str]:
    env = {key: os.environ[key] for key in ("HOME", "USER", "LOGNAME", "LANG", "TZ") if key in os.environ}
    env.setdefault("LANG", "C.UTF-8")
    env["PATH"] = f"{Path.home() / '.local' / 'bin'}:/usr/local/bin:/usr/bin:/bin"
    return env


def run_structured(
    *,
    job: str,
    system_prompt: str,
    user_prompt: str,
    schema: dict[str, Any],
    model: str,
    effort: str,
    timeout_sec: int,
    max_budget_usd: float,
) -> dict[str, Any]:
    """รันเซสชันเดียว คืน dict ที่มี `ok`, `output` (JSON ที่ได้), หลักฐานเรื่องโมเดล และต้นทุน

    เก็บผลดิบทุกครั้งที่ data/logs/<job>-runs/ (ตรวจย้อนหลังได้ว่าโมเดลตอบอะไรจริง)
    """
    binary = _claude_binary()
    if binary is None:
        return {"ok": False, "error": "ไม่พบคำสั่ง claude (ต้องติดตั้ง Claude Code CLI)"}

    audit_dir = config.DATA / "logs" / f"{job}-runs"
    audit_dir.mkdir(parents=True, exist_ok=True)
    stamp = storage.utc_now().strftime("%Y-%m-%dT%H%M%S")
    audit_path = audit_dir / f"{stamp}.json"

    # ไดเรกทอรีว่างโดยเฉพาะ: ไม่ให้ตั้งค่า/หน่วยความจำของโปรเจกต์หลุดเข้าไปในบริบทของ PM
    workdir = config.DATA / "headless"
    workdir.mkdir(parents=True, exist_ok=True)

    command = [
        binary, "-p",
        "--model", model,
        "--effort", effort,
        "--tools", "",
        "--json-schema", json.dumps(schema, ensure_ascii=False),
        "--output-format", "json",
        "--no-session-persistence",
        "--system-prompt", system_prompt,
        "--max-budget-usd", f"{max_budget_usd:.2f}",
    ]

    started = time.monotonic()
    try:
        proc = subprocess.run(
            command, input=user_prompt, capture_output=True, text=True,
            timeout=timeout_sec, cwd=workdir, env=_clean_env(),
        )
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": f"ชนเพดานเวลา {timeout_sec}s", "audit": str(audit_path)}
    duration = time.monotonic() - started

    audit_path.write_text(proc.stdout or "", encoding="utf-8")
    if proc.stderr.strip():
        audit_path.with_suffix(".stderr.txt").write_text(proc.stderr, encoding="utf-8")

    try:
        payload = json.loads(proc.stdout)
    except json.JSONDecodeError:
        tail = (proc.stderr or proc.stdout or "").strip()[-400:]
        return {"ok": False, "error": f"อ่านผลลัพธ์ไม่ได้ (exit {proc.returncode}): {tail}",
                "audit": str(audit_path)}

    models = [m for m in (payload.get("modelUsage") or {}) if not m.startswith("claude-haiku")]
    base = {
        "audit": str(audit_path.relative_to(config.ROOT)),
        "model_used": models,
        "model_ok": models == [model],
        "cost_usd": payload.get("total_cost_usd"),
        "duration_s": round(duration, 1),
        "usage": payload.get("usage") or {},
        "denials": payload.get("permission_denials") or [],
    }

    if payload.get("is_error") or payload.get("subtype") != "success":
        return {"ok": False, "error": f"{payload.get('subtype')}: {str(payload.get('result'))[:400]}", **base}

    output = payload.get("structured_output")
    if output is None:
        try:
            output = json.loads(payload.get("result") or "")
        except json.JSONDecodeError:
            return {"ok": False, "error": "ไม่มี structured_output ในผลลัพธ์", **base}
    return {"ok": True, "output": output, **base}
