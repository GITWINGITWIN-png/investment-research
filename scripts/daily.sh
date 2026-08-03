#!/usr/bin/env bash
# Wrapper สำหรับ cron — อย่าเรียก `uv run` จาก crontab ตรง ๆ
#
# เหตุผล: cron รันด้วย environment ที่แทบว่างเปล่า (PATH สั้นมาก, ไม่มี HOME บางที)
# สคริปต์นี้จึงต้องตั้ง PATH เอง และ cd ให้ถูกที่ก่อนเสมอ
# ไม่งั้นจะเจออาการคลาสสิก "รันมือได้ แต่ cron ไม่ทำงาน" แล้วหาสาเหตุไม่เจอ

set -uo pipefail

PROJECT_DIR="/home/you/investment-research"
LOG_DIR="${PROJECT_DIR}/data/logs"
LOG_FILE="${LOG_DIR}/daily-$(date +%Y-%m).log"

# uv ติดตั้งอยู่ใน ~/.local/bin ซึ่ง cron มักไม่มีใน PATH
export PATH="${HOME}/.local/bin:/usr/local/bin:/usr/bin:/bin"

mkdir -p "${LOG_DIR}"
cd "${PROJECT_DIR}" || exit 1

{
    echo "════════════════════════════════════════════════"
    echo "▶ เริ่ม $(date '+%Y-%m-%d %H:%M:%S %Z')"
} >> "${LOG_FILE}"

uv run finbg daily >> "${LOG_FILE}" 2>&1
STATUS=$?

echo "◀ จบ $(date '+%H:%M:%S') · exit=${STATUS}" >> "${LOG_FILE}"

# เก็บ log ไว้ 6 เดือนพอ — ไม่งั้นโตไม่มีที่สิ้นสุด
find "${LOG_DIR}" -name 'daily-*.log' -mtime +180 -delete 2>/dev/null

exit ${STATUS}
