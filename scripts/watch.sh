#!/usr/bin/env bash
# เฝ้าข่าว/ราคาแรง — cron เรียกทุกชั่วโมง (0 token จนกว่าจะเจอเรื่องใหญ่จริง)
# ถ้างานเช้ากำลังรันอยู่ (ถือล็อก) รอบนี้ข้ามไปเลย ชั่วโมงหน้าค่อยตรวจใหม่
set -uo pipefail
PROJECT_DIR="/home/you/investment-research"
LOG_FILE="${PROJECT_DIR}/data/logs/watch-$(date +%Y-%m).log"
export PATH="${HOME}/.local/bin:/usr/local/bin:/usr/bin:/bin"
mkdir -p "$(dirname "${LOG_FILE}")"
cd "${PROJECT_DIR}" || exit 1
exec 9>"${PROJECT_DIR}/data/.finbg.lock"
flock -n 9 || { echo "$(date '+%F %T') งานอื่นถือล็อกอยู่ — ข้าม" >> "${LOG_FILE}"; exit 0; }
uv run finbg watch >> "${LOG_FILE}" 2>&1
STATUS=$?
find "$(dirname "${LOG_FILE}")" -maxdepth 1 -name 'watch-*.log' -mtime +90 -delete 2>/dev/null
exit ${STATUS}
