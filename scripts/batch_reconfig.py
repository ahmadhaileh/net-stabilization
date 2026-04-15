#!/usr/bin/env python3
"""
Batch reconfigure miners: factory reset sleeping miners, then set correct pool.
Run on the mining server (192.168.95.6).
"""
import subprocess
import time
import sys
import concurrent.futures
import os

POOL_IP = "192.168.95.6"
POOL_PORT = "3333"
MINER_LIST = "/tmp/known_miners.txt"
LOG = "/tmp/br2_log.txt"
BATCH_SIZE = 10

def run(cmd, timeout=15):
    try:
        r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout)
        return r.stdout.strip()
    except:
        return ""

def is_mining(ip):
    return run(f"nc -z -w 2 {ip} 4028 && echo Y", timeout=5) == "Y"

def factory_reset(ip):
    run(f'curl -s --digest -u root:root --max-time 15 "http://{ip}/cgi-bin/reset_conf.cgi"')

def config_pool(ip, worker):
    url = f"http://{ip}/cgi-bin/set_miner_conf.cgi"
    data = (
        f"_ant_pool1url={POOL_IP}%3A{POOL_PORT}&_ant_pool1user={worker}&_ant_pool1pw=x"
        f"&_ant_pool2url={POOL_IP}%3A{POOL_PORT}&_ant_pool2user={worker}&_ant_pool2pw=x"
        f"&_ant_pool3url={POOL_IP}%3A{POOL_PORT}&_ant_pool3user={worker}&_ant_pool3pw=x"
        f"&_ant_freq=650&_ant_multi_level=1"
    )
    result = run(f'curl -s --digest -u root:root --max-time 120 --data "{data}" "{url}"', timeout=130)
    return "ok" in result.lower()

def check_config(ip):
    conf = run(f'curl -s --digest -u root:root --max-time 5 "http://{ip}/cgi-bin/get_miner_conf.cgi"')
    return f"{POOL_IP}:{POOL_PORT}" in conf

def log(msg):
    print(msg, flush=True)
    with open(LOG, "a") as f:
        f.write(msg + "\n")

def main():
    with open(LOG, "w") as f:
        f.write("")
    
    # Load miner list
    with open(MINER_LIST) as f:
        miners = [line.strip() for line in f if line.strip()]
    total = len(miners)
    log(f"=== Batch Reconfigure: {total} miners, pool {POOL_IP}:{POOL_PORT} ===")
    log(f"=== Phase 1: Factory reset sleeping miners ===")
    
    sleeping = []
    mining = []
    for ip in miners:
        if is_mining(ip):
            mining.append(ip)
        else:
            sleeping.append(ip)
    
    log(f"Already mining: {len(mining)}, Sleeping: {len(sleeping)}")
    
    # Factory reset sleeping miners in batches
    for i in range(0, len(sleeping), BATCH_SIZE):
        batch = sleeping[i:i+BATCH_SIZE]
        with concurrent.futures.ThreadPoolExecutor(max_workers=BATCH_SIZE) as ex:
            list(ex.map(factory_reset, batch))
        log(f"  Reset batch {i+len(batch)}/{len(sleeping)}")
    
    log(f"=== Phase 2: Waiting 300s for boot ===")
    time.sleep(300)
    
    # Check how many are now mining
    now_mining = sum(1 for ip in miners if is_mining(ip))
    log(f"Mining after reset: {now_mining}/{total}")
    
    log(f"=== Phase 3: Configure pool on all miners ===")
    ok = 0
    fail = 0
    for i in range(0, total, BATCH_SIZE):
        batch = miners[i:i+BATCH_SIZE]
        def do_config(ip):
            last = ip.split(".")[-1]
            worker = f"miner.95x{last}"
            return (ip, config_pool(ip, worker))
        
        with concurrent.futures.ThreadPoolExecutor(max_workers=BATCH_SIZE) as ex:
            results = list(ex.map(do_config, batch))
        for ip, success in results:
            if success:
                ok += 1
            else:
                fail += 1
                log(f"  {ip}: config FAIL")
        log(f"  Config batch {min(i+BATCH_SIZE, total)}/{total} (ok={ok} fail={fail})")
    
    log(f"Config complete: OK={ok} FAIL={fail}")
    
    log(f"=== Phase 4: Waiting 300s for reboots ===")
    time.sleep(300)
    
    log(f"=== Phase 5: Verify ===")
    verified = 0
    mining_final = 0
    not_mining = 0
    wrong_config = 0
    
    for ip in miners:
        if is_mining(ip):
            mining_final += 1
            if check_config(ip):
                verified += 1
            else:
                wrong_config += 1
                log(f"  {ip}: mining but WRONG config")
        else:
            not_mining += 1
    
    # Count stratum connections
    stratum = run("ss -tnp | grep :3333 | grep -v LISTEN | wc -l")
    
    log(f"")
    log(f"=== FINAL SUMMARY ===")
    log(f"Total: {total}")
    log(f"Mining: {mining_final}")
    log(f"Verified (correct pool): {verified}")
    log(f"Wrong config: {wrong_config}")
    log(f"Not mining: {not_mining}")
    log(f"Stratum connections: {stratum}")
    log(f"=== DONE ===")

if __name__ == "__main__":
    main()
