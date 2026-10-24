"""แจ้งเตือน — เดสก์ท็อปเป็นค่าเริ่มต้น มือถือ (ntfy) เปิดเองโดยตั้งใจเท่านั้น

## ทำไมไม่เปิด ntfy ให้อัตโนมัติ

ntfy.sh เป็นเซิร์ฟเวอร์สาธารณะ: ใครรู้ชื่อ topic ก็อ่านข้อความได้ และข้อความถูกเก็บไว้
ชั่วคราวบนเซิร์ฟเวอร์ — การส่งรายละเอียดพอร์ตออกไปจึงควรเป็นการตัดสินใจของผู้ใช้
`finbg notify setup` สร้าง topic สุ่มที่เดายาก แล้วบอกวิธีกดติดตามในแอป

## ทำไมต้องตั้ง DBUS เอง

cron/anacron รันโดยไม่มีตัวแปรของเซสชันเดสก์ท็อป notify-send จึงหาหน้าจอไม่เจอ
ถ้ารู้ที่อยู่ของ session bus (/run/user/<uid>/bus) ก็ส่งถึงหน้าจอได้ตามปกติ
"""

from __future__ import annotations

import json
import os
import secrets
import shutil
import subprocess
from pathlib import Path
from typing import Any

import requests

from . import config, storage

PRIORITY = {"info": "default", "medium": "default", "high": "high", "critical": "urgent"}


def _settings() -> dict[str, Any]:
    try:
        return json.loads(config.NOTIFY_CONFIG.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _desktop(title: str, body: str, severity: str) -> bool:
    binary = shutil.which("notify-send")
    if not binary:
        return False
    env = dict(os.environ)
    bus = Path(f"/run/user/{os.getuid()}/bus")
    if "DBUS_SESSION_BUS_ADDRESS" not in env and bus.exists():
        env["DBUS_SESSION_BUS_ADDRESS"] = f"unix:path={bus}"
    urgency = "critical" if severity == "critical" else "normal"
    try:
        subprocess.run([binary, "-u", urgency, "-a", "finbg", title, body[:500]],
                       env=env, timeout=10, capture_output=True)
        return True
    except (OSError, subprocess.SubprocessError):
        return False


def _ntfy(topic: str, title: str, body: str, severity: str, server: str) -> bool:
    try:
        response = requests.post(
            f"{server.rstrip('/')}/{topic}",
            data=body.encode("utf-8"),
            headers={
                # header ต้องเป็น latin-1 — ntfy รองรับ RFC 2047 สำหรับหัวเรื่องภาษาอื่น
                "Title": f"=?UTF-8?B?{__import__('base64').b64encode(title.encode()).decode()}?=",
                "Priority": PRIORITY.get(severity, "default"),
                "Tags": "chart_with_upwards_trend" if severity in ("info", "medium") else "warning",
            },
            timeout=15,
        )
        return response.ok
    except requests.RequestException:
        return False


def send(title: str, body: str, severity: str = "info") -> dict[str, Any]:
    """ส่งทุกช่องทางที่เปิดไว้ และบันทึกลง log เสมอ (ไม่มีช่องทางไหนทำงานก็ยังย้อนดูได้)"""
    settings = _settings()
    delivered: dict[str, bool] = {"desktop": _desktop(title, body, severity)}
    topic = settings.get("ntfy_topic")
    if topic:
        delivered["ntfy"] = _ntfy(topic, title, body, severity, settings.get("ntfy_server", config.NTFY_SERVER))

    config.ALERTS.mkdir(parents=True, exist_ok=True)
    with (config.ALERTS / "notifications.log").open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({"at": storage.utc_stamp(), "severity": severity, "title": title,
                             "body": body, "delivered": delivered}, ensure_ascii=False) + "\n")
    return delivered


def setup() -> dict[str, Any]:
    """เปิดการแจ้งเตือนผ่านมือถือด้วย ntfy — สร้าง topic สุ่มที่เดายาก"""
    current = _settings()
    if current.get("ntfy_topic"):
        return {"ok": True, "created": False, "topic": current["ntfy_topic"], "server": current.get("ntfy_server", config.NTFY_SERVER)}
    topic = f"finbg-{secrets.token_urlsafe(18)}"
    config.NOTIFY_CONFIG.parent.mkdir(parents=True, exist_ok=True)
    config.NOTIFY_CONFIG.write_text(json.dumps({"ntfy_topic": topic, "ntfy_server": config.NTFY_SERVER}, indent=2), encoding="utf-8")
    os.chmod(config.NOTIFY_CONFIG, 0o600)
    return {"ok": True, "created": True, "topic": topic, "server": config.NTFY_SERVER}
