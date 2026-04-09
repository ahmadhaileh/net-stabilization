#!/usr/bin/env python3
"""Network diagnosis: determine if boot failures under load are caused by
fleet manager polling (software) or physical network saturation (hardware).

Strategy:
  Phase 1 — Latency baseline: Measure ping, HTTP, and CGMiner latency to
            all miners with 0 load and with 64 miners running.
  Phase 2 — Boot test WITHOUT fleet manager: Stop the Docker container
            (kills background polling), wake sections 1+2, then boot
            section 3.  Compare to the 49% result from cliff test.
  Phase 3 — Boot test WITH fleet manager: Restart Docker, repeat.

If Phase 2 >> 49% → fleet manager polling is the bottleneck (software fix).
If Phase 2 ≈ 49%  → physical network is the bottleneck (hardware fix).

Usage:
    python3 /tmp/network_diagnosis.py                    # Full diagnosis
    python3 /tmp/network_diagnosis.py --phase 1          # Latency only
    python3 /tmp/network_diagnosis.py --phase 2          # Boot without FM
    python3 /tmp/network_diagnosis.py --phase 3          # Boot with FM
    python3 /tmp/network_diagnosis.py --skip-phase1      # Skip latency, do boot tests only
"""

import argparse
import json
import os
import socket
import subprocess
import statistics
import sys
import time
import urllib.request

# ── Config ──────────────────────────────────────────────────────────
DASHBOARD_API = "http://127.0.0.1:8080/dashboard/api"
METER_URL = "http://192.168.95.4:8044"
VNISH_USER = "root"
VNISH_PASS = "root"
CGMINER_PORT = 4028
BOOT_TIMEOUT = 150
POLL_INTERVAL = 5
KW_PER_MINER = 1.4
DOCKER_COMPOSE_DIR = "/home/dkk/net-stabilization"
DOCKER_SERVICE = "grid-stabilization"


# ── Helpers ─────────────────────────────────────────────────────────
def ip_sort_key(ip: str):
    return tuple(int(x) for x in ip.split("."))


def run_cmd(cmd, timeout=30):
    """Run a shell command and return (returncode, stdout, stderr)."""
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.returncode, r.stdout.strip(), r.stderr.strip()
    except subprocess.TimeoutExpired:
        return -1, "", "timeout"
    except Exception as e:
        return -1, "", str(e)


def get_all_miner_ips() -> list[str]:
    """Fetch miner list from dashboard API, sorted ascending by IP."""
    url = f"{DASHBOARD_API}/miners"
    req = urllib.request.Request(url)
    with urllib.request.urlopen(req, timeout=10) as resp:
        miners = json.loads(resp.read())
    return sorted(set(m["miner_id"].replace("_", ".") for m in miners), key=ip_sort_key)


def get_miner_ips_fallback() -> list[str]:
    """Generate miner IPs from known range when API is unavailable."""
    # Known range from previous tests: .9 through .250
    ips = []
    for i in range(9, 251):
        ips.append(f"192.168.95.{i}")
    return ips


def split_into_sections(ips, section_kw=50):
    miners_per_section = max(1, int(section_kw / KW_PER_MINER))
    sections = []
    for i in range(0, len(ips), miners_per_section):
        sections.append(ips[i:i + miners_per_section])
    return sections


def vnish_sleep(ip, wake):
    """Send wake/sleep via curl. Returns (success, latency_ms)."""
    mode = "0" if wake else "1"
    url = f"http://{ip}/cgi-bin/do_sleep_mode.cgi"
    t0 = time.time()
    try:
        result = subprocess.run(
            ["curl", "-s", "--digest", "-u", f"{VNISH_USER}:{VNISH_PASS}",
             "-d", f"mode={mode}",
             "-H", "Content-Type: application/x-www-form-urlencoded",
             "--connect-timeout", "5", "--max-time", "10", url],
            capture_output=True, text=True, timeout=15,
        )
        latency_ms = (time.time() - t0) * 1000
        return result.returncode == 0, latency_ms
    except Exception:
        latency_ms = (time.time() - t0) * 1000
        return False, latency_ms


