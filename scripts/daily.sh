#!/usr/bin/env bash
# Wrapper for cron: never call `uv run` from crontab directly
#
# Reason: cron runs with an almost empty environment (a very short PATH, sometimes no HOME),
# so this script sets PATH itself and always cd's to the right place first;
# otherwise you get the classic "works by hand, but not from cron" with no visible cause

set -uo pipefail

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"   # the folder this repo was cloned to
LOG_DIR="${PROJECT_DIR}/data/logs"
LOG_FILE="${LOG_DIR}/daily-$(date +%Y-%m).log"

# uv is installed in ~/.local/bin, which cron's PATH usually lacks
export PATH="${HOME}/.local/bin:/usr/local/bin:/usr/bin:/bin"

mkdir -p "${LOG_DIR}"
cd "${PROJECT_DIR}" || exit 1

{
    echo "════════════════════════════════════════════════"
    echo "▶ start $(date '+%Y-%m-%d %H:%M:%S %Z')"
} >> "${LOG_FILE}"

uv run finbg daily >> "${LOG_FILE}" 2>&1
STATUS=$?

echo "◀ done $(date '+%H:%M:%S'), exit=${STATUS}" >> "${LOG_FILE}"

# Keeping 6 months of logs is enough; otherwise they grow without end
find "${LOG_DIR}" -name 'daily-*.log' -mtime +180 -delete 2>/dev/null

exit ${STATUS}
