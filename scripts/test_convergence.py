#!/usr/bin/env python3
"""
Grid Stabilization - Power Convergence Test
Sends an /api/activate command and monitors power convergence.
"""
import sys
import time
import json
import urllib.request
from datetime import datetime

BASE_URL = "http://100.125.153.88:8080"
MARGIN_PERCENT = 5.0  # ±5% target margin
POLL_INTERVAL = 5     # seconds between polls
MAX_WAIT = 360        # 6 minutes max wait

def api_get(path):
    req = urllib.request.Request(f"{BASE_URL}{path}")
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read())

def api_post(path, data=None):
    body = json.dumps(data).encode() if data else None
    req = urllib.request.Request(
        f"{BASE_URL}{path}",
        data=body,
        headers={"Content-Type": "application/json"} if body else {},
        method="POST"
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read())

def get_status():
    return api_get("/dashboard/api/status")

def within_margin(actual, target, margin_pct):
    if target == 0:
        return actual < 1.0
    return abs(actual - target) / target * 100 <= margin_pct

def run_test(target_kw, test_num):
    print(f"\n{'='*70}")
    print(f"  TEST {test_num}: Activate at {target_kw} kW (±{MARGIN_PERCENT}% = {target_kw*(1-MARGIN_PERCENT/100):.1f} - {target_kw*(1+MARGIN_PERCENT/100):.1f} kW)")
    print(f"{'='*70}")
    
    # Get pre-test status
    status = get_status()
    print(f"  Pre-test: state={status['state']}, active={status['active_power_kw']} kW, mining={status['mining_miners']}/{status['total_miners']}")
    
    # Send activation
    print(f"\n  [{datetime.now().strftime('%H:%M:%S')}] Sending /api/activate with {target_kw} kW...")
    result = api_post("/api/activate", {"activationPowerInKw": target_kw})
    print(f"  Response: accepted={result.get('accepted')}, message={result.get('message')}")
    
    if not result.get("accepted"):
        print(f"  FAILED: Activation not accepted")
        return None
    
    start_time = time.time()
    converged_time = None
    first_in_margin_time = None
    peak_power = 0
    
    print(f"\n  {'Time':>6}  {'Elapsed':>8}  {'Meter kW':>9}  {'Est kW':>8}  {'Mining':>7}  {'Dev%':>6}  Status")
    print(f"  {'-'*6}  {'-'*8}  {'-'*9}  {'-'*8}  {'-'*7}  {'-'*6}  {'-'*10}")
    
    while True:
        elapsed = time.time() - start_time
        
        if elapsed > MAX_WAIT:
            print(f"\n  TIMEOUT after {MAX_WAIT}s — target not reached within margin")
            break
        
        try:
            status = get_status()
            actual = status.get("measured_power_kw") or status.get("active_power_kw", 0)
            estimated = status.get("estimated_power_kw", 0)
            mining = status["mining_miners"]
            total = status["total_miners"]
            state = status["state"]
            
            if actual > peak_power:
                peak_power = actual
            
            if target_kw > 0:
                dev_pct = (actual - target_kw) / target_kw * 100
            else:
                dev_pct = 0
            
            in_margin = within_margin(actual, target_kw, MARGIN_PERCENT)
            marker = " ✓ IN MARGIN" if in_margin else ""
            
            print(f"  {datetime.now().strftime('%H:%M:%S'):>6}  {elapsed:>7.0f}s  {actual:>8.2f}  {estimated:>7.2f}  {mining:>3}/{total:<3}  {dev_pct:>+5.1f}%  {state}{marker}")
            
            if in_margin and first_in_margin_time is None:
                first_in_margin_time = elapsed
            
            # Consider converged if in margin for 2 consecutive readings
            if in_margin and converged_time is None:
                if first_in_margin_time is not None and (elapsed - first_in_margin_time) >= POLL_INTERVAL:
                    converged_time = first_in_margin_time
                    print(f"\n  ✅ CONVERGED at {converged_time:.0f}s (first in margin)")
                    # Keep monitoring for 30 more seconds to see stability
                    end_watch = elapsed + 30
                    while time.time() - start_time < end_watch:
                        time.sleep(POLL_INTERVAL)
                        elapsed2 = time.time() - start_time
                        status = get_status()
                        actual2 = status.get("measured_power_kw") or status.get("active_power_kw", 0)
                        mining2 = status["mining_miners"]
                        dev2 = (actual2 - target_kw) / target_kw * 100 if target_kw > 0 else 0
                        in2 = within_margin(actual2, target_kw, MARGIN_PERCENT)
                        print(f"  {datetime.now().strftime('%H:%M:%S'):>6}  {elapsed2:>7.0f}s  {actual2:>8.2f}  {'':>8}  {mining2:>3}/{total:<3}  {dev2:>+5.1f}%  {'✓' if in2 else '✗ DRIFTED'}")
                    break
            
            # Reset first_in_margin if we drift out
            if not in_margin:
                first_in_margin_time = None
                
        except Exception as e:
            print(f"  {datetime.now().strftime('%H:%M:%S'):>6}  {elapsed:>7.0f}s  ERROR: {e}")
        
        time.sleep(POLL_INTERVAL)
    
    print(f"\n  Summary:")
    print(f"    Target:          {target_kw} kW")
    print(f"    Margin:          ±{MARGIN_PERCENT}% ({target_kw*(1-MARGIN_PERCENT/100):.1f} - {target_kw*(1+MARGIN_PERCENT/100):.1f} kW)")
    print(f"    Peak power:      {peak_power:.2f} kW")
    if converged_time is not None:
        print(f"    Converged in:    {converged_time:.0f}s ({converged_time/60:.1f} min) ⬅️")
    else:
        print(f"    Converged in:    DID NOT CONVERGE")
    print(f"    First in margin: {first_in_margin_time:.0f}s" if first_in_margin_time else "    First in margin: NEVER")
    
    return converged_time

