#!/usr/bin/env bash
# สำรองคลังที่สร้างย้อนหลังไม่ได้
#
# สิ่งที่สำรอง — เลือกมาเพราะ "หายแล้วสร้างใหม่ไม่ได้" เท่านั้น:
#   data/raw/     ราคาที่ปรับแล้วเปลี่ยนย้อนหลัง · URL ข่าวเน่าตามเวลา
#   data/labels/  label ที่ทำย้อนหลัง = ปนเปื้อน hindsight
#   data/plans/   แผนรายสัปดาห์ + พรอมต์ที่ใช้จริง + ผลคำทำนาย — track record ของ PM
#   data/alerts/  ประวัติการแจ้งเตือน (เหตุผลของทุก replan)
#   Journal/      คำทำนาย บทวิเคราะห์ สมมติฐาน — งานสมองล้วน
#
# ไม่สำรอง data/derived/ เพราะคำนวณใหม่ได้จาก raw เสมอ

set -uo pipefail

PROJECT_DIR="/home/you/investment-research"

# ⚠️ ค่าเริ่มต้นอยู่บนดิสก์เดียวกัน = กันลบพลาดได้ แต่กันดิสก์พังไม่ได้
# ควรเปลี่ยนเป็น external drive หรือ remote (เช่น user@host:/path หรือ rclone)
BACKUP_DIR="${FINBG_BACKUP_DIR:-${HOME}/backups/finbg}"

LOG_FILE="${PROJECT_DIR}/data/logs/backup-$(date +%Y).log"

export PATH="${HOME}/.local/bin:/usr/local/bin:/usr/bin:/bin"

mkdir -p "${BACKUP_DIR}" "$(dirname "${LOG_FILE}")"
cd "${PROJECT_DIR}" || exit 1

{
    echo "──────────────────────────────"
    echo "▶ สำรอง $(date '+%Y-%m-%d %H:%M:%S')  →  ${BACKUP_DIR}"
} >> "${LOG_FILE}"

STATUS=0
for SOURCE in data/raw data/labels data/plans data/alerts Journal; do
    if [[ ! -d "${SOURCE}" ]]; then
        echo "  ข้าม ${SOURCE} (ยังไม่มี)" >> "${LOG_FILE}"
        continue
    fi
    # rsync สร้างโฟลเดอร์ปลายทางที่ซ้อนหลายชั้นเองไม่ได้ (เช่น data/raw ต้องมี data/ ก่อน)
    # จึงต้อง mkdir -p เองก่อน — ไม่งั้นจะ fail เงียบ ๆ ทุกสัปดาห์
    mkdir -p "${BACKUP_DIR}/${SOURCE}"

    # ไม่ใช้ --delete โดยตั้งใจ: ถ้าไฟล์ต้นทางหายเพราะอุบัติเหตุ
    # สำเนาสำรองต้องยังอยู่ ไม่ถูกลบตามไปด้วย
    if rsync -a "${SOURCE}/" "${BACKUP_DIR}/${SOURCE}/" >> "${LOG_FILE}" 2>&1; then
        COUNT=$(find "${SOURCE}" -type f | wc -l)
        echo "  ✅ ${SOURCE} (${COUNT} ไฟล์)" >> "${LOG_FILE}"
    else
        echo "  ❌ ${SOURCE} ล้มเหลว" >> "${LOG_FILE}"
        STATUS=1
    fi
done

SIZE=$(du -sh "${BACKUP_DIR}" 2>/dev/null | cut -f1)
echo "◀ จบ · ขนาดสำรองรวม ${SIZE} · exit=${STATUS}" >> "${LOG_FILE}"

exit ${STATUS}
