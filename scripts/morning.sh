#!/usr/bin/env bash
# งานประจำวัน — anacron เรียกวันละครั้ง ตอนเครื่องเปิดครั้งแรกของวัน (ดู scripts/anacrontab)
#
# ลำดับ: เก็บข้อมูล → label ข่าว → สืบหุ้นที่ผันผวนรุนแรง (ถ้ามี) → สแกนทั้งตลาดหาโอกาส
#       → สรุป 8-K/ข่าวแรงประจำสัปดาห์ + แผนรายสัปดาห์ (เฉพาะวันออกแผน — ปกติคือเช้าวันเสาร์)
#
# ทำไมรวมเป็นสคริปต์เดียวแทน cron 06:00 + 06:30 แบบเดิม:
# เดิมถ้าเครื่องปิดตอน 06:00 งานวันนั้นหายทั้งวัน (เกิดจริง 27 จาก 59 วันทำการ ส.ค.–ต.ค.)
# anacron จำว่าวันนี้รันแล้วหรือยัง แล้วรันให้ตอนเปิดเครื่อง — แต่มันรันงานแบบไม่สนลำดับ
# การรวมไว้ที่นี่ทำให้ label เกิดหลังเก็บข่าวเสมอ และแผนเกิดหลังข้อมูลสดเสมอ

set -uo pipefail

PROJECT_DIR="/home/you/investment-research"
LOG_DIR="${PROJECT_DIR}/data/logs"
LOG_FILE="${LOG_DIR}/morning-$(date +%Y-%m).log"
PLAN_LOG="${LOG_DIR}/plan-$(date +%Y-%m).log"

export PATH="${HOME}/.local/bin:/usr/local/bin:/usr/bin:/bin"
mkdir -p "${LOG_DIR}"
cd "${PROJECT_DIR}" || exit 1

# ล็อกกันงานนี้ซ้อนกันเอง (เช่น เรียกมือระหว่างที่ anacron กำลังรัน)
exec 9>"${PROJECT_DIR}/data/.finbg.lock"
if ! flock -w 3600 9; then
    echo "$(date '+%F %T') รอล็อกเกิน 1 ชม. — ข้ามรอบนี้" >> "${LOG_FILE}"
    exit 1
fi

# เปิดเครื่องใหม่ ๆ Wi-Fi อาจยังไม่ต่อ — รอได้สูงสุด 10 นาที ดีกว่าเก็บข้อมูลพังทั้งวัน
for _ in $(seq 1 20); do
    getent hosts data.sec.gov > /dev/null 2>&1 && break
    sleep 30
done

"${PROJECT_DIR}/scripts/daily.sh"
DAILY=$?

"${PROJECT_DIR}/scripts/label-auto.sh"
LABEL=$?

# ทุกวัน: แทร็กไทย ขั้น 1 — รายชื่อ ราคา ข่าวหุ้นไทย (0 token · ไม่ขึ้นกับข้อมูลฝั่ง US จึงรันเสมอ) — โน้ต 13
uv run finbg th collect >> "${LOG_DIR}/th-$(date +%Y-%m).log" 2>&1
TH=$?

# ขั้นที่ต้องใช้ข้อมูลสด — ถ้าเก็บข้อมูลไม่สำเร็จ ไม่ตัดสินใจจากข้อมูลเก่า (พรุ่งนี้ลองใหม่)
VOL="skip"
PLAN="skip"
if [[ ${DAILY} -eq 0 ]]; then
    {
        echo "════════════════════════════════════════════════"
        echo "▶ เริ่ม $(date '+%Y-%m-%d %H:%M:%S %Z')"
    } >> "${PLAN_LOG}"
    # ทุกวัน: หุ้นที่จับตาผันผวนรุนแรงไหม → ถ้าใช่ เรียก Claude สืบสาเหตุ (0 token ถ้าไม่มี)
    uv run finbg volatility >> "${PLAN_LOG}" 2>&1
    VOL=$?
    # ทุกวัน: สแกนทั้งตลาด (~3,000 ตัว + filing ทั้งหมดของ SEC) หาโอกาสที่คนมองข้าม — ดูโน้ต 12
    # ข้ามเองถ้าสแกนวันทำการนั้นไปแล้ว หรือตลาดสหรัฐยังเปิดอยู่
    uv run finbg scan >> "${PLAN_LOG}" 2>&1
    # สัปดาห์ละครั้ง: สรุป 8-K/ข่าวแรง/VIX ให้แผนอ่าน (เดิมทุกชั่วโมง — ปรับตามที่ผู้ใช้ขอ 2026-10-24)
    uv run finbg watch --weekly --no-replan >> "${PLAN_LOG}" 2>&1
    uv run finbg plan run >> "${PLAN_LOG}" 2>&1
    PLAN=$?
    echo "◀ จบ $(date '+%H:%M:%S') · volatility=${VOL} · plan=${PLAN}" >> "${PLAN_LOG}"
fi

echo "$(date '+%F %T') daily=${DAILY} label=${LABEL} th=${TH} volatility=${VOL} plan=${PLAN}" >> "${LOG_FILE}"
find "${LOG_DIR}" -maxdepth 1 -name 'plan-*.log' -mtime +365 -delete 2>/dev/null
[[ ${DAILY} -eq 0 ]] && exit 0 || exit "${DAILY}"
