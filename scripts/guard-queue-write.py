#!/usr/bin/env python3
"""PreToolUse hook — อนุญาตให้เขียนไฟล์ได้เฉพาะใน data/queue/ เท่านั้น

## ทำไมต้องใช้ hook แทนกฎ permission ปกติ

CLI 2.1.179 ไม่ match กฎรูปแบบ `Write(<path>)` เลย — ทดสอบแล้วทั้งสามแบบ
(relative `data/queue/**`, absolute `//home/...`, home `~/...`) ถูกปฏิเสธหมด
มีแต่ `Write` เปล่า ๆ ที่ทำงาน ซึ่งเท่ากับอนุญาตให้เขียนได้ทุกที่ในเครื่อง
hook ทำงานคนละชั้นกับการ match กฎ จึงไม่ติดบั๊กนี้ และบังคับขอบเขตได้จริง

## ทำไมเป็นไฟล์ .py ไม่ใช่ .sh ที่มี heredoc

heredoc กิน stdin ของโปรเซส ทำให้สคริปต์อ่าน JSON ที่ hook ป้อนมาไม่ได้
แล้วจะปฏิเสธทุกอย่างรวมทั้งที่ควรผ่าน (เคยพลาดมาแล้ว จับได้ตอน pipe-test)

รับ JSON ทาง stdin · ตอบ JSON ที่มี permissionDecision กลับไปทาง stdout
"""

import json
import os
import sys

ALLOWED_PREFIX = "/home/you/investment-research/data/queue/"


def decide(payload: dict) -> tuple[str, str]:
    path = (payload.get("tool_input") or {}).get("file_path", "")
    if not path:
        return "deny", "guard: ไม่ระบุ file_path"

    # ผ่าน realpath กัน ../ และ symlink ที่พาออกนอกขอบเขต
    resolved = os.path.realpath(path)
    if resolved.startswith(ALLOWED_PREFIX):
        return "allow", "guard: อยู่ใน data/queue"
    return "deny", f"guard: เขียนได้เฉพาะ {ALLOWED_PREFIX} — ขอเขียน {resolved}"


def main() -> None:
    try:
        payload = json.load(sys.stdin)
    except Exception as exc:
        # อ่าน input ไม่ได้ = ไม่รู้ว่ากำลังจะเขียนอะไร → ปฏิเสธไว้ก่อน
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
