#!/usr/bin/env python3
"""Full fleet poke test using async HTTP (httpx.AsyncClient).

Previous test used curl subprocesses → too slow at 172 miners (14%).
This version uses async HTTP with high concurrency to maintain ~5s
poke interval per miner regardless of fleet size.

Flow:
  1. Sleep all miners
  2. Pause fleet manager polling
  3. Wake all 172 miners
  4. Async poke loop: hit get_miner_status.cgi on every miner every ~5s
  5. Async check loop: test CGMiner port 4028 for mining
  6. Report per-section results

Usage:
    python3 /tmp/async_fleet_poke_test.py
    python3 /tmp/async_fleet_poke_test.py --timeout 240   # longer timeout
"""
import argparse
import asyncio
import json
import socket
import subprocess
import sys
import time
from datetime import datetime

import httpx

# ── Config ──────────────────────────────────────────────────────────
API = "http://127.0.0.1:8080/dashboard/api"
VNISH_USER = "root"
VNISH_PASS = "root"
BOOT_TIMEOUT = 210  # longer than before (curve was still rising at 150s)
POKE_INTERVAL = 5   # seconds between poke rounds
SECTION_SIZE = 35
CONCURRENCY = 172    # all miners in parallel
DEAD_MINERS = {"192.168.95.109", "192.168.95.116", "192.168.95.184",
               "192.168.95.209", "192.168.95.220"}
LOG_FILE = "/tmp/async_fleet_poke.log"


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
    import urllib.request
    with urllib.request.urlopen(f"{API}{path}", timeout=10) as r:
        return json.loads(r.read())


def api_post(path):
    import urllib.request
    r = urllib.request.Request(f"{API}{path}", method="POST")
    with urllib.request.urlopen(r, timeout=10) as resp:
        return json.loads(resp.read())


def get_all_miners():
    data = api_get("/miners")
    return sorted(set(m["miner_id"].replace("_", ".") for m in data), key=ip_sort_key)


def split_sections(ips):
    return [ips[i:i + SECTION_SIZE] for i in range(0, len(ips), SECTION_SIZE)]


# ── Async operations ────────────────────────────────────────────────
async def async_vnish_wake(client: httpx.AsyncClient, ip: str) -> bool:
    """Send wake command via async HTTP."""
    url = f"http://{ip}/cgi-bin/do_sleep_mode.cgi"
    auth = httpx.DigestAuth(VNISH_USER, VNISH_PASS)
    try:
        resp = await client.post(
            url, auth=auth,
            data={"mode": "0"},
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=10.0
        )
        return resp.status_code == 200
    except Exception:
        return False


async def async_vnish_sleep(client: httpx.AsyncClient, ip: str) -> bool:
    """Send sleep command via async HTTP."""
    url = f"http://{ip}/cgi-bin/do_sleep_mode.cgi"
    auth = httpx.DigestAuth(VNISH_USER, VNISH_PASS)
    try:
        resp = await client.post(
            url, auth=auth,
            data={"mode": "1"},
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=10.0
        )
        return resp.status_code == 200
    except Exception:
        return False


async def async_poke(client: httpx.AsyncClient, ip: str) -> bool:
    """Async HTTP poke: hit get_miner_status.cgi."""
    url = f"http://{ip}/cgi-bin/get_miner_status.cgi"
    auth = httpx.DigestAuth(VNISH_USER, VNISH_PASS)
    try:
        resp = await client.get(url, auth=auth, timeout=5.0)
        return resp.status_code == 200
    except Exception:
        return False


async def async_cgminer_check(ip: str) -> bool:
    """Check CGMiner port 4028 for mining (async socket)."""
    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(ip, 4028), timeout=3.0
        )
        writer.write(b'{"command":"summary"}')
        await writer.drain()

        data = b""
        while True:
            try:
                chunk = await asyncio.wait_for(reader.read(4096), timeout=3.0)
                if not chunk:
                    break
                data += chunk
                if b"\x00" in data:
                    break
            except asyncio.TimeoutError:
                break

        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass

        text = data.decode("utf-8", errors="replace").rstrip("\x00")
        j = json.loads(text)
        ghs = float(j.get("SUMMARY", [{}])[0].get("GHS 5s", 0))
        return ghs > 0
    except Exception:
        return False


async def sleep_all(client: httpx.AsyncClient, ips: list):
    """Sleep all miners concurrently."""
    log(f"  Sleeping {len(ips)} miners ...")
    sem = asyncio.Semaphore(CONCURRENCY)

    async def _sleep(ip):
        async with sem:
            return await async_vnish_sleep(client, ip)

    await asyncio.gather(*[_sleep(ip) for ip in ips])
    log(f"  Sleep commands sent.")
    await asyncio.sleep(10)


async def wake_all(client: httpx.AsyncClient, ips: list):
    """Wake all miners concurrently."""
    log(f"  Waking {len(ips)} miners ...")
    sem = asyncio.Semaphore(CONCURRENCY)

    async def _wake(ip):
        async with sem:
            return await async_vnish_wake(client, ip)

    results = await asyncio.gather(*[_wake(ip) for ip in ips])
    ok = sum(1 for r in results if r)
    log(f"  Wake commands sent: {ok}/{len(ips)} acknowledged.")


