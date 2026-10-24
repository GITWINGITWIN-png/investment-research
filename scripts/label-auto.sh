#!/usr/bin/env bash
# Label ข่าวอัตโนมัติแบบไม่ต้องเปิด session ทิ้งไว้ (cron 06:30 — หลัง daily.sh 30 นาที)
#
# ต่างจาก CronCreate ในตัว Claude Code ตรงที่ตัวนั้นเก็บงานไว้ในหน่วยความจำของ session
# ปิด session แล้วงานหาย และหมดอายุเองใน 7 วัน — ใช้กับงานประจำทุกวันไม่ได้
# สคริปต์นี้เรียก `claude -p` (headless) จาก cron ของระบบ จึงไม่ผูกกับ session ใด ๆ
#
# ความปลอดภัยตอนไม่มีคนดู: **ไม่ใส่ --dangerously-skip-permissions โดยเด็ดขาด**
# งานนี้ป้อนพาดหัวข่าวจากอินเทอร์เน็ต (ข้อความจากบุคคลที่สามที่ไม่ผ่านการตรวจ)
# ให้โมเดลที่มีสิทธิ์รันคำสั่ง — allow list ใน .claude/settings.local.json คือกรงที่กันไว้
# ทดสอบแล้วว่าโหมดปกติ "ปฏิเสธแล้วไปต่อ" ไม่ใช่ "ค้างรอคนกด" จึงไม่แขวน cron

set -uo pipefail

PROJECT_DIR="/home/you/investment-research"
LOG_DIR="${PROJECT_DIR}/data/logs"
LOG_FILE="${LOG_DIR}/label-$(date +%Y-%m).log"
AUDIT_DIR="${LOG_DIR}/label-runs"

# ล็อกโมเดลไว้ตายตัว ห้ามปล่อยให้ใช้ค่า default ของ CLI
# ถ้าวันหนึ่ง default เปลี่ยน คลังจะปนโมเดลโดยไม่มีใครรู้ตัว
# ฟิลด์ model ใน schema มีไว้จับเรื่องนี้ แต่จับได้ตอนสายแล้ว = ต้องทิ้ง label
MODEL="claude-opus-5"

# เพดานเวลา — CLI เวอร์ชันนี้ไม่มี --max-turns จึงคุมด้วย timeout แทน
# ปกติ 2-3 รอบจบใน 15 นาที ถ้าชน 30 นาทีแปลว่ามีอะไรผิด
TIMEOUT_SEC=1800

# cron รันด้วย environment ที่แทบว่างเปล่า — ต้องตั้ง PATH เอง (uv/claude อยู่ใน ~/.local/bin)
export PATH="${HOME}/.local/bin:/usr/local/bin:/usr/bin:/bin"

mkdir -p "${LOG_DIR}" "${AUDIT_DIR}"
cd "${PROJECT_DIR}" || exit 1

STAMP=$(date '+%Y-%m-%d %H:%M:%S %Z')
echo "════════════════════════════════════════════════" >> "${LOG_FILE}"
echo "▶ เริ่ม ${STAMP}" >> "${LOG_FILE}"

# ── ด่านก่อนจุดไฟ ──
# ถามด้วยคำสั่งที่ไม่ใช้ token เลยว่ามีงานไหม
# การเปิด session claude หนึ่งครั้งมีค่าใช้จ่ายคงที่ (~$0.08 ค่าสร้าง cache)
# ถ้าคืนไหนไม่มีข่าวใหม่ ก็ไม่มีเหตุให้จ่าย
PENDING=$(uv run finbg label stats 2>/dev/null | grep 'ยังไม่ label' | awk '{print $NF}')

if [[ ! "${PENDING}" =~ ^[0-9]+$ ]]; then
    echo "❌ อ่านจำนวนข่าวที่รอ label ไม่ได้ (ได้ค่า '${PENDING}') — ข้ามรอบนี้" >> "${LOG_FILE}"
    echo "◀ จบ $(date '+%H:%M:%S') · exit=1" >> "${LOG_FILE}"
    exit 1
fi

if [[ "${PENDING}" -eq 0 ]]; then
    echo "⏭️  ไม่มีข่าวรอ label — ไม่เปิด session (ประหยัดค่า boot)" >> "${LOG_FILE}"
    echo "◀ จบ $(date '+%H:%M:%S') · exit=0" >> "${LOG_FILE}"
    exit 0
fi

echo "   มีข่าวรอ label ${PENDING} ชิ้น → เปิด headless session" >> "${LOG_FILE}"

# ── รันจริง ──
AUDIT_FILE="${AUDIT_DIR}/$(date +%Y-%m-%dT%H%M).json"

