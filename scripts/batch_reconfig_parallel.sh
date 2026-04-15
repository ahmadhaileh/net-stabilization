#!/bin/bash
# Multi-phase batch reconfigure: factory reset all, then config all
# Much faster than sequential per-miner processing
#
# Phase 1: Scan and identify all Antminer IPs
# Phase 2: Factory reset all sleeping miners (parallel, quick HTTP GET)
# Phase 3: Wait for all miners to boot (5 min)
# Phase 4: Config POST all miners with correct pool (parallel batches)
# Phase 5: Wait for reboots, then verify

POOL_IP="192.168.95.6"
POOL_PORT="3333"
SUBNET="192.168.95"
LOG="/tmp/batch_reconfig_log.txt"
MINER_LIST="/tmp/miner_ips.txt"

echo "=== Phase 1: Scanning for Antminers $(date) ===" | tee "$LOG"
> "$MINER_LIST"

for LAST in $(seq 2 254); do
    IP="$SUBNET.$LAST"
    [ "$LAST" -eq 6 ] && continue  # skip server
    
    ping -c 1 -W 1 "$IP" > /dev/null 2>&1 && echo "$IP" >> "$MINER_LIST" &
done
wait

# Filter to only Antminers (parallel HTTP checks)
ANTMINER_LIST="/tmp/antminer_ips.txt"
> "$ANTMINER_LIST"

echo "Checking $(wc -l < "$MINER_LIST") responsive IPs..." | tee -a "$LOG"

while IFS= read -r IP; do
    (
        SYS=$(curl -s --digest -u root:root --max-time 5 "http://$IP/cgi-bin/get_system_info.cgi" 2>/dev/null)
        if echo "$SYS" | grep -qi "antminer"; then
            echo "$IP" >> "$ANTMINER_LIST"
        fi
    ) &
done < "$MINER_LIST"
wait

TOTAL=$(wc -l < "$ANTMINER_LIST")
echo "Found $TOTAL Antminers" | tee -a "$LOG"
cat "$ANTMINER_LIST" | tee -a "$LOG"
echo "" | tee -a "$LOG"

# Phase 2: Check which need factory reset (bmminer not running)
echo "=== Phase 2: Factory reset sleeping miners $(date) ===" | tee -a "$LOG"
RESET_COUNT=0
ALREADY_MINING=0

while IFS= read -r IP; do
    nc -z -w 2 "$IP" 4028 2>/dev/null
    if [ $? -eq 0 ]; then
        ALREADY_MINING=$((ALREADY_MINING+1))
        echo "$IP: already mining, skip reset" >> "$LOG"
    else
        RESET_COUNT=$((RESET_COUNT+1))
        echo "$IP: factory resetting..." >> "$LOG"
        curl -s --digest -u root:root --max-time 15 "http://$IP/cgi-bin/reset_conf.cgi" > /dev/null 2>&1 &
    fi
done < "$ANTMINER_LIST"
wait

echo "Factory reset: $RESET_COUNT miners, Already mining: $ALREADY_MINING" | tee -a "$LOG"

# Phase 3: Wait for all miners to boot
echo "=== Phase 3: Waiting 300s for miners to boot $(date) ===" | tee -a "$LOG"
sleep 300

# Check how many have bmminer running now
MINING_NOW=0
while IFS= read -r IP; do
    nc -z -w 2 "$IP" 4028 2>/dev/null && MINING_NOW=$((MINING_NOW+1))
done < "$ANTMINER_LIST"
echo "Miners with bmminer running: $MINING_NOW / $TOTAL" | tee -a "$LOG"

# Phase 4: Config POST all miners with correct pool (batches of 20)
echo "=== Phase 4: Configuring pool on all miners $(date) ===" | tee -a "$LOG"
BATCH_SIZE=20
BATCH_NUM=0
CONFIG_OK=0
CONFIG_FAIL=0

while IFS= read -r IP; do
    LAST=$(echo "$IP" | awk -F. '{print $4}')
    WORKER="miner.95x${LAST}"
    
    (
        RESULT=$(curl -s --digest -u root:root --max-time 120 \
            --data "_ant_pool1url=${POOL_IP}%3A${POOL_PORT}&_ant_pool1user=${WORKER}&_ant_pool1pw=x&_ant_pool2url=${POOL_IP}%3A${POOL_PORT}&_ant_pool2user=${WORKER}&_ant_pool2pw=x&_ant_pool3url=${POOL_IP}%3A${POOL_PORT}&_ant_pool3user=${WORKER}&_ant_pool3pw=x&_ant_freq=650&_ant_multi_level=1" \
            "http://$IP/cgi-bin/set_miner_conf.cgi" 2>/dev/null)
        
        if echo "$RESULT" | grep -qi "ok"; then
            echo "$IP: config OK" >> "$LOG"
        else
            echo "$IP: config FAIL ($RESULT)" >> "$LOG"
        fi
    ) &
    
    BATCH_NUM=$((BATCH_NUM+1))
    if [ $((BATCH_NUM % BATCH_SIZE)) -eq 0 ]; then
        echo "  Batch $BATCH_NUM / $TOTAL sent, waiting for completion..." | tee -a "$LOG"
        wait
    fi
done < "$ANTMINER_LIST"
wait
echo "All config POSTs sent" | tee -a "$LOG"

# Phase 5: Wait for reboots and verify
echo "=== Phase 5: Waiting 300s for reboots $(date) ===" | tee -a "$LOG"
sleep 300

echo "=== Phase 6: Verifying $(date) ===" | tee -a "$LOG"
VERIFIED=0
MINING=0
CONNECTED=0
NOT_MINING=0

while IFS= read -r IP; do
    # Check bmminer
    nc -z -w 2 "$IP" 4028 2>/dev/null
    if [ $? -eq 0 ]; then
        MINING=$((MINING+1))
    else
        NOT_MINING=$((NOT_MINING+1))
        echo "$IP: NOT MINING" >> "$LOG"
        continue
    fi
    
    # Check config
    CONF=$(curl -s --digest -u root:root --max-time 5 "http://$IP/cgi-bin/get_miner_conf.cgi" 2>/dev/null)
    if echo "$CONF" | grep -q "$POOL_IP:$POOL_PORT"; then
        VERIFIED=$((VERIFIED+1))
    else
        echo "$IP: WRONG CONFIG" >> "$LOG"
    fi
done < "$ANTMINER_LIST"

# Count stratum connections
STRATUM_CONNS=$(ss -tnp | grep ":3333" | grep -v "LISTEN" | wc -l)

echo "" | tee -a "$LOG"
echo "=== FINAL SUMMARY ===" | tee -a "$LOG"
echo "Total Antminers: $TOTAL" | tee -a "$LOG"
echo "Mining (4028 open): $MINING" | tee -a "$LOG"
echo "Correct config: $VERIFIED" | tee -a "$LOG"
echo "Not mining: $NOT_MINING" | tee -a "$LOG"
echo "Stratum connections: $STRATUM_CONNS" | tee -a "$LOG"
echo "=== Completed $(date) ===" | tee -a "$LOG"