def cgminer_is_mining(ip):
    """Check if CGMiner port 4028 responds with hashrate > 0."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(3)
        s.connect((ip, CGMINER_PORT))
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
        text = data.decode("utf-8", errors="replace").rstrip("\x00")
        j = json.loads(text)
        summary = j.get("SUMMARY", [{}])[0]
        ghs5s = summary.get("GHS 5s", 0)
        return float(ghs5s) > 0
    except Exception:
        return False


def sleep_miners(ips, label=""):
    tag = f" ({label})" if label else ""
    print(f"  Sleeping {len(ips)} miners{tag} ...", end="", flush=True)
    for ip in ips:
        vnish_sleep(ip, wake=False)
    print(" done.  Settling ...", end="", flush=True)
    time.sleep(10)
    print(" ok")


def docker_is_running():
    """Check if the grid-stabilization container is running."""
    rc, out, _ = run_cmd(["docker", "ps", "--filter", f"name={DOCKER_SERVICE}",
                          "--format", "{{.Status}}"])
    return rc == 0 and "Up" in out


def docker_stop():
    """Stop the Docker container."""
    print("  Stopping Docker container (fleet manager) ...", end="", flush=True)
    rc, _, err = run_cmd(
        ["docker", "compose", "-f", f"{DOCKER_COMPOSE_DIR}/docker-compose.yml",
         "stop"], timeout=60)
    if rc == 0:
        print(" stopped.")
    else:
        print(f" FAILED: {err}")
    time.sleep(3)
    return rc == 0


def docker_start():
    """Start the Docker container."""
    print("  Starting Docker container (fleet manager) ...", end="", flush=True)
    rc, _, err = run_cmd(
        ["docker", "compose", "-f", f"{DOCKER_COMPOSE_DIR}/docker-compose.yml",
         "up", "-d"], timeout=120)
    if rc == 0:
        print(" started.")
        # Wait for API to be ready
        print("  Waiting for API ...", end="", flush=True)
        for _ in range(30):
            time.sleep(2)
            try:
                req = urllib.request.Request(f"{DASHBOARD_API}/status")
                with urllib.request.urlopen(req, timeout=5) as resp:
                    if resp.status == 200:
                        print(" ready.")
                        return True
            except Exception:
                pass
        print(" TIMEOUT waiting for API.")
    else:
        print(f" FAILED: {err}")
    return rc == 0


def enable_dev_mode():
    """Enable dev mode on fleet manager."""
    try:
        req = urllib.request.Request(
            f"{DASHBOARD_API}/dev_mode?enabled=true", method="POST")
        with urllib.request.urlopen(req, timeout=10) as resp:
            print(f"  Dev mode: {json.loads(resp.read())}")
    except Exception as e:
        print(f"  WARNING: Could not enable dev mode: {e}")


def set_polling_paused(paused: bool):
    """Pause or resume fleet manager background polling."""
    state = "paused" if paused else "resumed"
    try:
        req = urllib.request.Request(
            f"{DASHBOARD_API}/pause_polling?enabled={'true' if paused else 'false'}",
            method="POST")
        with urllib.request.urlopen(req, timeout=10) as resp:
            result = json.loads(resp.read())
            print(f"  Polling: {result}")
    except Exception as e:
        print(f"  WARNING: Could not set polling {state}: {e}")


# ── Phase 1: Latency Measurement ───────────────────────────────────

def measure_ping(ip, count=3):
    """Ping a miner and return avg latency in ms, or None if unreachable."""
    rc, out, _ = run_cmd(["ping", "-c", str(count), "-W", "2", ip], timeout=15)
    if rc != 0:
        return None
    # Parse: "round-trip min/avg/max/stddev = 0.5/1.2/2.0/0.3 ms"
    for line in out.split("\n"):
        if "avg" in line and "/" in line:
            parts = line.split("=")[-1].strip().split("/")
            if len(parts) >= 2:
                return float(parts[1])
    return None


def measure_cgminer_latency(ip):
    """Send CGMiner summary command and measure round-trip time (ms)."""
    t0 = time.time()
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(5)
        s.connect((ip, CGMINER_PORT))
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
        latency_ms = (time.time() - t0) * 1000
        return latency_ms
    except Exception:
        return None


def measure_http_latency(ip):
    """Send Vnish HTTP request and measure round-trip time (ms)."""
    url = f"http://{ip}/cgi-bin/get_system_info.cgi"
    t0 = time.time()
    try:
        result = subprocess.run(
            ["curl", "-s", "--digest", "-u", f"{VNISH_USER}:{VNISH_PASS}",
             "--connect-timeout", "3", "--max-time", "5", "-o", "/dev/null",
             "-w", "%{time_total}", url],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode == 0:
            return float(result.stdout.strip()) * 1000
        return None
    except Exception:
        return None


def run_latency_test(ips, label, sample_ips=None):
    """Measure ping, CGMiner, and HTTP latency across miners."""
    if sample_ips is None:
        # Sample 20 miners spread across the range
        step = max(1, len(ips) // 20)
        sample_ips = [ips[i] for i in range(0, len(ips), step)][:20]

    print(f"\n  ┌── Latency Test: {label} ({len(sample_ips)} miners sampled) ──┐")

    ping_times = []
    cgminer_times = []
    http_times = []
    ping_fails = 0
    cgminer_fails = 0
    http_fails = 0

    for i, ip in enumerate(sample_ips):
        # Ping
        p = measure_ping(ip, count=2)
        if p is not None:
            ping_times.append(p)
        else:
            ping_fails += 1

        # CGMiner (only for miners that should be mining)
        c = measure_cgminer_latency(ip)
        if c is not None:
            cgminer_times.append(c)
        else:
            cgminer_fails += 1

        # HTTP (Vnish web API)
        h = measure_http_latency(ip)
        if h is not None:
            http_times.append(h)
        else:
            http_fails += 1

        if (i + 1) % 5 == 0:
            print(f"    Measured {i+1}/{len(sample_ips)} ...", flush=True)

    def stats_str(times, fails):
        if not times:
            return f"no data ({fails} failures)"
        avg = statistics.mean(times)
        med = statistics.median(times)
        mx = max(times)
        mn = min(times)
        p95 = sorted(times)[int(len(times) * 0.95)] if len(times) > 2 else mx
        return (f"avg={avg:.0f}ms  med={med:.0f}ms  p95={p95:.0f}ms  "
                f"min={mn:.0f}ms  max={mx:.0f}ms  fails={fails}")

    print(f"    Ping   : {stats_str(ping_times, ping_fails)}")
    print(f"    CGMiner: {stats_str(cgminer_times, cgminer_fails)}")
    print(f"    HTTP   : {stats_str(http_times, http_fails)}")
    print(f"  └{'─'*58}┘")

    return {
        "label": label,
        "ping": {"times": ping_times, "fails": ping_fails},
        "cgminer": {"times": cgminer_times, "fails": cgminer_fails},
        "http": {"times": http_times, "fails": http_fails},
    }


# ── Phase 2/3: Boot Test ──────────────────────────────────────────

def boot_section_timed(section_ips, already_running_count, label):
    """Wake a section and record per-miner timing.
    Returns (results_dict, timing_data)."""
    n = len(section_ips)
    print(f"\n  ╔══ Boot Test: {label} ({n} miners, "
          f"{already_running_count} already running) ══╗")

    # Send wake commands — record individual latency
    wake_timings = []
    print(f"  Waking {n} miners ...", flush=True)
    t_start = time.time()
    for ip in section_ips:
        ok, latency_ms = vnish_sleep(ip, wake=True)
        wake_timings.append({"ip": ip, "ok": ok, "latency_ms": latency_ms})
    wake_dur = time.time() - t_start
    wake_ok = sum(1 for w in wake_timings if w["ok"])
    wake_fail = n - wake_ok
    wake_lats = [w["latency_ms"] for w in wake_timings if w["ok"]]
    avg_wake = statistics.mean(wake_lats) if wake_lats else 0

    print(f"    Wake commands: {wake_ok}/{n} ok in {wake_dur:.1f}s "
          f"(avg {avg_wake:.0f}ms/miner)")
    if wake_fail > 0:
        fails = [w["ip"] for w in wake_timings if not w["ok"]]
        print(f"    Wake FAILED for: {', '.join(fails)}")

    # Poll until all miners are mining or timeout — track when each miner starts
    results = {ip: False for ip in section_ips}
    first_mining_time = {}  # ip -> seconds since wake
    t0 = time.time()
    prev_count = 0

    while time.time() - t0 < BOOT_TIMEOUT:
        time.sleep(POLL_INTERVAL)
        elapsed = time.time() - t0

        # Measure poll cycle time
        poll_start = time.time()
        for ip in section_ips:
            if not results[ip] and cgminer_is_mining(ip):
                results[ip] = True
                first_mining_time[ip] = elapsed
        poll_dur = (time.time() - poll_start) * 1000

        mining_now = sum(1 for v in results.values() if v)

        if mining_now != prev_count or int(elapsed) % 15 < POLL_INTERVAL:
            print(f"    T+{elapsed:5.0f}s | mining: {mining_now:3d}/{n} | "
                  f"poll: {poll_dur:.0f}ms")
            prev_count = mining_now

        if mining_now >= n:
            print(f"    ✓ 100% at T+{elapsed:.0f}s")
            break
    else:
        mining_now = sum(1 for v in results.values() if v)
        failed_ips = [ip for ip, ok in results.items() if not ok]
        print(f"    ⏰ Timeout after {BOOT_TIMEOUT}s: "
              f"{mining_now}/{n} mining, {len(failed_ips)} failed")
        for ip in failed_ips:
            print(f"       ✗ {ip}")

    passed = sum(1 for v in results.values() if v)
    print(f"  Result: {passed}/{n} ({passed/n*100:.0f}%)")
    print(f"  ╚{'═'*56}╝")

    return results, {
        "label": label,
        "passed": passed,
        "total": n,
        "rate": passed / n * 100,
        "already_running": already_running_count,
        "wake_timings": wake_timings,
        "avg_wake_ms": avg_wake,
        "boot_times": first_mining_time,
    }


def wake_sections_1_2(sections, all_ips):
    """Wake sections 1 and 2, return count of running miners."""
    sec1, sec2 = sections[0], sections[1]
    print(f"\n  Waking Section 1 ({len(sec1)} miners) ...")
    for ip in sec1:
        vnish_sleep(ip, wake=True)
    print(f"  Waking Section 2 ({len(sec2)} miners) ...")
    for ip in sec2:
        vnish_sleep(ip, wake=True)

    # Wait for them to boot
    print(f"  Waiting 180s for sections 1+2 to boot ...", flush=True)
    time.sleep(180)

    # Count how many are running
    running = 0
    for ip in sec1 + sec2:
        if cgminer_is_mining(ip):
            running += 1
    print(f"  Sections 1+2: {running}/{len(sec1)+len(sec2)} mining")
    return running


# ── Main ────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Network diagnosis for miner boot failures")
    parser.add_argument("--phase", type=int, default=None,
                        help="Run only this phase (1, 2, or 3)")
    parser.add_argument("--skip-phase1", action="store_true",
                        help="Skip latency measurement, do boot tests only")
    args = parser.parse_args()

    print("=" * 70)
    print("  NETWORK DIAGNOSIS")
    print("  Goal: Determine if boot failures under load are caused by")
    print("        fleet manager polling (software) or network saturation (hardware)")
    print("=" * 70)

    # Get miner list — try API first, fall back to known range
    ips = None
    if docker_is_running():
        try:
            ips = get_all_miner_ips()
            print(f"\n  Miners from API: {len(ips)}")
        except Exception:
            pass

    if not ips:
        ips = get_miner_ips_fallback()
        print(f"\n  Miners from known range: {len(ips)} (API unavailable)")

    sections = split_into_sections(ips)
    print(f"  Sections: {len(sections)}")
    for i, sec in enumerate(sections):
        print(f"    Section {i+1}: {sec[0]} – {sec[-1]} ({len(sec)} miners)")

    sec3 = sections[2] if len(sections) >= 3 else []
    if not sec3:
        print("ERROR: Need at least 3 sections")
        sys.exit(1)

    # Determine which phases to run
    run_phase1 = (args.phase is None or args.phase == 1) and not args.skip_phase1
    run_phase2 = args.phase is None or args.phase == 2
    run_phase3 = args.phase is None or args.phase == 3

    results_summary = {}

    # ================================================================
    # PHASE 1: Latency Measurement
    # ================================================================
    if run_phase1:
        print(f"\n{'='*70}")
        print(f"  PHASE 1: LATENCY MEASUREMENT")
        print(f"  Measure network latency to miners under different loads")
        print(f"{'='*70}")

        # Ensure Docker is running
        if not docker_is_running():
            docker_start()
        enable_dev_mode()

        # Pick sample miners from each section
        sample_sec1 = sections[0][::7][:5]   # 5 from section 1
        sample_sec2 = sections[1][::7][:5]   # 5 from section 2
        sample_sec3 = sections[2][::7][:5]   # 5 from section 3
        sample_all = sample_sec1 + sample_sec2 + sample_sec3

        # 1A: All miners asleep, polling active
        print("\n  --- 1A: All miners asleep (zero load, polling active) ---")
        set_polling_paused(False)
        sleep_miners(ips, "all")
        time.sleep(5)
        lat_idle = run_latency_test(ips, "All asleep, polling on", sample_all)
        results_summary["latency_idle"] = lat_idle

        # 1B: Sections 1+2 running, polling active
        print("\n  --- 1B: Sections 1+2 running, polling ACTIVE ---")
        running = wake_sections_1_2(sections, ips)
        lat_load_poll = run_latency_test(ips, f"{running} mining + polling", sample_sec3)
        results_summary["latency_load_poll_on"] = lat_load_poll

        # 1C: Same load, polling PAUSED
        print("\n  --- 1C: Sections 1+2 running, polling PAUSED ---")
        set_polling_paused(True)
        time.sleep(10)  # Let in-flight polls finish
        lat_load_no_poll = run_latency_test(ips, f"{running} mining, no polling", sample_sec3)
        results_summary["latency_load_poll_off"] = lat_load_no_poll

        # Cleanup
        set_polling_paused(False)
        sleep_miners(ips, "cleanup after phase 1")

    # ================================================================
    # PHASE 2: Boot Test WITHOUT Polling
    # ================================================================
    if run_phase2:
        print(f"\n{'='*70}")
        print(f"  PHASE 2: BOOT SECTION 3 — POLLING PAUSED")
        print(f"  Fleet manager running but all background polling stopped")
        print(f"  If this passes ~90%+, the fleet manager polling is the bottleneck")
        print(f"{'='*70}")

        # Ensure Docker is running
        if not docker_is_running():
            docker_start()
        enable_dev_mode()

        # Sleep all
        sleep_miners(ips, "all")
        time.sleep(5)

        # Wake sections 1+2 to create load (64 miners)
        running_12 = wake_sections_1_2(sections, ips)

        # NOW pause polling — eliminate all fleet manager network traffic
        set_polling_paused(True)
        time.sleep(10)  # Let in-flight polls drain
        print(f"  Polling paused — zero fleet manager traffic")

        # Boot section 3 — the critical test
        sec3_results, sec3_timing = boot_section_timed(
            sec3, running_12,
            "Section 3 — POLLING PAUSED"
        )
        results_summary["boot_no_poll"] = sec3_timing

        # Resume polling
        set_polling_paused(False)

        # Cleanup
        sleep_miners(ips, "cleanup after phase 2")

    # ================================================================
    # PHASE 3: Boot Test WITH Polling
    # ================================================================
    if run_phase3:
        print(f"\n{'='*70}")
        print(f"  PHASE 3: BOOT SECTION 3 — POLLING ACTIVE")
        print(f"  Fleet manager running with full background polling")
        print(f"  Compare to Phase 2 to isolate polling impact")
        print(f"{'='*70}")

        # Ensure Docker is running
        if not docker_is_running():
            docker_start()
        enable_dev_mode()
        set_polling_paused(False)
        time.sleep(10)  # Let polling stabilize

        # Sleep all
        sleep_miners(ips, "all")
        time.sleep(5)

        # Wake sections 1+2
        running_12 = wake_sections_1_2(sections, ips)

        # Boot section 3 — with fleet manager polling
        sec3_results, sec3_timing = boot_section_timed(
            sec3, running_12,
            "Section 3 — POLLING ACTIVE"
        )
        results_summary["boot_with_poll"] = sec3_timing

        # Cleanup
        sleep_miners(ips, "cleanup after phase 3")

    # ================================================================
    # DIAGNOSIS
    # ================================================================
    print(f"\n{'='*70}")
    print(f"  DIAGNOSIS RESULTS")
    print(f"{'='*70}")

    # Latency comparison
    if "latency_idle" in results_summary:
        print(f"\n  LATENCY (section 3 miners):")
        for key in ["latency_idle", "latency_load_poll_on", "latency_load_poll_off"]:
            if key not in results_summary:
                continue
            d = results_summary[key]
            ping_avg = statistics.mean(d["ping"]["times"]) if d["ping"]["times"] else -1
            http_avg = statistics.mean(d["http"]["times"]) if d["http"]["times"] else -1
            print(f"    {d['label']:>40}: ping={ping_avg:5.0f}ms  http={http_avg:5.0f}ms")

    # Boot comparison
    print(f"\n  BOOT TEST (section 3 with ~64 miners running):")
    print(f"    {'Test':>45} | {'Pass':>4} | {'Total':>5} | {'Rate':>5}")
    print(f"    {'-'*45}-+-{'-'*4}-+-{'-'*5}-+-{'-'*5}")

    # Reference: original cliff test
    print(f"    {'Original cliff test (sec 1+2+3)':>45} | {'17':>4} | {'35':>5} | {'49%':>5}")

    for key in ["boot_no_poll", "boot_with_poll"]:
        if key in results_summary:
            d = results_summary[key]
            rate = f"{d['rate']:.0f}%"
            print(f"    {d['label']:>45} | {d['passed']:>4} | {d['total']:>5} | {rate:>5}")

    # Verdict
    if "boot_no_poll" in results_summary and "boot_with_poll" in results_summary:
        no_poll = results_summary["boot_no_poll"]["rate"]
        with_poll = results_summary["boot_with_poll"]["rate"]
        diff = no_poll - with_poll

        print(f"\n  VERDICT:")
        if no_poll > 80 and diff > 20:
            print(f"    ⚡ SOFTWARE BOTTLENECK (fleet manager polling)")
            print(f"    Without polling: {no_poll:.0f}%  |  With polling: {with_poll:.0f}%  |  Δ = {diff:.0f}%")
            print(f"    The fleet manager's background polling (172 miners × 4 calls")
            print(f"    every 5s) saturates network or miner CPUs during boot.")
            print(f"    FIX: Reduce poll frequency, lower concurrency, or")
            print(f"    section-aware polling (only poll active section miners).")
        elif no_poll > 80 and diff <= 20:
            print(f"    ⚡ MIXED — polling contributes but not the only cause")
            print(f"    Without polling: {no_poll:.0f}%  |  With polling: {with_poll:.0f}%  |  Δ = {diff:.0f}%")
        elif no_poll <= 55:
            print(f"    ⚡ HARDWARE/NETWORK BOTTLENECK")
            print(f"    Without polling: {no_poll:.0f}%  |  With polling: {with_poll:.0f}%  |  Δ = {diff:.0f}%")
            print(f"    Even without polling, section 3 fails under load.")
            print(f"    The running miners' own traffic saturates the network.")
            print(f"    FIX: Check switch capacity, VLAN segmentation, or")
            print(f"    stagger wake commands with delays between batches.")
        else:
            print(f"    ⚡ PARTIAL IMPROVEMENT")
            print(f"    Without polling: {no_poll:.0f}%  |  With polling: {with_poll:.0f}%  |  Δ = {diff:.0f}%")
            print(f"    Both factors contribute. Fix software first, then reassess.")
    elif "boot_no_poll" in results_summary:
        no_poll = results_summary["boot_no_poll"]["rate"]
        print(f"\n  PARTIAL VERDICT (Phase 2 only):")
        if no_poll > 80:
            print(f"    Without polling: {no_poll:.0f}% — polling is likely the cause.")
        else:
            print(f"    Without polling: {no_poll:.0f}% — network saturation even without polling.")

    # Ensure Docker is running at the end
    if not docker_is_running():
        print(f"\n  Restarting Docker container ...")
        docker_start()
        enable_dev_mode()

    print(f"\n{'='*70}")
    print(f"  DIAGNOSIS COMPLETE")
    print(f"{'='*70}")


if __name__ == "__main__":
    main()
