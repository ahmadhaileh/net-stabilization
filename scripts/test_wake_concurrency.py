#!/usr/bin/env python3
"""Wake concurrency benchmark — measures how fast the fleet reaches target power.

Usage:
    python3 scripts/test_wake_concurrency.py [--small-only] [--full-only] [--target TARGET_KW]
"""

import argparse
import json
import sys
import time
import urllib.request

DASHBOARD_API = "http://127.0.0.1:8080/dashboard/api"
EMS_API = "http://127.0.0.1:8080/api"
METER_URL = "http://192.168.95.4:8044"
POLL_INTERVAL = 10   # seconds between status polls
SMALL_TARGET = 25    # kW for quick sanity check
FULL_TARGET = 194    # kW for full fleet
CONVERGE_THRESHOLD = 0.95  # 95% of target = converged
MAX_WAIT = 360       # seconds before declaring failure
IDLE_WAIT = 120      # seconds to wait for fleet to idle after deactivate


def api_get(base: str, path: str) -> dict:
    url = f"{base}/{path}"
    req = urllib.request.Request(url)
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read())


def api_post(base: str, path: str, data: dict | None = None) -> dict:
    url = f"{base}/{path}"
    body = json.dumps(data).encode() if data else None
    req = urllib.request.Request(url, data=body, method="POST")
    if body:
        req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read())


def get_meter_kw() -> float | None:
    try:
        req = urllib.request.Request(METER_URL)
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read())
            return data.get("active_power", 0) / 1000.0
    except Exception:
        return None


def wait_for_idle(timeout: int = IDLE_WAIT) -> bool:
    """Wait until mining_miners == 0 and meter < 10 kW."""
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            st = api_get(DASHBOARD_API, "status")
            meter = st.get("measured_power_kw") or 0
            mining = st.get("mining_miners", 0)
            elapsed = int(time.time() - t0)
            print(f"  idle wait: {meter:.1f} kW, {mining} mining  (T+{elapsed}s)")
            if mining == 0 and meter < 10:
                return True
        except Exception as e:
            print(f"  idle wait error: {e}")
        time.sleep(10)
    return False


def run_benchmark(target_kw: float, label: str) -> dict:
    """Activate to target_kw and monitor convergence."""
    print(f"\n{'='*60}")
    print(f"  {label}: target = {target_kw} kW")
    print(f"{'='*60}")

    # Activate
    t0 = time.time()
    resp = api_post(EMS_API, "activate", {"activation_power_in_kw": target_kw})
    t_activate = time.time() - t0
    print(f"  Activate response ({t_activate:.1f}s): {resp}")

    converge_time = None
    results = []

    # Poll until converged or timeout
    while True:
        elapsed = time.time() - t0
        if elapsed > MAX_WAIT:
            break

        time.sleep(POLL_INTERVAL)
        elapsed = time.time() - t0

        try:
            st = api_get(DASHBOARD_API, "status")
            meter_kw = st.get("measured_power_kw") or 0
            est_kw = st.get("estimated_power_kw") or 0
            mining = st.get("mining_miners", 0)
            total = st.get("total_miners", 0)
            state = st.get("state", "?")

            row = {
                "t": round(elapsed, 1),
                "meter_kw": round(meter_kw, 1),
                "est_kw": round(est_kw, 1),
                "mining": mining,
                "total": total,
                "state": state,
            }
            results.append(row)

            print(f"  T+{elapsed:5.0f}s | meter={meter_kw:6.1f} kW | est={est_kw:6.1f} kW | "
                  f"mining={mining:3d}/{total} | {state}")

            # Check convergence on meter reading
            if meter_kw >= target_kw * CONVERGE_THRESHOLD and converge_time is None:
                converge_time = round(elapsed, 1)
                print(f"  >>> CONVERGED at T+{converge_time}s (meter >= {target_kw * CONVERGE_THRESHOLD:.0f} kW)")

        except Exception as e:
            print(f"  T+{elapsed:5.0f}s | ERROR: {e}")

    # Summary
    final_meter = results[-1]["meter_kw"] if results else 0
    final_mining = results[-1]["mining"] if results else 0
    passed = converge_time is not None

    print(f"\n  Result: {'PASS' if passed else 'FAIL'}")
    print(f"  Converge time: {converge_time}s" if converge_time else "  Converge time: DID NOT CONVERGE")
    print(f"  Final: {final_meter:.1f} kW, {final_mining} mining")
    print(f"  Activate latency: {t_activate:.1f}s")

    return {
        "label": label,
        "target_kw": target_kw,
        "passed": passed,
        "converge_time": converge_time,
        "final_meter_kw": final_meter,
        "final_mining": final_mining,
        "activate_latency": t_activate,
        "results": results,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--small-only", action="store_true")
    parser.add_argument("--full-only", action="store_true")
    parser.add_argument("--target", type=float, default=FULL_TARGET)
    args = parser.parse_args()

    print("Wake Concurrency Benchmark")
    print(f"EMS API: {EMS_API}")
    print(f"Dashboard API: {DASHBOARD_API}")

    # Pre-check: API reachable
    try:
        st = api_get(DASHBOARD_API, "status")
        print(f"Fleet: {st['total_miners']} miners, state={st['state']}, "
              f"meter={st.get('measured_power_kw', 0):.1f} kW")
    except Exception as e:
        print(f"FATAL: Cannot reach API: {e}")
        sys.exit(1)

    all_results = []

    # Phase 1: Small test
    if not args.full_only:
        print(f"\n--- Phase 1: Small test ({SMALL_TARGET} kW) ---")
        r = run_benchmark(SMALL_TARGET, "SMALL")
        all_results.append(r)

        # Deactivate and wait for idle
        print("\n--- Phase 2: Deactivate and wait for idle ---")
        api_post(EMS_API, "deactivate")
        if not wait_for_idle():
            print("WARNING: Fleet did not fully idle, continuing anyway")

    # Phase 2: Full test
    if not args.small_only:
        print(f"\n--- Phase 3: Full test ({args.target} kW) ---")
        r = run_benchmark(args.target, "FULL")
        all_results.append(r)

        # Deactivate
        print("\n--- Phase 4: Deactivate ---")
        api_post(EMS_API, "deactivate")

    # Final summary
    print(f"\n{'='*60}")
    print("  SUMMARY")
    print(f"{'='*60}")
    for r in all_results:
        status = "PASS" if r["passed"] else "FAIL"
        ct = f"{r['converge_time']}s" if r["converge_time"] else "N/A"
        print(f"  {r['label']:6s} | {status} | target={r['target_kw']}kW | "
              f"converge={ct} | final={r['final_meter_kw']}kW, {r['final_mining']} mining")


if __name__ == "__main__":
    main()
