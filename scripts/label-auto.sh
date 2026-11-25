#!/usr/bin/env bash
# Label news automatically without keeping a session open (cron 06:30, 30 minutes after daily.sh)
#
# Unlike Claude Code's built-in CronCreate, which keeps jobs in the session's memory,
# so they vanish when the session closes and expire after 7 days, unusable for a daily job,
# this script calls `claude -p` (headless) from the system's cron, so it is tied to no session
#
# Safety with nobody watching: **never pass --dangerously-skip-permissions**.
# This job feeds headlines from the internet (unvetted third-party text)
# to a model allowed to run commands; the allow list in .claude/settings.local.json is the cage around it.
# Tested: normal mode "refuses and carries on" instead of "waits for someone to click", so cron never hangs

set -uo pipefail

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"   # the folder this repo was cloned to
LOG_DIR="${PROJECT_DIR}/data/logs"
LOG_FILE="${LOG_DIR}/label-$(date +%Y-%m).log"
AUDIT_DIR="${LOG_DIR}/label-runs"

# Pin the model; never let the CLI default decide
# If the default changed one day, the corpus would mix models without anyone noticing.
# The model field in the schema exists to catch this, but it catches it late, which means discarding labels
MODEL="claude-opus-5"

# Time limit: this CLI version has no --max-turns, so a timeout is used instead
# Normally 2 to 3 rounds finish within 15 minutes; hitting 30 minutes means something is wrong
TIMEOUT_SEC=1800

# cron runs with an almost empty environment, so PATH must be set here (uv and claude live in ~/.local/bin)
export PATH="${HOME}/.local/bin:/usr/local/bin:/usr/bin:/bin"

mkdir -p "${LOG_DIR}" "${AUDIT_DIR}"
cd "${PROJECT_DIR}" || exit 1

STAMP=$(date '+%Y-%m-%d %H:%M:%S %Z')
echo "════════════════════════════════════════════════" >> "${LOG_FILE}"
echo "▶ start ${STAMP}" >> "${LOG_FILE}"

# ── Gate before starting ──
# Ask, with a command that uses no tokens, whether there is any work
# Opening a claude session has a fixed cost (about $0.08 to build the cache)
# so on a night with no new news there is no reason to pay it
PENDING=$(uv run finbg label stats 2>/dev/null | grep 'ยังไม่ label' | awk '{print $NF}')

if [[ ! "${PENDING}" =~ ^[0-9]+$ ]]; then
    echo "❌ could not read the number of news items waiting for labels (got '${PENDING}'); skipping this round" >> "${LOG_FILE}"
    echo "◀ done $(date '+%H:%M:%S'), exit=1" >> "${LOG_FILE}"
    exit 1
fi

if [[ "${PENDING}" -eq 0 ]]; then
    echo "⏭️  no news waiting for labels; no session opened (saves the boot cost)" >> "${LOG_FILE}"
    echo "◀ done $(date '+%H:%M:%S'), exit=0" >> "${LOG_FILE}"
    exit 0
fi

echo "   ${PENDING} news items waiting for labels -> opening a headless session" >> "${LOG_FILE}"

# ── The real run ──
AUDIT_FILE="${AUDIT_DIR}/$(date +%Y-%m-%dT%H%M).json"

# --settings points to a file the system never touches (unlike .claude/settings.local.json, which is rewritten
# every time the user approves something, and once lost its whole deny block)
# It holds: a bare allow for Write + a hook that allows writes only in data/queue + denies for dangerous commands
timeout "${TIMEOUT_SEC}" claude -p "$(cat "${PROJECT_DIR}/scripts/loop-prompt-headless.md")" \
    --model "${MODEL}" \
    --settings "${PROJECT_DIR}/scripts/label-loop-settings.json" \
    --output-format json \
    < /dev/null > "${AUDIT_FILE}" 2>>"${LOG_FILE}"
STATUS=$?

if [[ ${STATUS} -eq 124 ]]; then
    echo "⏱️  hit the time limit of ${TIMEOUT_SEC}s; stopped" >> "${LOG_FILE}"
fi

# ── A summary that is easy to read in the morning ──
# permission_denials matter most: if it is not empty, something tried to get out of the cage
python3 - "${AUDIT_FILE}" "${MODEL}" >> "${LOG_FILE}" 2>&1 <<'PYEOF'
import json, sys

try:
    with open(sys.argv[1], encoding="utf-8") as fh:
        d = json.load(fh)
except Exception as exc:
    print(f"   ⚠️  could not read the result: {exc}")
    raise SystemExit(0)

expected_model = sys.argv[2]

denials = d.get("permission_denials") or []
cost = d.get("total_cost_usd")
usage = d.get("usage") or {}

print(f"   result   {d.get('subtype')}, {d.get('num_turns')} turns, {d.get('duration_ms', 0)/1000:.0f}s")
print(f"   cost     ${cost:.3f}" if isinstance(cost, (int, float)) else "   cost     ?")
print(f"   token   in={usage.get('input_tokens', '?')} out={usage.get('output_tokens', '?')} "
      f"cache_read={usage.get('cache_read_input_tokens', '?')}")

# ── Check the model from the harness's record, not from the model's own claim ──
# modelUsage is what the system recorded as actually called; a model cannot fake it
# Asking the model to confirm itself once made it refuse to work all night, because it cannot really introspect
# (the model list bundled with the CLI is older than the models in service, so it contradicted the Environment block)
# so this check belongs to the script, which sees the real data
models = list((d.get("modelUsage") or {}).keys())
main = [m for m in models if not m.startswith("claude-haiku")]  # haiku = the CLI's internal helper
print(f"   model    {', '.join(main) if main else '?'}")

if main != [expected_model]:
    print(f"   🚨 model mismatch! expected {expected_model} but the run used {main}")
    print(f"      labels from this round have contaminated provenance; quarantine them before use:")
    print(f"      filter data/labels/ by today's labeled_at, then decide whether to keep or discard them")

if denials:
    print(f"   🚨 {len(denials)} permission denials; check now whether something tried to get out of the cage")
    for x in denials[:5]:
        cmd = str((x.get("tool_input") or {}).get("command", ""))[:90]
        print(f"      {x.get('tool_name')}: {cmd}")

result = (d.get("result") or "").strip()
summary = next((l for l in result.splitlines() if l.startswith("SUMMARY")), "")
print(f"   {summary}" if summary else f"   report   {result[:300]}")
PYEOF

echo "   audit   ${AUDIT_FILE#"${PROJECT_DIR}/"}" >> "${LOG_FILE}"
echo "◀ done $(date '+%H:%M:%S'), exit=${STATUS}" >> "${LOG_FILE}"

# Keep logs for 6 months and the raw audit files for 90 days (the JSON files are larger)
find "${LOG_DIR}" -maxdepth 1 -name 'label-*.log' -mtime +180 -delete 2>/dev/null
find "${AUDIT_DIR}" -name '*.json' -mtime +90 -delete 2>/dev/null

exit ${STATUS}
