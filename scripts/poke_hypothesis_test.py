#!/usr/bin/env python3
"""Graduated test of the Vnish HTTP-poke wake hypothesis.

Phase 1 — Single miner (5 miners, 3 trials each):
    With polling paused + sections 1+2 running, test individual miners.
    A) Wake WITHOUT poke → wait 150s → check mining
    B) Wake WITH poke (every 5s) → wait 150s → check mining

Phase 2 — Section 3 (35 miners):
    A) Pause polling, wake section 3 WITHOUT pokes → 150s → count
    B) Sleep section 3, wake WITH pokes → 150s → count

Phase 3 — Full fleet (all 5 sections):
    Sleep everything, wake ALL with pokes → 150s → count per section

Usage:
    python3 /tmp/poke_hypothesis_test.py                # All phases
    python3 /tmp/poke_hypothesis_test.py --phase 1      # Single miner only
    python3 /tmp/poke_hypothesis_test.py --phase 2      # Section only
    python3 /tmp/poke_hypothesis_test.py --phase 3      # Full fleet only
"""
import argparse
import json
import os
import socket
import subprocess
import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

# ── Config ──────────────────────────────────────────────────────────
API = "http://127.0.0.1:8080/dashboard/api"
VNISH_USER = "root"
VNISH_PASS = "root"
BOOT_TIMEOUT = 150
POLL_INTERVAL = 5
SECTION_SIZE = 35
# Known-dead miners (never boot, exclude from scoring)
DEAD_MINERS = {"192.168.95.109", "192.168.95.116", "192.168.95.184",
               "192.168.95.209", "192.168.95.220"}

# Results log file
LOG_FILE = "/tmp/poke_hypothesis_results.log"

# ── Helpers ─────────────────────────────────────────────────────────
def ip_sort_key(ip):
    return tuple(int(x) for x in ip.split("."))