def deactivate_and_wait():
    """Deactivate fleet and wait for power to drop."""
    print(f"\n  [{datetime.now().strftime('%H:%M:%S')}] Deactivating fleet...")
    result = api_post("/api/deactivate")
    print(f"  Response: {result}")
    
    print(f"  Waiting for fleet to idle (< 5 kW)...")
    start = time.time()
    while time.time() - start < 300:  # Max 5 min wait
        time.sleep(10)
        status = get_status()
        actual = status.get("measured_power_kw") or status.get("active_power_kw", 0)
        mining = status["mining_miners"]
        elapsed = time.time() - start
        print(f"  ... {elapsed:.0f}s: {actual:.2f} kW, {mining} mining")
        if actual < 5.0 and mining == 0:
            print(f"  Fleet idle after {elapsed:.0f}s")
            return True
    print(f"  WARNING: Fleet didn't fully idle in 5 min")
    return False

def main():
    targets = [60, 90, 121]
    results = {}
    
    print(f"\n{'#'*70}")
    print(f"  GRID STABILIZATION - POWER CONVERGENCE TEST SERIES")
    print(f"  Testing targets: {targets} kW")
    print(f"  Margin: ±{MARGIN_PERCENT}%")
    print(f"  Started: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'#'*70}")
    
    for i, target in enumerate(targets, 1):
        conv_time = run_test(target, i)
        results[target] = conv_time
        
        if i < len(targets):
            deactivate_and_wait()
            # Extra cooldown
            print(f"  Extra 30s cooldown before next test...")
            time.sleep(30)
    
    # Deactivate after all tests
    deactivate_and_wait()
    
    # Final report
    print(f"\n{'='*70}")
    print(f"  FINAL RESULTS")
    print(f"{'='*70}")
    print(f"  {'Target kW':>12}  {'Margin':>15}  {'Convergence':>15}  {'Pass?':>6}")
    print(f"  {'-'*12}  {'-'*15}  {'-'*15}  {'-'*6}")
    for target, conv in results.items():
        margin_str = f"±{MARGIN_PERCENT}% ({target*(1-MARGIN_PERCENT/100):.0f}-{target*(1+MARGIN_PERCENT/100):.0f} kW)"
        if conv is not None:
            conv_str = f"{conv:.0f}s ({conv/60:.1f} min)"
            passed = "✅" if conv <= 240 else "❌"  # Pass if under 4 min
        else:
            conv_str = "DID NOT CONVERGE"
            passed = "❌"
        print(f"  {target:>10} kW  {margin_str:>15}  {conv_str:>15}  {passed:>6}")
    
    print(f"\n  Threshold: ≤ 240s (4 min)")
    all_pass = all(v is not None and v <= 240 for v in results.values())
    print(f"  Overall: {'✅ ALL PASSED' if all_pass else '❌ SOME FAILED'}")
    print(f"{'='*70}\n")

if __name__ == "__main__":
    main()
