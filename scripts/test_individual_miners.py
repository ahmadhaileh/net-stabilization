#!/usr/bin/env python3
"""Section-based miner wake diagnostic.

Splits all miners into ~50 kW sections (~36 miners each at 1.4 kW).
Each section is tested once — wake all miners in the section, verify
each one reaches mining state, record pass/fail.  No miner is tested
twice.  Leaves all miners running at the end for visual confirmation.

Bypasses fleet manager entirely — talks directly to Vnish sleep API
and checks CGMiner port 4028.

Usage (run on the server):
    python3 /tmp/test_individual_miners.py
    python3 /tmp/test_individual_miners.py --section-kw 25   # smaller sections
    python3 /tmp/test_individual_miners.py --miners 192.168.95.100,192.168.95.101
"""

import argparse
import json
import socket
import subprocess
import sys
import time
import urllib.request

# ── Config ──────────────────────────────────────────────────────────
DASHBOARD_API = "http://127.0.0.1:8080/dashboard/api"
METER_URL = "http://192.168.95.4:8044"
VNISH_USER = "root"
VNISH_PASS = "root"
KW_PER_MINER = 1.4
SECTION_KW = 50            # kW per section → ~36 miners
BOOT_TIMEOUT = 150         # seconds to wait for an entire section to boot
CGMINER_PORT = 4028
POLL_INTERVAL = 5          # seconds between detection checks
SETTLE_AFTER_SLEEP = 10    # seconds to let miners settle after sleep command


# ── Helpers ─────────────────────────────────────────────────────────
def ip_sort_key(ip: str):
    """Sort key for IP addresses (numeric)."""
    return tuple(int(x) for x in ip.split("."))


def get_all_miner_ips() -> list[str]:
    """Fetch miner list from dashboard API, sorted ascending by IP."""
    url = f"{DASHBOARD_API}/miners"
    req = urllib.request.Request(url)
    with urllib.request.urlopen(req, timeout=10) as resp:
        miners = json.loads(resp.read())
    return sorted(set(m["miner_id"].replace("_", ".") for m in miners), key=ip_sort_key)


def get_meter_kw() -> float:
    try:
        req = urllib.request.Request(METER_URL)
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read())
            return data.get("active_power", 0) / 1000.0
    except Exception:
        return -1.0


def vnish_sleep(ip: str, wake: bool) -> bool:
    """Send wake (mode=0) or sleep (mode=1) via curl + digest auth."""
    mode = "0" if wake else "1"
    url = f"http://{ip}/cgi-bin/do_sleep_mode.cgi"
    try:
        result = subprocess.run(
            [
                "curl", "-s", "--digest",
                "-u", f"{VNISH_USER}:{VNISH_PASS}",
                "-d", f"mode={mode}",
                "-H", "Content-Type: application/x-www-form-urlencoded",
                "--connect-timeout", "5",
                "--max-time", "10",
                url,
            ],
            capture_output=True, text=True, timeout=15,
        )
        return result.returncode == 0
    except Exception as e:
        print(f"    curl error for {ip}: {e}")
        return False


def cgminer_is_mining(ip: str) -> bool:
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


def sleep_miners(ips: list[str], label: str = ""):
    """Put miners to sleep."""
    tag = f" ({label})" if label else ""
    print(f"  Sleeping {len(ips)} miners{tag} ...", end="", flush=True)
    for ip in ips:
        vnish_sleep(ip, wake=False)
    print(" done.  Settling ...", end="", flush=True)
    time.sleep(SETTLE_AFTER_SLEEP)
    print(" ok")


def split_into_sections(ips: list[str], section_kw: float) -> list[list[str]]:
    """Split miner IPs into sections of ~section_kw each."""
    miners_per_section = max(1, int(section_kw / KW_PER_MINER))
    sections = []
    for i in range(0, len(ips), miners_per_section):
        sections.append(ips[i:i + miners_per_section])
    return sections


