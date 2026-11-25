#!/usr/bin/env python3
"""PreToolUse hook: allows file writes only inside data/queue/.

## Why a hook instead of an ordinary permission rule

CLI 2.1.179 does not match `Write(<path>)` rules at all; all three forms were tested
(relative `data/queue/**`, absolute `//home/...`, home `~/...`) and all were refused.
Only a bare `Write` works, which would allow writing anywhere on the machine.
A hook runs in a different layer from rule matching, so it avoids this bug and really enforces the boundary.

## Why a .py file and not a .sh with a heredoc

A heredoc consumes the process's stdin, so the script could not read the JSON the hook passes in,
and it would refuse everything, including what should pass (this happened once and was caught in a pipe test).

Reads JSON on stdin; answers with JSON containing permissionDecision on stdout.
"""

import json
import os
import sys

# data/queue/ inside the folder this repo was cloned to
ALLOWED_PREFIX = os.path.join(os.path.dirname(os.path.dirname(os.path.realpath(__file__))), "data", "queue") + os.sep


def decide(payload: dict) -> tuple[str, str]:
    path = (payload.get("tool_input") or {}).get("file_path", "")
    if not path:
        return "deny", "guard: ไม่ระบุ file_path"

    # Through realpath, to block ../ and symlinks that lead outside the allowed area
    resolved = os.path.realpath(path)
    if resolved.startswith(ALLOWED_PREFIX):
        return "allow", "guard: อยู่ใน data/queue"
    return "deny", f"guard: เขียนได้เฉพาะ {ALLOWED_PREFIX} — ขอเขียน {resolved}"


def main() -> None:
    try:
        payload = json.load(sys.stdin)
    except Exception as exc:
        # Input cannot be read = we do not know what is about to be written, so refuse by default
        decision, reason = "deny", f"guard: อ่าน hook input ไม่ได้ ({exc})"
    else:
        decision, reason = decide(payload)

    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": decision,
            "permissionDecisionReason": reason,
        }
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
