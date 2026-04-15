#!/usr/bin/env python3
"""
Retarget test: activate at TARGET_A, wait to stabilize, retarget to TARGET_B.
Polls dashboard/api/status + dashboard/api/sections every 5s, saves CSV.

Run inside the Docker container:
  python3 /tmp/retarget_test.py 100 130 180 180
  args: target_a target_b stabilize_seconds monitor_after_seconds
"""
import csv
import json
import sys
import time
import urllib.request
from datetime import datetime

API = "http://127.0.0.1:8080"
TARGET_A = float(sys.argv[1]) if len(sys.argv) > 1 else 100
TARGET_B = float(sys.argv[2]) if len(sys.argv) > 2 else 130
STABILIZE = int(sys.argv[3]) if len(sys.argv) > 3 else 180
MONITOR = int(sys.argv[4]) if len(sys.argv) > 4 else 180

LOGFILE = f"/tmp/retarget_test_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"

FIELDS = [
    "timestamp", "elapsed_s", "phase",
    "target_kw", "meter_kw", "estimated_kw", "active_power_kw",
    "mining_miners", "sleeping_miners", "pending_wakes_total",
    "online_miners", "total_miners", "voltage",
    # Per-section breakdown
    "s1_target", "s1_mining", "s1_pending", "s1_active",
    "s2_target", "s2_mining", "s2_pending", "s2_active",
    "s3_target", "s3_mining", "s3_pending", "s3_active",
    "s4_target", "s4_mining", "s4_pending", "s4_active",
    "s5_target", "s5_mining", "s5_pending", "s5_active",
]


def api_get(path):
    try:
        req = urllib.request.Request(f"{API}{path}")
        with urllib.request.urlopen(req, timeout=3) as resp:
            return json.loads(resp.read())
    except Exception as e:
        return None


def api_post(path, data=None):
    try:
        body = json.dumps(data).encode() if data else b""
        req = urllib.request.Request(
            f"{API}{path}", data=body,
            headers={"Content-Type": "application/json"} if data else {},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read())
    except Exception as e:
        print(f"  POST {path} error: {e}")
        return None


def poll(phase, start_time, writer):
    elapsed = int(time.time() - start_time)
    ts = datetime.now().strftime("%H:%M:%S")

    status = api_get("/dashboard/api/status")
    sections_data = api_get("/dashboard/api/sections")

    if not status:
        print(f"[{ts}] {elapsed:4d}s {phase:20s} | API ERROR")
        return

    meter = status.get("measured_power_kw", "")
    target = status.get("target_power_kw", "")
    est = status.get("estimated_power_kw", "")
    active = status.get("active_power_kw", "")
    mining = status.get("mining_miners", "")
    sleeping = status.get("sleeping_miners", "")
    online = status.get("online_miners", "")
    total = status.get("total_miners", "")
    voltage = status.get("voltage", "")

    # Per-section data
    sec_vals = []
    pending_total = 0
    sections = []
    if sections_data:
        sections = sections_data.get("sections", [])

    for i in range(5):
        if i < len(sections):
            s = sections[i]
            st = s.get("target_power_kw", "")
            sm = s.get("mining_miners", 0)
            sp = s.get("pending_wakes", 0)
            sa = s.get("active_power_kw", "")
            pending_total += sp
            sec_vals.extend([st, sm, sp, sa])
        else:
            sec_vals.extend(["", "", "", ""])

    row = [
        ts, elapsed, phase,
        target, meter, est, active,
        mining, sleeping, pending_total, online, total, voltage,
    ] + sec_vals

    writer.writerow(row)

    print(
        f"[{ts}] {elapsed:4d}s {phase:20s} | "
        f"target={fmt(target):>6} meter={fmt(meter):>6} est={fmt(est):>6} | "
        f"mining={fmt(mining):>3} sleeping={fmt(sleeping):>3} pending={pending_total:>2} | "
        f"sections: " +
        " | ".join(
            f"s{i+1}:t={fmt(sections[i].get('target_power_kw',0)):>5} m={fmt(sections[i].get('mining_miners',0)):>2} p={fmt(sections[i].get('pending_wakes',0)):>1}"
            for i in range(min(len(sections), 5))
        )
    )


def fmt(v):
    """Format a value for display, handling None."""
    if v is None:
        return "?"
    return str(v)


def main():
    print(f"=== Retarget Test ===")
    print(f"Target A: {TARGET_A} kW -> Target B: {TARGET_B} kW")
    print(f"Stabilize: {STABILIZE}s, Monitor after: {MONITOR}s")
    print(f"Log: {LOGFILE}")
    print()

    start = time.time()

    with open(LOGFILE, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(FIELDS)

        # Phase 0: Baseline (15s)
        print("--- Phase 0: Baseline ---")
        for _ in range(3):
            poll("baseline", start, writer)
            f.flush()
            time.sleep(5)

        # Phase 1: Activate at TARGET_A
        print(f"\n--- Phase 1: Activate at {TARGET_A} kW ---")
        result = api_post("/api/activate", {"activationPowerInKw": TARGET_A})
        print(f"  Response: {result}")

        deadline = time.time() + STABILIZE
        while time.time() < deadline:
            poll(f"activate_{TARGET_A}", start, writer)
            f.flush()
            time.sleep(5)

        # Phase 2: Retarget to TARGET_B
        print(f"\n--- Phase 2: Retarget to {TARGET_B} kW ---")
        result = api_post("/api/activate", {"activationPowerInKw": TARGET_B})
        print(f"  Response: {result}")

        deadline = time.time() + MONITOR
        while time.time() < deadline:
            poll(f"retarget_{TARGET_B}", start, writer)
            f.flush()
            time.sleep(5)

        # Phase 3: Deactivate
        print(f"\n--- Phase 3: Deactivate ---")
        result = api_post("/api/deactivate")
        print(f"  Response: {result}")

        for _ in range(4):
            poll("deactivate", start, writer)
            f.flush()
            time.sleep(5)

    print(f"\n=== Test complete. Data saved to {LOGFILE} ===")


if __name__ == "__main__":
    main()
