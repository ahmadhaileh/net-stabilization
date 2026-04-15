#!/bin/bash
# Batch wake & reconfigure all miners with correct pool IP
# Server pool is at 192.168.95.6:3333 (NOT .1)
#
# Strategy per miner:
# 1. Check if bmminer is running (port 4028)
# 2. If NOT running: factory reset (starts bmminer)
# 3. Wait for bmminer to start
# 4. Send config POST with correct pool URL
# 5. Wait for reboot + bmminer restart
# 6. Verify stratum connection

POOL_IP="192.168.95.6"
POOL_PORT="3333"
SUBNET="192.168.95"
LOG="/tmp/batch_reconfig_log.txt"

# Counters
OK=0
FAIL=0
SKIP=0
UNREACH=0

echo "=== Batch Reconfigure Started $(date) ===" | tee "$LOG"
echo "Pool: $POOL_IP:$POOL_PORT" | tee -a "$LOG"
echo "" | tee -a "$LOG"

for LAST in $(seq 2 254); do
    IP="$SUBNET.$LAST"
    
    # Skip known non-miner IPs
    if [ "$LAST" -eq 6 ]; then
        echo "$IP: SKIP (this server)" | tee -a "$LOG"
        SKIP=$((SKIP+1))
        continue
    fi
    
    # Quick ping check
    if ! ping -c 1 -W 1 "$IP" > /dev/null 2>&1; then
        UNREACH=$((UNREACH+1))
        continue
    fi
    
    # Check if it's an Antminer
    SYS=$(curl -s --digest -u root:root --max-time 5 "http://$IP/cgi-bin/get_system_info.cgi" 2>/dev/null)
    if ! echo "$SYS" | grep -qi "antminer"; then
        echo "$IP: SKIP (not Antminer)" | tee -a "$LOG"
        SKIP=$((SKIP+1))
        continue
    fi
    
    # Check current config - already correct?
    CONF=$(curl -s --digest -u root:root --max-time 5 "http://$IP/cgi-bin/get_miner_conf.cgi" 2>/dev/null)
    if echo "$CONF" | grep -q "$POOL_IP:$POOL_PORT"; then
        # Check if bmminer is running
        nc -z -w 2 "$IP" 4028 2>/dev/null
        if [ $? -eq 0 ]; then
            echo "$IP: OK (already configured + mining)" | tee -a "$LOG"
            OK=$((OK+1))
            continue
        fi
    fi
    
    # Check if bmminer is running
    nc -z -w 2 "$IP" 4028 2>/dev/null
    BMMINER=$?
    
    if [ $BMMINER -ne 0 ]; then
        # bmminer not running - factory reset first
        echo "$IP: Factory resetting..." | tee -a "$LOG"
        curl -s --digest -u root:root --max-time 30 "http://$IP/cgi-bin/reset_conf.cgi" > /dev/null 2>&1
        
        # Wait for reboot + bmminer start (up to 300s)
        echo "$IP: Waiting for bmminer to start..." | tee -a "$LOG"
        STARTED=0
        for WAIT in $(seq 1 60); do
            sleep 5
            nc -z -w 2 "$IP" 4028 2>/dev/null
            if [ $? -eq 0 ]; then
                STARTED=1
                echo "$IP: bmminer started after ${WAIT}x5s" | tee -a "$LOG"
                break
            fi
        done
        
        if [ $STARTED -eq 0 ]; then
            echo "$IP: FAIL (bmminer didn't start after factory reset)" | tee -a "$LOG"
            FAIL=$((FAIL+1))
            continue
        fi
        
        # Wait a bit more for bmminer to fully initialize
        sleep 10
    fi
    
    # Now send config with correct pool
    WORKER="miner.95x${LAST}"
    echo "$IP: Configuring pool $POOL_IP:$POOL_PORT worker=$WORKER" | tee -a "$LOG"
    
    RESULT=$(curl -s --digest -u root:root --max-time 120 \
        --data "_ant_pool1url=${POOL_IP}%3A${POOL_PORT}&_ant_pool1user=${WORKER}&_ant_pool1pw=x&_ant_pool2url=${POOL_IP}%3A${POOL_PORT}&_ant_pool2user=${WORKER}&_ant_pool2pw=x&_ant_pool3url=${POOL_IP}%3A${POOL_PORT}&_ant_pool3user=${WORKER}&_ant_pool3pw=x&_ant_freq=650&_ant_multi_level=1" \
        "http://$IP/cgi-bin/set_miner_conf.cgi" 2>/dev/null)
    
    if echo "$RESULT" | grep -qi "ok"; then
        echo "$IP: Config applied (ok), waiting for reboot..." | tee -a "$LOG"
        
        # Wait for reboot + bmminer restart
        sleep 5  # let it start rebooting
        STARTED=0
        for WAIT in $(seq 1 60); do
            sleep 5
            nc -z -w 2 "$IP" 4028 2>/dev/null
            if [ $? -eq 0 ]; then
                STARTED=1
                break
            fi
        done
        
        if [ $STARTED -eq 1 ]; then
            echo "$IP: OK (bmminer running after config)" | tee -a "$LOG"
            OK=$((OK+1))
        else
            echo "$IP: WARN (config applied but bmminer not running)" | tee -a "$LOG"
            FAIL=$((FAIL+1))
        fi
    else
        echo "$IP: FAIL (config POST failed: $RESULT)" | tee -a "$LOG"
        FAIL=$((FAIL+1))
    fi
done

echo "" | tee -a "$LOG"
echo "=== SUMMARY ===" | tee -a "$LOG"
echo "OK: $OK" | tee -a "$LOG"
echo "FAIL: $FAIL" | tee -a "$LOG"
echo "SKIP: $SKIP" | tee -a "$LOG"
echo "UNREACHABLE: $UNREACH" | tee -a "$LOG"
echo "=== Completed $(date) ===" | tee -a "$LOG"