def api_post_json(path: str, data: dict):
    """POST JSON to dashboard API."""
    url = f"{DASHBOARD_API}{path}"
    body = json.dumps(data).encode()
    req = urllib.request.Request(url, data=body, method="POST",
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read())
    except Exception:
        pass  # non-critical — dashboard display only


def build_dashboard_sections(sections: list[list[str]]) -> list[dict]:
    """Build section state dicts for the dashboard API."""
    return [
        {
            "id": i + 1,
            "miners": sec,
            "status": "waiting",
            "mining_count": 0,
            "failed": [],
            "started_at": None,
            "finished_at": None,
        }
        for i, sec in enumerate(sections)
    ]


def push_dashboard_state(dash_sections: list[dict], running: bool = True, current: int = None):
    """Push section test state to dashboard."""
    api_post_json("/section_test", {
        "running": running,
        "sections": dash_sections,
        "current_section": current,
        "started_at": time.strftime("%H:%M:%S"),
    })


# ── Section test ────────────────────────────────────────────────────
def test_section(section_ips: list[str], section_num: int, total_sections: int,
                 already_running: list[str],
                 dash_sections: list[dict] = None) -> dict[str, bool]:
    """Wake all miners in a section, verify each one mines.

    Returns dict of {ip: True/False}.
    """
    n = len(section_ips)
    expected_kw = n * KW_PER_MINER
    print(f"\n  ╔══ Section {section_num}/{total_sections}: "
          f"{n} miners, ~{expected_kw:.0f} kW expected ══╗")

    # Update dashboard
    if dash_sections:
        dash_sections[section_num - 1]["status"] = "testing"
        dash_sections[section_num - 1]["started_at"] = time.strftime("%H:%M:%S")
        push_dashboard_state(dash_sections, running=True, current=section_num)

    meter_before = get_meter_kw()
    running_before = len(already_running)
    print(f"  Meter before: {meter_before:.1f} kW  "
          f"(already running from prev sections: {running_before})")

    # Send wake commands to all miners in this section
    print(f"  Waking {n} miners ...", end="", flush=True)
    t_start = time.time()
    wake_ok = 0
    wake_fail = 0
    for ip in section_ips:
        if vnish_sleep(ip, wake=True):
            wake_ok += 1
        else:
            wake_fail += 1
    wake_dur = time.time() - t_start
    print(f" {wake_ok} sent, {wake_fail} failed in {wake_dur:.1f}s")

    # Poll until all miners are mining or timeout
    results = {ip: False for ip in section_ips}
    t0 = time.time()
    prev_count = 0

    while time.time() - t0 < BOOT_TIMEOUT:
        time.sleep(POLL_INTERVAL)
        elapsed = time.time() - t0

        for ip in section_ips:
            if not results[ip] and cgminer_is_mining(ip):
                results[ip] = True

        mining_now = sum(1 for v in results.values() if v)
        meter_kw = get_meter_kw()

        # Only print when something changes or every 15s
        if mining_now != prev_count or int(elapsed) % 15 < POLL_INTERVAL:
            print(f"    T+{elapsed:5.0f}s | section mining: {mining_now:3d}/{n} | "
                  f"meter: {meter_kw:6.1f} kW")
            prev_count = mining_now

        # Update dashboard live
        if dash_sections:
            dash_sections[section_num - 1]["mining_count"] = mining_now
            push_dashboard_state(dash_sections, running=True, current=section_num)

        # All done?
        if mining_now >= n:
            print(f"    ✓ 100% of section {section_num} mining at T+{elapsed:.0f}s")
            break
    else:
        # Timeout — report stragglers
        mining_now = sum(1 for v in results.values() if v)
        failed_ips = [ip for ip, ok in results.items() if not ok]
        print(f"    ⏰ Timeout after {BOOT_TIMEOUT}s: "
              f"{mining_now}/{n} mining, {len(failed_ips)} failed")
        for ip in failed_ips:
            print(f"       ✗ {ip}")

    # Summary for this section
    passed = sum(1 for v in results.values() if v)
    failed_list = [ip for ip, ok in results.items() if not ok]
    total_running = running_before + passed
    meter_after = get_meter_kw()
    print(f"  Section {section_num} result: {passed}/{n} "
          f"({passed/n*100:.0f}%) | total fleet running: {total_running} | "
          f"meter: {meter_after:.1f} kW")
    print(f"  ╚{'═'*56}╝")

    # Update dashboard with final result
    if dash_sections:
        ds = dash_sections[section_num - 1]
        ds["status"] = "done"
        ds["mining_count"] = passed
        ds["failed"] = failed_list
        ds["finished_at"] = time.strftime("%H:%M:%S")
        push_dashboard_state(dash_sections, running=True, current=section_num)

    return results


