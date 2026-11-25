#!/usr/bin/env bash
# Back up the stores that cannot be rebuilt later
#
# What is backed up, chosen only because it cannot be recreated once lost:
#   data/raw/     adjusted prices change retroactively; news URLs rot over time
#   data/labels/  labels made later would be contaminated by hindsight
#   data/plans/   weekly plans + the prompts actually used + prediction results: the PM's track record
#   data/alerts/  alert history (the reason for every replan)
#   data/investigations/  investigations of violent moves + the data Claude read
#   data/opportunities/   idea portfolio + control group + real outcomes (whole-market scan)
#   Journal/      predictions, analyses, hypotheses: pure thinking work
#
# data/derived/ is not backed up because it can always be recomputed from raw

set -uo pipefail

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"   # the folder this repo was cloned to

# ⚠️ The default is on the same disk: it protects against accidental deletion, not against a disk failure;
# change it to an external drive or a remote (e.g. user@host:/path or rclone)
BACKUP_DIR="${FINBG_BACKUP_DIR:-${HOME}/backups/finbg}"

LOG_FILE="${PROJECT_DIR}/data/logs/backup-$(date +%Y).log"

export PATH="${HOME}/.local/bin:/usr/local/bin:/usr/bin:/bin"

mkdir -p "${BACKUP_DIR}" "$(dirname "${LOG_FILE}")"
cd "${PROJECT_DIR}" || exit 1

{
    echo "──────────────────────────────"
    echo "▶ backup $(date '+%Y-%m-%d %H:%M:%S')  →  ${BACKUP_DIR}"
} >> "${LOG_FILE}"

STATUS=0
for SOURCE in data/raw data/labels data/plans data/alerts data/investigations data/opportunities Journal; do
    if [[ ! -d "${SOURCE}" ]]; then
        echo "  skipped ${SOURCE} (does not exist yet)" >> "${LOG_FILE}"
        continue
    fi
    # rsync cannot create nested destination folders itself (data/raw needs data/ first),
    # so mkdir -p comes first; otherwise it would fail silently every week
    mkdir -p "${BACKUP_DIR}/${SOURCE}"

    # No --delete on purpose: if a source file disappears by accident,
    # the backup copy must survive instead of being deleted with it
    if rsync -a "${SOURCE}/" "${BACKUP_DIR}/${SOURCE}/" >> "${LOG_FILE}" 2>&1; then
        COUNT=$(find "${SOURCE}" -type f | wc -l)
        echo "  ✅ ${SOURCE} (${COUNT} files)" >> "${LOG_FILE}"
    else
        echo "  ❌ ${SOURCE} failed" >> "${LOG_FILE}"
        STATUS=1
    fi
done

SIZE=$(du -sh "${BACKUP_DIR}" 2>/dev/null | cut -f1)
echo "◀ done, total backup size ${SIZE}, exit=${STATUS}" >> "${LOG_FILE}"

exit ${STATUS}
