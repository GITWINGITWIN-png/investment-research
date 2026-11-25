"""Record every experiment: "how many tries" is an input the Deflated Sharpe needs.

Note 09: "record the number of trials every time ... it cannot be reconstructed later".
Every factor evaluation through the harness is appended to this file automatically (append-only),
with the git commit of the code at the time, so each line can be traced to the code version it came from.
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
    """Number of **distinct** experiments: re-running the same experiment (same name, parameters and period)
    is not a new try, so it should not add to the count used to deflate the Sharpe.
    """
    return len({(r["family"], r["name"], r["params_hash"], tuple(r["period"])) for r in history(family)})