async def poke_and_check_loop(client: httpx.AsyncClient, ips: list, timeout: int):
    """Main loop: poke all miners and check CGMiner every POKE_INTERVAL seconds.
    
    Returns dict of {ip: True/False}."""
    results = {ip: False for ip in ips}
    t0 = time.time()
    prev_count = -1
    round_num = 0

    while time.time() - t0 < timeout:
        round_start = time.time()
        round_num += 1
        elapsed = round_start - t0

        # Determine which miners still need poking/checking
        pending = [ip for ip in ips if not results[ip]]
        if not pending:
            break

        # Poke + check in parallel
        sem = asyncio.Semaphore(CONCURRENCY)

        async def _poke_and_check(ip):
            async with sem:
                await async_poke(client, ip)
                mining = await async_cgminer_check(ip)
                return ip, mining

        tasks = [_poke_and_check(ip) for ip in pending]
        outcomes = await asyncio.gather(*tasks)

        for ip, mining in outcomes:
            if mining:
                results[ip] = True

        mining_count = sum(1 for v in results.values() if v)
        round_elapsed = time.time() - round_start

        if mining_count != prev_count or round_num % 3 == 0:
            log(f"    T+{elapsed:5.0f}s | mining: {mining_count:3d}/{len(ips)} "
                f"| pending: {len(pending)} | round: {round_elapsed:.1f}s")
            prev_count = mining_count

        # Wait remainder of POKE_INTERVAL
        sleep_time = max(0, POKE_INTERVAL - (time.time() - round_start))
        if sleep_time > 0:
            await asyncio.sleep(sleep_time)

    return results


def score_section(results: dict, section_ips: list, label: str) -> tuple:
    """Score a section, excluding known-dead miners."""
    sec_results = {ip: results.get(ip, False) for ip in section_ips}
    alive = [ip for ip in section_ips if ip not in DEAD_MINERS]
    dead_in = [ip for ip in section_ips if ip in DEAD_MINERS]
    booted = sum(1 for ip in alive if sec_results.get(ip, False))
    total = len(alive)
    pct = (booted / total * 100) if total > 0 else 0
    failed = sorted([ip for ip in alive if not sec_results.get(ip, False)], key=ip_sort_key)

    log(f"  {label}: {booted}/{total} ({pct:.0f}%) [excl {len(dead_in)} dead]")
    if failed:
        log(f"    Failed: {', '.join(failed)}")
    return booted, total, pct


# ═══════════════════════════════════════════════════════════════════
# Main
# ═══════════════════════════════════════════════════════════════════
async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--timeout", type=int, default=BOOT_TIMEOUT,
                        help=f"Boot timeout in seconds (default: {BOOT_TIMEOUT})")
    args = parser.parse_args()
    timeout = args.timeout

    with open(LOG_FILE, "w") as f:
        f.write(f"=== Async Fleet Poke Test — {datetime.now().isoformat()} ===\n")

    log(f"Starting async fleet poke test at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    log(f"Config: timeout={timeout}s, poke_interval={POKE_INTERVAL}s, concurrency={CONCURRENCY}")

    try:
        ips = get_all_miners()
        sections = split_sections(ips)
        log(f"Fleet: {len(ips)} miners, {len(sections)} sections")
        for i, sec in enumerate(sections):
            dead_in = [ip for ip in sec if ip in DEAD_MINERS]
            log(f"  Section {i+1}: {sec[0]}–{sec[-1]} ({len(sec)} miners, {len(dead_in)} dead)")

        # Enable dev mode
        api_post("/dev_mode?enabled=true")
        log("  Dev mode enabled")

        # Use a single persistent httpx client for all operations
        async with httpx.AsyncClient() as client:
            # Step 1: Sleep everything
            log("\n=== STEP 1: Sleep all miners ===")
            await sleep_all(client, ips)
            await asyncio.sleep(15)

            # Step 2: Pause fleet manager polling
            log("\n=== STEP 2: Pause fleet manager polling ===")
            api_post("/pause_polling?enabled=true")
            log("  Polling paused — zero FM traffic")
            await asyncio.sleep(5)

            # Step 3: Wake all miners
            log(f"\n=== STEP 3: Wake all {len(ips)} miners ===")
            await wake_all(client, ips)

            # Step 4: Async poke + check loop
            log(f"\n=== STEP 4: Async poke loop (timeout={timeout}s) ===")
            t_start = time.time()
            results = await poke_and_check_loop(client, ips, timeout)
            t_total = time.time() - t_start

            # Results
            log(f"\n=== RESULTS (elapsed: {t_total:.0f}s) ===")
            log("")

            overall_booted = 0
            overall_total = 0
            for i, sec in enumerate(sections):
                b, t, p = score_section(results, sec, f"Section {i+1}")
                overall_booted += b
                overall_total += t

            overall_pct = (overall_booted / overall_total * 100) if overall_total > 0 else 0
            log(f"\n  FULL FLEET: {overall_booted}/{overall_total} ({overall_pct:.0f}%)")
            log("")
            log("  Comparison to previous tests:")
            log("    Curl-based poke (prev test):  24/167 (14%)")
            log("    FM polling (cliff test 1+2+3): 81/105 (77%)")
            log("    FM polling (sec 3 alone):      94%")
            log(f"    THIS TEST (async poke):       {overall_booted}/{overall_total} ({overall_pct:.0f}%)")

            # Cleanup: resume polling, sleep all
            log("\n=== Cleanup ===")
            api_post("/pause_polling?enabled=false")
            log("  Polling resumed")
            await sleep_all(client, ips)

    except KeyboardInterrupt:
        log("\nInterrupted")
    except Exception as e:
        log(f"\nERROR: {e}")
        import traceback
        traceback.print_exc()
    finally:
        try:
            api_post("/pause_polling?enabled=false")
        except:
            pass
        try:
            api_post("/dev_mode?enabled=false")
        except:
            pass
        log("  Dev mode disabled")
        log(f"Done. Log: {LOG_FILE}")


if __name__ == "__main__":
    asyncio.run(main())
