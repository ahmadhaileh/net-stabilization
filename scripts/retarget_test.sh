#!/usr/bin/env bash
# Retarget test: activate at TARGET_A, wait, retarget to TARGET_B, monitor throughout.
# Collects meter + API data every 5 seconds into a CSV.

set -euo pipefail

API="http://127.0.0.1:8080"
LOGFILE="/tmp/retarget_test_$(date +%Y%m%d_%H%M%S).csv"
TARGET_A="${1:-100}"
TARGET_B="${2:-130}"
STABILIZE_SECONDS="${3:-180}"
MONITOR_AFTER="${4:-180}"

echo "=== Retarget Test ==="
echo "Target A: ${TARGET_A} kW -> Target B: ${TARGET_B} kW"
echo "Stabilize: ${STABILIZE_SECONDS}s, Monitor after: ${MONITOR_AFTER}s"
echo "Log: ${LOGFILE}"
echo ""

# CSV header
echo "timestamp,elapsed_s,phase,target_kw,meter_kw,estimated_kw,mining_miners,sleeping_miners,total_miners,voltage" > "$LOGFILE"

START_EPOCH=$(date +%s)

poll_status() {
    local phase="$1"
    local now=$(date +%s)
    local elapsed=$((now - START_EPOCH))
    local ts=$(date +%H:%M:%S)

    # Get API status (JSON)
    local json
    json=$(curl -s --max-time 3 "${API}/api/status" 2>/dev/null || echo "{}")

    # Parse fields with python (available in container)
    local line
    line=$(python3 -c "
import json, sys
try:
    d = json.loads('''${json}''')
    print(','.join([
        '${ts}',
        str(${elapsed}),
        '${phase}',
        str(d.get('targetPowerInKw', d.get('target_power_kw', ''))),
        str(d.get('measuredPowerInKw', d.get('measured_power_kw', ''))),
        str(d.get('estimatedPowerInKw', d.get('estimated_power_kw', ''))),
        str(d.get('miningMiners', d.get('mining_miners', ''))),
        str(d.get('sleepingMiners', d.get('sleeping_miners', ''))),
        str(d.get('totalMiners', d.get('total_miners', ''))),
        str(d.get('voltage', '')),
    ]))
except:
    print('${ts},${elapsed},${phase},ERR,ERR,ERR,ERR,ERR,ERR,ERR')
" 2>/dev/null)

    echo "$line" >> "$LOGFILE"
    # Print live
    printf "[%s] %3ds %-12s | target=%s meter=%s mining=%s\n" \
        "$ts" "$elapsed" "$phase" \
        "$(echo "$line" | cut -d, -f4)" \
        "$(echo "$line" | cut -d, -f5)" \
        "$(echo "$line" | cut -d, -f7)"
}

# Phase 0: baseline (10s)
echo "--- Phase 0: Baseline (10s) ---"
for i in $(seq 1 2); do
    poll_status "baseline"
    sleep 5
done

# Phase 1: Activate at TARGET_A
echo ""
echo "--- Phase 1: Activating at ${TARGET_A} kW ---"
curl -s -X POST "${API}/api/ems/activate" \
    -H "Content-Type: application/json" \
    -d "{\"activationPowerInKw\": ${TARGET_A}}" | python3 -c "import sys,json; print(json.dumps(json.load(sys.stdin),indent=2))" 2>/dev/null || true
echo ""

PHASE1_END=$(($(date +%s) + STABILIZE_SECONDS))
while [ $(date +%s) -lt $PHASE1_END ]; do
    poll_status "activate_${TARGET_A}"
    sleep 5
done

# Phase 2: Retarget to TARGET_B (WITHOUT deactivating)
echo ""
echo "--- Phase 2: Retargeting to ${TARGET_B} kW ---"
curl -s -X POST "${API}/api/ems/activate" \
    -H "Content-Type: application/json" \
    -d "{\"activationPowerInKw\": ${TARGET_B}}" | python3 -c "import sys,json; print(json.dumps(json.load(sys.stdin),indent=2))" 2>/dev/null || true
echo ""

PHASE2_END=$(($(date +%s) + MONITOR_AFTER))
while [ $(date +%s) -lt $PHASE2_END ]; do
    poll_status "retarget_${TARGET_B}"
    sleep 5
done

# Phase 3: Deactivate
echo ""
echo "--- Phase 3: Deactivating ---"
curl -s -X POST "${API}/api/ems/deactivate" | python3 -c "import sys,json; print(json.dumps(json.load(sys.stdin),indent=2))" 2>/dev/null || true

# Final readings
for i in $(seq 1 3); do
    sleep 5
    poll_status "deactivate"
done

echo ""
echo "=== Test complete. Data saved to ${LOGFILE} ==="
echo "Total samples: $(wc -l < "$LOGFILE")"