# ── Main ────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="Section-based miner wake diagnostic")
    parser.add_argument("--section-kw", type=float, default=SECTION_KW,
                        help=f"kW per section (default: {SECTION_KW})")
    parser.add_argument("--miners", type=str, default=None,
                        help="Comma-separated IPs to test (default: all)")
    parser.add_argument("--section", type=int, default=None,
                        help="Test only this section number (1-based). Sleeps all others first.")
    parser.add_argument("--sections", type=str, default=None,
                        help="Test multiple sections together (comma-sep, e.g. '1,2,3'). "
                             "Wakes all at once to find the capacity cliff.")
    parser.add_argument("--keep-running", action="store_true", default=True,
                        help="Leave all miners running at the end (default: true)")
    parser.add_argument("--sleep-after", action="store_true", default=False,
                        help="Sleep all miners after test completes")
    args = parser.parse_args()

    # Enable dev mode to prevent fleet manager / EMS interference
    try:
        req = urllib.request.Request(
            f"{DASHBOARD_API}/dev_mode?enabled=true",
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            print(f"Dev mode: {json.loads(resp.read())}")
    except Exception as e:
        print(f"WARNING: Could not enable dev mode: {e}")
        print("EMS idle enforcement may interfere with the test!")

    # Get miner list
    if args.miners:
        ips = sorted(args.miners.split(","), key=ip_sort_key)
    else:
        ips = get_all_miner_ips()
    print(f"Total miners: {len(ips)}")

    # Split into sections
    sections = split_into_sections(ips, args.section_kw)
    miners_per = len(sections[0]) if sections else 0
    print(f"Sections: {len(sections)} × ~{miners_per} miners "
          f"(~{args.section_kw:.0f} kW each)")
    # Parse --sections into a set for marker display
    _selected_sections = set()
    if args.sections:
        _selected_sections = {int(x) for x in args.sections.split(",")}
    
    for i, sec in enumerate(sections):
        marker = ""
        if args.section == i + 1:
            marker = " ◀"
        elif (i + 1) in _selected_sections:
            marker = " ◀"
        print(f"  Section {i+1}: {sec[0]} – {sec[-1]} ({len(sec)} miners){marker}")

    # If --section N, test only that one section in isolation
    if args.section is not None:
        idx = args.section - 1
        if idx < 0 or idx >= len(sections):
            print(f"ERROR: Section {args.section} does not exist (1-{len(sections)})")
            sys.exit(1)
        target_section = sections[idx]
        print(f"\n{'='*60}")
        print(f"  SINGLE SECTION TEST: Section {args.section} ({len(target_section)} miners)")
        print(f"  Range: {target_section[0]} – {target_section[-1]}")
        print(f"{'='*60}")

        # Sleep ALL miners for a perfectly clean test
        sleep_miners(ips, "all")
        time.sleep(5)
        print(f"  Baseline meter: {get_meter_kw():.1f} kW\n")

        # Build dashboard state for just this one section
        dash_sections = build_dashboard_sections([target_section])
        push_dashboard_state(dash_sections, running=True, current=None)

        results = test_section(target_section, 1, 1, [], dash_sections=dash_sections)
        push_dashboard_state(dash_sections, running=False, current=None)

        passed = sum(1 for v in results.values() if v)
        failed_ips = sorted((ip for ip, ok in results.items() if not ok), key=ip_sort_key)
        total = len(results)

        print(f"\n{'='*60}")
        print(f"  SECTION {args.section} RESULT: {passed}/{total} ({passed/total*100:.0f}%)")
        print(f"{'='*60}")
        if failed_ips:
            print(f"  Failed:")
            for ip in failed_ips:
                print(f"    ✗ {ip}")
        else:
            print(f"  All miners passed ✓")

        if args.sleep_after:
            sleep_miners(target_section, "cleanup")
        else:
            print(f"\nSection {args.section} miners LEFT RUNNING")
        return

    # If --sections 1,2,3, test those sections combined (all at once)
    if args.sections is not None:
        sec_nums = sorted(int(x) for x in args.sections.split(","))
        for n in sec_nums:
            if n < 1 or n > len(sections):
                print(f"ERROR: Section {n} does not exist (1-{len(sections)})")
                sys.exit(1)
        
        combined_ips = []
        for n in sec_nums:
            combined_ips.extend(sections[n - 1])
        total_miners = len(combined_ips)
        expected_kw = total_miners * KW_PER_MINER
        
        label = ",".join(str(n) for n in sec_nums)
        print(f"\n{'='*60}")
        print(f"  COMBINED SECTION TEST: Sections [{label}]")
        print(f"  {total_miners} miners, ~{expected_kw:.0f} kW expected")
        print(f"  Range: {combined_ips[0]} – {combined_ips[-1]}")
        print(f"{'='*60}")

        # Sleep ALL miners for a clean test
        sleep_miners(ips, "all")
        time.sleep(5)
        print(f"  Baseline meter: {get_meter_kw():.1f} kW\n")

        # Build dashboard state — one entry per selected section
        selected_sections = [sections[n - 1] for n in sec_nums]
        dash_sections = build_dashboard_sections(selected_sections)
        push_dashboard_state(dash_sections, running=True, current=None)

        # Test each section sequentially (wake section, wait, then next)
        all_results = {}
        already_running = []
        for idx, n in enumerate(sec_nums):
            sec_ips = sections[n - 1]
            results = test_section(sec_ips, idx + 1, len(sec_nums), already_running,
                                   dash_sections=dash_sections)
            all_results.update(results)
            newly_running = [ip for ip, ok in results.items() if ok]
            already_running.extend(newly_running)
            if idx < len(sec_nums) - 1:
                print(f"\n  Pausing 15s before next section ...")
                time.sleep(15)

        push_dashboard_state(dash_sections, running=False, current=None)

        # Summary
        passed = sum(1 for v in all_results.values() if v)
        failed = total_miners - passed
        failed_ips = sorted((ip for ip, ok in all_results.items() if not ok), key=ip_sort_key)

        print(f"\n{'='*60}")
        print(f"  COMBINED RESULT: Sections [{label}]")
        print(f"  {passed}/{total_miners} ({passed/total_miners*100:.0f}%)")
        print(f"{'='*60}")
        
        # Per-section breakdown
        print(f"  {'Sec':>4} | {'Range':>33} | {'Size':>5} | {'Pass':>5} | {'Rate':>6}")
        print(f"  {'-'*4}-+-{'-'*33}-+-{'-'*5}-+-{'-'*5}-+-{'-'*6}")
        for n in sec_nums:
            sec_ips = sections[n - 1]
            sec_pass = sum(1 for ip in sec_ips if all_results.get(ip, False))
            sec_n = len(sec_ips)
            rate = f"{sec_pass/sec_n*100:.0f}%"
            rng = f"{sec_ips[0]} – {sec_ips[-1]}"
            print(f"  {n:>4} | {rng:>33} | {sec_n:>5} | {sec_pass:>5} | {rate:>6}")

        if failed_ips:
            print(f"\n  Failed miners ({len(failed_ips)}):")
            for ip in failed_ips:
                print(f"    ✗ {ip}")
        else:
            print(f"\n  All miners passed ✓")

        if args.sleep_after:
            sleep_miners(combined_ips, "cleanup")
        else:
            print(f"\nAll tested miners LEFT RUNNING")
        return

    # Build dashboard section state
    dash_sections = build_dashboard_sections(sections)
    push_dashboard_state(dash_sections, running=True, current=None)

    # Sleep ALL miners first for a clean baseline
    print(f"\n{'='*60}")
    print(f"  CLEAN START: sleeping all {len(ips)} miners")
    print(f"{'='*60}")
    sleep_miners(ips, "all")
    time.sleep(5)
    print(f"  Baseline meter: {get_meter_kw():.1f} kW\n")

    # Test each section — miners from previous sections stay running
    all_results: dict[str, bool] = {}
    already_running: list[str] = []

    for i, section in enumerate(sections):
        results = test_section(section, i + 1, len(sections), already_running,
                               dash_sections=dash_sections)
        all_results.update(results)

        # Track which miners are now running (don't sleep them!)
        newly_running = [ip for ip, ok in results.items() if ok]
        already_running.extend(newly_running)

        # Small pause between sections to let power stabilize
        if i < len(sections) - 1:
            print(f"\n  Pausing 15s before next section ...")
            time.sleep(15)

    # Mark test complete on dashboard
    push_dashboard_state(dash_sections, running=False, current=None)

    # ── Final summary ───────────────────────────────────────────────
    total = len(all_results)
    passed = sum(1 for v in all_results.values() if v)
    failed = total - passed
    failed_ips = sorted(ip for ip, ok in all_results.items() if not ok)

    print(f"\n{'='*60}")
    print(f"  FINAL RESULTS")
    print(f"{'='*60}")
    print(f"  Total miners tested : {total}")
    print(f"  Passed (mining)     : {passed}  ({passed/total*100:.1f}%)")
    print(f"  Failed              : {failed}  ({failed/total*100:.1f}%)")
    print(f"  Final meter reading : {get_meter_kw():.1f} kW")
    print(f"  Expected (all up)   : {total * KW_PER_MINER:.1f} kW")

    if failed_ips:
        print(f"\n  Failed miners:")
        for ip in failed_ips:
            print(f"    ✗ {ip}")

    # Per-section breakdown
    print(f"\n  Section breakdown:")
    print(f"  {'Sec':>4} | {'Range':>33} | {'Size':>5} | {'Pass':>5} | {'Rate':>6}")
    print(f"  {'-'*4}-+-{'-'*33}-+-{'-'*5}-+-{'-'*5}-+-{'-'*6}")
    for i, section in enumerate(sections):
        sec_pass = sum(1 for ip in section if all_results.get(ip, False))
        sec_n = len(section)
        rate = f"{sec_pass/sec_n*100:.0f}%"
        rng = f"{section[0]} – {section[-1]}"
        print(f"  {i+1:>4} | {rng:>33} | {sec_n:>5} | {sec_pass:>5} | {rate:>6}")

    print(f"{'='*60}")

    # Retry failed miners once more
    if failed_ips:
        print(f"\n  Retrying {len(failed_ips)} failed miners one more time ...")
        retry_results = {}
        for ip in failed_ips:
            vnish_sleep(ip, wake=True)
        time.sleep(BOOT_TIMEOUT)
        for ip in failed_ips:
            retry_results[ip] = cgminer_is_mining(ip)
        retry_passed = sum(1 for v in retry_results.values() if v)
        print(f"  Retry: {retry_passed}/{len(failed_ips)} now mining")
        still_failed = [ip for ip, ok in retry_results.items() if not ok]
        if still_failed:
            print(f"  Still failing after retry:")
            for ip in still_failed:
                print(f"    ✗ {ip}")
        else:
            print(f"  All failed miners recovered on retry ✓")

    # Cleanup
    if args.sleep_after:
        print("\nSleeping all miners (cleanup) ...")
        sleep_miners(ips, "cleanup")
    else:
        print(f"\nAll miners LEFT RUNNING (use --sleep-after to sleep them)")

    # Disable dev mode
    try:
        req = urllib.request.Request(
            f"{DASHBOARD_API}/dev_mode?enabled=false",
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            print(f"Dev mode: {json.loads(resp.read())}")
    except Exception:
        pass


if __name__ == "__main__":
    main()
