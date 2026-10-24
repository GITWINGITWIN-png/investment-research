"""บันทึกทุกการทดลอง — "ลองกี่ครั้ง" คือตัวแปรที่ Deflated Sharpe ต้องใช้

โน้ต 09: "บันทึกจำนวนการทดลองทุกครั้ง ... ย้อนกลับไปทำทีหลังไม่ได้"
ทุกครั้งที่ประเมิน factor ผ่าน harness จะต่อท้ายไฟล์นี้อัตโนมัติ (append-only)
พร้อม git commit ของโค้ดตอนนั้น → ย้อนดูได้ว่าผลแต่ละบรรทัดมาจากโค้ดเวอร์ชันไหน
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from typing import Any

from finbg import config as fconfig
from finbg import storage

LOG = fconfig.DATA / "research" / "experiments.jsonl"


def _git_commit() -> str:
    try:
        return subprocess.run(["git", "-C", str(fconfig.ROOT), "rev-parse", "--short", "HEAD"],
                              capture_output=True, text=True, timeout=5).stdout.strip() or "?"
    except (OSError, subprocess.SubprocessError):
        return "?"


def log(family: str, name: str, params: dict[str, Any], metrics: dict[str, Any], universe_size: int,
        period: tuple[str, str]) -> dict[str, Any]:
    record = {
        "at": storage.utc_stamp(),
        "family": family,
        "name": name,
        "params": params,
        "params_hash": hashlib.sha256(json.dumps(params, sort_keys=True).encode()).hexdigest()[:12],
        "universe_size": universe_size,
        "period": list(period),
        "metrics": metrics,
        "commit": _git_commit(),
    }
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False, default=float) + "\n")
    return record


def history(family: str | None = None) -> list[dict[str, Any]]:
    if not LOG.exists():
        return []
    records = [json.loads(line) for line in LOG.read_text(encoding="utf-8").splitlines() if line.strip()]
    return [r for r in records if family is None or r["family"] == family]


def count(family: str | None = None) -> int:
    """จำนวนการทดลองที่ **ไม่ซ้ำกัน** — รันการทดลองเดิมซ้ำ (ชื่อ+พารามิเตอร์+ช่วงเวลาเดิม)
    ไม่ใช่การลองใหม่ จึงไม่ควรเพิ่มจำนวนครั้งที่ใช้หัก Deflated Sharpe
    """
    return len({(r["family"], r["name"], r["params_hash"], tuple(r["period"])) for r in history(family)})
