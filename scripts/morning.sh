#!/usr/bin/env bash
# Daily job: anacron calls it once a day, at the machine's first start-up of the day (see scripts/anacrontab)
#
# Order: collect data -> label news -> investigate violent moves (if any) -> scan the whole market for opportunities
#       -> weekly summary of 8-Ks and strong news + the weekly plan (only on plan days, normally Saturday morning)
#
# Why one script instead of the old 06:00 + 06:30 cron jobs:
# if the machine was off at 06:00, the whole day's work was lost (it happened on 27 of 59 trading days, Aug to Oct)
# anacron remembers whether today's run happened and runs it at start-up, but it ignores ordering;
# keeping everything here guarantees labelling after news collection and plans after fresh data

set -uo pipefail

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"   # the folder this repo was cloned to
LOG_DIR="${PROJECT_DIR}/data/logs"
LOG_FILE="${LOG_DIR}/morning-$(date +%Y-%m).log"
PLAN_LOG="${LOG_DIR}/plan-$(date +%Y-%m).log"

export PATH="${HOME}/.local/bin:/usr/local/bin:/usr/bin:/bin"
mkdir -p "${LOG_DIR}"
cd "${PROJECT_DIR}" || exit 1

# Lock so this job never overlaps itself (for example a manual run while anacron is running it)
exec 9>"${PROJECT_DIR}/data/.finbg.lock"
if ! flock -w 3600 9; then
    echo "$(date '+%F %T') waited for the lock over 1 hour; skipping this round" >> "${LOG_FILE}"
    exit 1
fi

# Right after start-up Wi-Fi may not be connected yet; waiting up to 10 minutes beats a day of broken data
for _ in $(seq 1 20); do
    getent hosts data.sec.gov > /dev/null 2>&1 && break
    sleep 30
done

"${PROJECT_DIR}/scripts/daily.sh"
DAILY=$?

"${PROJECT_DIR}/scripts/label-auto.sh"
LABEL=$?

# Daily: Thai track stage 1, the Thai stock list, prices and news (0 tokens; independent of US data, so it always runs); note 13
uv run finbg th collect >> "${LOG_DIR}/th-$(date +%Y-%m).log" 2>&1
TH=$?
# Daily: Thai scanner; skipped if that trading day was already scanned or the stored news is stale (no decisions on old news)
uv run finbg th scan >> "${LOG_DIR}/th-$(date +%Y-%m).log" 2>&1

# Steps that need fresh data: if collection failed, no decisions are made on old data (try again tomorrow)
VOL="skip"
PLAN="skip"
if [[ ${DAILY} -eq 0 ]]; then
    {
        echo "════════════════════════════════════════════════"
        echo "▶ start $(date '+%Y-%m-%d %H:%M:%S %Z')"
    } >> "${PLAN_LOG}"
    # Daily: any violent moves in watched stocks? If so, Claude investigates the cause (0 tokens otherwise)
    uv run finbg volatility >> "${PLAN_LOG}" 2>&1
    VOL=$?
    # Daily: scan the whole market (about 3,000 stocks + every SEC filing) for overlooked opportunities; see note 12
    # skipped if that trading day was already scanned or the US market is still open
    uv run finbg scan >> "${PLAN_LOG}" 2>&1
    # Weekly: summary of 8-Ks, strong news and VIX for the plan (it used to be hourly; changed at the owner's request, 2026-10-24)
    uv run finbg watch --weekly --no-replan >> "${PLAN_LOG}" 2>&1
    uv run finbg plan run >> "${PLAN_LOG}" 2>&1
    PLAN=$?
echo "◀ done $(date '+%H:%M:%S'), volatility=${VOL}, plan=${PLAN}" >> "${PLAN_LOG}"
fi

echo "$(date '+%F %T') daily=${DAILY} label=${LABEL} th=${TH} volatility=${VOL} plan=${PLAN}" >> "${LOG_FILE}"
find "${LOG_DIR}" -maxdepth 1 -name 'plan-*.log' -mtime +365 -delete 2>/dev/null
[[ ${DAILY} -eq 0 ]] && exit 0 || exit "${DAILY}"
