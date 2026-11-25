"""Call Claude headless with "no tools at all" and get a JSON answer that follows a schema.

## How this differs from label-auto.sh

label-auto gives the model tools (Write + `uv run finbg label ...`) fenced by a deny list
and a hook, which that job needs because it loops many times in one session.

Planning does not touch any file: code puts all the data into the prompt, the model answers in JSON,
and code checks the answer, so every tool is disabled (`--tools ""`) and the answer format is enforced with
`--json-schema`. **A headline carrying a prompt injection has nothing to command.**
The worst it can do is make the paper plan worse, inside limits the code already enforces.

## Why the environment is cleared before the call

When called from inside a Claude Code session (for example while testing), a CLAUDECODE variable comes along
and makes the CLI refuse to open a nested session. Passing the same environment as cron
(only HOME/PATH/LANG) gives the same result wherever it is called from.

## Evidence of the model

Uses the `modelUsage` the harness returns (the model cannot fake it), the same lesson as label-auto.
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
    """Run one session; return a dict with `ok`, `output` (the JSON received), evidence of the model, and the cost.

    The raw result is always saved in data/logs/<job>-runs/ (so what the model really answered can be checked later).
    """
    binary = _claude_binary()
    if binary is None:
        return {"ok": False, "error": "ไม่พบคำสั่ง claude (ต้องติดตั้ง Claude Code CLI)"}

    audit_dir = config.DATA / "logs" / f"{job}-runs"
    audit_dir.mkdir(parents=True, exist_ok=True)
    stamp = storage.utc_now().strftime("%Y-%m-%dT%H%M%S")
    audit_path = audit_dir / f"{stamp}.json"

    # A deliberately empty directory: keeps the project's settings and memory out of the PM's context
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