def log(msg):
    ts = datetime.now().strftime("%H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    with open(LOG_FILE, "a") as f:
        f.write(line + "\n")


def api_get(path):
    with urllib.request.urlopen(f"{API}{path}", timeout=10) as r:
        return json.loads(r.read())


def api_post(path):
    r = urllib.request.Request(f"{API}{path}", method="POST")
    with urllib.request.urlopen(r, timeout=10) as resp:
        return json.loads(resp.read())


def get_all_miners():
    data = api_get("/miners")
    return sorted(set(m["miner_id"].replace("_", ".") for m in data), key=ip_sort_key)


def split_sections(ips):
    return [ips[i:i + SECTION_SIZE] for i in range(0, len(ips), SECTION_SIZE)]


def vnish_wake(ip):
    """Send wake command. Returns True if curl succeeded."""
    try:
        r = subprocess.run(
            ["curl", "-s", "--digest", "-u", f"{VNISH_USER}:{VNISH_PASS}",
             "-d", "mode=0", "-H", "Content-Type: application/x-www-form-urlencoded",
             "--connect-timeout", "5", "--max-time", "10",
             f"http://{ip}/cgi-bin/do_sleep_mode.cgi"],
            capture_output=True, text=True, timeout=15)
        return r.returncode == 0
    except:
        return False


def vnish_sleep(ip):
    """Send sleep command."""
    try:
        subprocess.run(
            ["curl", "-s", "--digest", "-u", f"{VNISH_USER}:{VNISH_PASS}",
             "-d", "mode=1", "-H", "Content-Type: application/x-www-form-urlencoded",
             "--connect-timeout", "5", "--max-time", "10",
             f"http://{ip}/cgi-bin/do_sleep_mode.cgi"],
            capture_output=True, text=True, timeout=15)
    except:
        pass


def vnish_poke(ip):
    """HTTP poke: hit get_miner_status.cgi to stimulate firmware."""
    try:
        subprocess.run(
            ["curl", "-s", "--digest", "-u", f"{VNISH_USER}:{VNISH_PASS}",
             "--connect-timeout", "2", "--max-time", "3", "-o", "/dev/null",
             f"http://{ip}/cgi-bin/get_miner_status.cgi"],
            capture_output=True, timeout=5)
    except:
        pass


def cgminer_mining(ip):
    """Check if cgminer is running and hashing."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(3)
        s.connect((ip, 4028))
        s.sendall(b'{"command":"summary"}')
        data = b""
        while True:
            chunk = s.recv(4096)
            if not chunk:
                break
            data += chunk
            if b"\x00" in data:
                break
        s.close()
        j = json.loads(data.decode("utf-8", errors="replace").rstrip("\x00"))
        return float(j.get("SUMMARY", [{}])[0].get("GHS 5s", 0)) > 0
    except:
        return False


def sleep_batch(ips, label=""):
    """Sleep a batch of miners in parallel."""
    log(f"  Sleeping {len(ips)} miners {label}...")
    with ThreadPoolExecutor(max_workers=30) as pool:
        list(pool.map(vnish_sleep, ips))
    time.sleep(5)


def wake_batch(ips, label=""):
    """Wake a batch of miners in parallel."""
    log(f"  Waking {len(ips)} miners {label}...")
    with ThreadPoolExecutor(max_workers=30) as pool:
        list(pool.map(vnish_wake, ips))


def wait_for_boot(ips, poke=False, timeout=BOOT_TIMEOUT):
    """Wait for miners to boot, optionally poking them.
    Returns dict of {ip: booted_bool}."""
    results = {ip: False for ip in ips}
    t0 = time.time()
    prev_count = -1

    while time.time() - t0 < timeout:
        time.sleep(POLL_INTERVAL)
        elapsed = time.time() - t0

        # Check / poke miners in parallel
        with ThreadPoolExecutor(max_workers=30) as pool:
            futures = {}
            for ip in ips:
                if not results[ip]:
                    if poke:
                        pool.submit(vnish_poke, ip)
                    futures[pool.submit(cgminer_mining, ip)] = ip

            for f in as_completed(futures):
                ip = futures[f]
                try:
                    if f.result():
                        results[ip] = True
                except:
                    pass

        mining = sum(1 for v in results.values() if v)
        if mining != prev_count:
            log(f"    T+{elapsed:5.0f}s | mining: {mining}/{len(ips)}")
            prev_count = mining
        if mining >= len(ips):
            break

    return results


def score(results, label):
    """Score results, excluding known-dead miners."""
    alive_ips = [ip for ip in results if ip not in DEAD_MINERS]
    dead_in_set = [ip for ip in results if ip in DEAD_MINERS]
    booted = sum(1 for ip in alive_ips if results[ip])
    total = len(alive_ips)
    pct = (booted / total * 100) if total > 0 else 0
    failed = [ip for ip in alive_ips if not results[ip]]

    log(f"  {label}: {booted}/{total} ({pct:.0f}%) [excl {len(dead_in_set)} known-dead]")
    if failed:
        log(f"    Failed: {', '.join(sorted(failed, key=ip_sort_key))}")
    return booted, total, pct


def set_polling(paused):
    try:
        api_post(f"/pause_polling?enabled={'true' if paused else 'false'}")
        state = "paused" if paused else "resumed"
        log(f"  Fleet manager polling {state}")
        time.sleep(5)
    except Exception as e:
        log(f"  WARNING: Failed to set polling: {e}")


def set_dev_mode(enabled):
    try:
        api_post(f"/dev_mode?enabled={'true' if enabled else 'false'}")
        log(f"  Dev mode {'enabled' if enabled else 'disabled'}")
    except Exception as e:
        log(f"  WARNING: Failed to set dev mode: {e}")


# ═══════════════════════════════════════════════════════════════════
# Phase 1: Single Miner Test
# ═══════════════════════════════════════════════════════════════════
def phase1(sections):
    log("=" * 60)
    log("PHASE 1: SINGLE MINER TEST (5 miners, A/B each)")
    log("=" * 60)

    sec1, sec2, sec3 = sections[0], sections[1], sections[2]
    all_12 = sec1 + sec2

    # Pick 5 test miners from section 3 (skip known-dead)
    test_miners = [ip for ip in sec3 if ip not in DEAD_MINERS][:5]
    log(f"Test miners: {test_miners}")

    # Ensure sections 1+2 are running (creates load context)
    log("Setting up load: waking sections 1+2 ...")
    sleep_batch(sec3, "(sec3)")
    wake_batch(all_12, "(sec 1+2)")
    log("  Waiting 180s for sections 1+2 to boot ...")
    time.sleep(180)

    running_12 = sum(1 for ip in all_12 if cgminer_mining(ip))
    log(f"  Sections 1+2 baseline: {running_12}/{len(all_12)} mining")

    # Pause polling — isolate poke effect
    set_polling(paused=True)

    results_no_poke = {}
    results_poke = {}

    # --- A) Without poke ---
    log("\n--- A) Wake WITHOUT poke ---")
    for ip in test_miners:
        log(f"  Testing {ip} — no poke ...")
        vnish_sleep(ip)
        time.sleep(10)
        vnish_wake(ip)
        time.sleep(BOOT_TIMEOUT)
        mining = cgminer_mining(ip)
        results_no_poke[ip] = mining
        log(f"    {ip}: {'MINING' if mining else 'FAILED'}")
        vnish_sleep(ip)
        time.sleep(10)

    # --- B) With poke ---
    log("\n--- B) Wake WITH poke (every 5s) ---")
    for ip in test_miners:
        log(f"  Testing {ip} — with poke ...")
        vnish_sleep(ip)
        time.sleep(10)
        vnish_wake(ip)
        t0 = time.time()
        mining = False
        while time.time() - t0 < BOOT_TIMEOUT:
            time.sleep(POLL_INTERVAL)
            vnish_poke(ip)
            if cgminer_mining(ip):
                mining = True
                elapsed = time.time() - t0
                log(f"    {ip}: MINING at T+{elapsed:.0f}s")
                break
        results_poke[ip] = mining
        if not mining:
            log(f"    {ip}: FAILED (no mining after {BOOT_TIMEOUT}s)")
        vnish_sleep(ip)
        time.sleep(10)

    # Summary
    log("\n--- Phase 1 Summary ---")
    no_poke_count = sum(1 for v in results_no_poke.values() if v)
    poke_count = sum(1 for v in results_poke.values() if v)
    log(f"  Without poke: {no_poke_count}/{len(test_miners)}")
    log(f"  With poke:    {poke_count}/{len(test_miners)}")
    log(f"  {'HYPOTHESIS SUPPORTED' if poke_count > no_poke_count else 'NO CLEAR DIFFERENCE'}")

    for ip in test_miners:
        a = "MINE" if results_no_poke.get(ip) else "FAIL"
        b = "MINE" if results_poke.get(ip) else "FAIL"
        log(f"    {ip}: no_poke={a}  poke={b}")

    return results_no_poke, results_poke


# ═══════════════════════════════════════════════════════════════════
# Phase 2: Section Test (35 miners)
# ═══════════════════════════════════════════════════════════════════
def phase2(sections):
    log("\n" + "=" * 60)
    log("PHASE 2: SECTION 3 TEST (35 miners, A/B)")
    log("=" * 60)

    sec1, sec2, sec3 = sections[0], sections[1], sections[2]
    all_12 = sec1 + sec2

    # Ensure sections 1+2 are running
    log("Ensuring sections 1+2 running ...")
    sleep_batch(sec3, "(sec3)")
    wake_batch(all_12, "(sec 1+2)")
    log("  Waiting 180s for boot ...")
    time.sleep(180)

    running_12 = sum(1 for ip in all_12 if cgminer_mining(ip))
    log(f"  Sections 1+2: {running_12}/{len(all_12)} mining")

    # Pause polling
    set_polling(paused=True)

    # --- A) Section 3 WITHOUT pokes ---
    log("\n--- A) Section 3 WITHOUT pokes ---")
    sleep_batch(sec3, "(sec3)")
    time.sleep(10)
    wake_batch(sec3, "(sec3)")
    res_no_poke = wait_for_boot(sec3, poke=False)
    b_np, t_np, p_np = score(res_no_poke, "No-poke")

    # --- B) Section 3 WITH pokes ---
    log("\n--- B) Section 3 WITH pokes ---")
    sleep_batch(sec3, "(sec3)")
    time.sleep(10)
    wake_batch(sec3, "(sec3)")
    res_poke = wait_for_boot(sec3, poke=True)
    b_p, t_p, p_p = score(res_poke, "With-poke")

    # Summary
    log("\n--- Phase 2 Summary ---")
    log(f"  Section 3 no poke:  {b_np}/{t_np} ({p_np:.0f}%)")
    log(f"  Section 3 poke:     {b_p}/{t_p} ({p_p:.0f}%)")
    delta = p_p - p_np
    log(f"  Delta: +{delta:.0f}pp  {'HYPOTHESIS SUPPORTED' if delta > 10 else 'INCONCLUSIVE'}")

    return res_no_poke, res_poke


# ═══════════════════════════════════════════════════════════════════
# Phase 3: Full Fleet Test (all sections)
# ═══════════════════════════════════════════════════════════════════
def phase3(sections):
    log("\n" + "=" * 60)
    log("PHASE 3: FULL FLEET TEST (all sections, with pokes)")
    log("=" * 60)

    all_ips = []
    for sec in sections:
        all_ips.extend(sec)

    # Sleep everything
    sleep_batch(all_ips, "(all)")
    time.sleep(15)

    # Pause polling (poke replaces it)
    set_polling(paused=True)

    # Wake ALL at once
    log(f"Waking ALL {len(all_ips)} miners with HTTP pokes ...")
    wake_batch(all_ips, "(all)")

    # Wait for boot with pokes on every miner
    res = wait_for_boot(all_ips, poke=True, timeout=180)

    # Score per section
    log("\n--- Phase 3 Per-Section Results ---")
    section_results = []
    for i, sec in enumerate(sections):
        sec_res = {ip: res.get(ip, False) for ip in sec}
        b, t, p = score(sec_res, f"Section {i+1}")
        section_results.append((b, t, p))

    # Overall
    alive = [ip for ip in all_ips if ip not in DEAD_MINERS]
    booted = sum(1 for ip in alive if res.get(ip, False))
    total = len(alive)
    pct = (booted / total * 100) if total > 0 else 0
    log(f"\n  FULL FLEET: {booted}/{total} ({pct:.0f}%)")
    log(f"  (Previous best without pokes: ~89% sequential, ~17% section 4+)")

    failed = sorted([ip for ip in alive if not res.get(ip, False)], key=ip_sort_key)
    if failed:
        log(f"  Failed ({len(failed)}): {', '.join(failed)}")

    return res, section_results


# ═══════════════════════════════════════════════════════════════════
# Main
# ═══════════════════════════════════════════════════════════════════
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--phase", type=int, help="Run only phase 1, 2, or 3")
    args = parser.parse_args()

    # Clear log
    with open(LOG_FILE, "w") as f:
        f.write(f"=== Poke Hypothesis Test — {datetime.now().isoformat()} ===\n")

    log(f"Starting poke hypothesis test at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

    try:
        ips = get_all_miners()
        sections = split_sections(ips)
        log(f"Fleet: {len(ips)} miners, {len(sections)} sections")
        for i, sec in enumerate(sections):
            dead_in = [ip for ip in sec if ip in DEAD_MINERS]
            log(f"  Section {i+1}: {sec[0]}–{sec[-1]} ({len(sec)} miners, {len(dead_in)} dead)")

        set_dev_mode(True)

        if args.phase is None or args.phase == 1:
            phase1(sections)

        if args.phase is None or args.phase == 2:
            phase2(sections)

        if args.phase is None or args.phase == 3:
            phase3(sections)

    except KeyboardInterrupt:
        log("\nInterrupted by user")
    except Exception as e:
        log(f"\nERROR: {e}")
        import traceback
        traceback.print_exc()
    finally:
        # Always clean up
        log("\n--- Cleanup ---")
        try:
            set_polling(paused=False)
        except:
            pass
        try:
            set_dev_mode(False)
        except:
            pass
        log("Done. Full log at " + LOG_FILE)


if __name__ == "__main__":
    main()