# --settings ชี้ไฟล์ที่ระบบไม่แตะ (ต่างจาก .claude/settings.local.json ที่ถูก rewrite
# ทุกครั้งที่ผู้ใช้กดอนุมัติ จน deny block เคยหายไปทั้งก้อน)
# ในนั้นมี: allow Write เปล่า ๆ + hook บังคับให้เขียนได้เฉพาะ data/queue + deny คำสั่งอันตราย
timeout "${TIMEOUT_SEC}" claude -p "$(cat "${PROJECT_DIR}/scripts/loop-prompt-headless.md")" \
    --model "${MODEL}" \
    --settings "${PROJECT_DIR}/scripts/label-loop-settings.json" \
    --output-format json \
    < /dev/null > "${AUDIT_FILE}" 2>>"${LOG_FILE}"
STATUS=$?

if [[ ${STATUS} -eq 124 ]]; then
    echo "⏱️  ชนเพดานเวลา ${TIMEOUT_SEC}s — ถูกตัดจบ" >> "${LOG_FILE}"
fi

# ── สรุปผลให้อ่านง่ายตอนเช้า ──
# permission_denials คือจุดที่ต้องดูที่สุด: ถ้าไม่ว่าง แปลว่ามีอะไรพยายามออกนอกกรง
python3 - "${AUDIT_FILE}" "${MODEL}" >> "${LOG_FILE}" 2>&1 <<'PYEOF'
import json, sys

try:
    with open(sys.argv[1], encoding="utf-8") as fh:
        d = json.load(fh)
except Exception as exc:
    print(f"   ⚠️  อ่านผลลัพธ์ไม่ได้: {exc}")
    raise SystemExit(0)

expected_model = sys.argv[2]

denials = d.get("permission_denials") or []
cost = d.get("total_cost_usd")
usage = d.get("usage") or {}

print(f"   ผล      {d.get('subtype')} · {d.get('num_turns')} turn · {d.get('duration_ms', 0)/1000:.0f}s")
print(f"   ค่าใช้จ่าย ${cost:.3f}" if isinstance(cost, (int, float)) else "   ค่าใช้จ่าย ?")
print(f"   token   in={usage.get('input_tokens', '?')} out={usage.get('output_tokens', '?')} "
      f"cache_read={usage.get('cache_read_input_tokens', '?')}")

# ── ตรวจโมเดลจากหลักฐานของ harness ไม่ใช่คำบอกเล่าของโมเดล ──
# modelUsage คือสิ่งที่ระบบบันทึกว่าเรียกโมเดลไหนไปจริง โมเดลปลอมค่านี้ไม่ได้
# เคยลองให้โมเดลยืนยันตัวเอง แล้วมันปฏิเสธทำงานทั้งคืน — เพราะมัน introspect ไม่ได้จริง
# (รายชื่อโมเดลที่ติดมากับ CLI เก่ากว่าโมเดลที่ให้บริการ จึงขัดกับบล็อก Environment)
# หน้าที่นี้จึงเป็นของสคริปต์ ซึ่งเห็นข้อมูลจริง
models = list((d.get("modelUsage") or {}).keys())
main = [m for m in models if not m.startswith("claude-haiku")]  # haiku = ตัวช่วยภายในของ CLI
print(f"   โมเดล    {', '.join(main) if main else '?'}")

if main != [expected_model]:
    print(f"   🚨 โมเดลไม่ตรง! ตั้งใจใช้ {expected_model} แต่รันจริง {main}")
    print(f"      label ที่เกิดในรอบนี้ปนเปื้อน provenance — ให้กักกันก่อนใช้:")
    print(f"      กรองด้วย labeled_at ของวันนี้ใน data/labels/ แล้วตัดสินใจว่าจะเก็บหรือทิ้ง")

if denials:
    print(f"   🚨 ถูกปฏิเสธสิทธิ์ {len(denials)} ครั้ง — ตรวจด่วน ว่ามีอะไรพยายามออกนอกกรง")
    for x in denials[:5]:
        cmd = str((x.get("tool_input") or {}).get("command", ""))[:90]
        print(f"      {x.get('tool_name')}: {cmd}")

result = (d.get("result") or "").strip()
summary = next((l for l in result.splitlines() if l.startswith("SUMMARY")), "")
print(f"   {summary}" if summary else f"   รายงาน  {result[:300]}")
PYEOF

echo "   audit   ${AUDIT_FILE#"${PROJECT_DIR}/"}" >> "${LOG_FILE}"
echo "◀ จบ $(date '+%H:%M:%S') · exit=${STATUS}" >> "${LOG_FILE}"

# เก็บ log 6 เดือน / เก็บ audit ดิบ 90 วัน (ไฟล์ JSON ใหญ่กว่า)
find "${LOG_DIR}" -maxdepth 1 -name 'label-*.log' -mtime +180 -delete 2>/dev/null
find "${AUDIT_DIR}" -name '*.json' -mtime +90 -delete 2>/dev/null

exit ${STATUS}
