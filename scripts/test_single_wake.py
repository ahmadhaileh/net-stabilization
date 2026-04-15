#!/usr/bin/env python3
"""Wake a single miner and monitor its boot sequence to see pool behavior."""
import httpx
import time
import json

ip = "192.168.95.27"  # clean miner
auth = httpx.DigestAuth("root", "root")

# Check current state
print(f"Testing miner {ip}")
print("=" * 50)
r = httpx.get(f"http://{ip}/cgi-bin/get_miner_status.cgi", auth=auth, timeout=5.0)
print(f"BEFORE STATUS ({r.status_code}):")
try:
    d = r.json()
    s = d.get("summary", {})
    print(f"  GHS5s: {s.get('ghs5s', '?')}, Elapsed: {s.get('elapsed', '?')}")
    pools = d.get("pools", [])
    if pools and isinstance(pools[0], dict):
        p = pools[0]
        print(f"  Pool: {p.get('url', '?')}, Status: {p.get('status', '?')}")
except Exception:
    print(f"  Raw: {r.text[:200]}")

# Wake it
print("\nWaking miner...")
r = httpx.post(f"http://{ip}/cgi-bin/do_sleep_mode.cgi", data={"mode": "0"}, auth=auth, timeout=5.0)
print(f"Wake response: {r.status_code} {r.text[:100]}")

# Poll every 15s for 2.5 minutes
print("\nMonitoring boot sequence:")
for i in range(10):
    time.sleep(15)
    try:
        r = httpx.get(f"http://{ip}/cgi-bin/get_miner_status.cgi", auth=auth, timeout=5.0)
        try:
            d = r.json()
            s = d.get("summary", {})
            ghs = s.get("ghs5s", "0")
            elapsed = s.get("elapsed", 0)
            accepted = s.get("accepted", 0)
            pools = d.get("pools", [])
            if pools and isinstance(pools[0], dict):
                pstatus = pools[0].get("status", "?")
                paccepted = pools[0].get("accepted", 0)
            else:
                pstatus = "?"
                paccepted = "?"
            print(f"  +{(i+1)*15:>3}s: GHS5s={ghs}, Elapsed={elapsed}, Pool={pstatus}, Shares={paccepted}")
        except Exception:
            print(f"  +{(i+1)*15:>3}s: Non-JSON ({r.status_code}): {r.text[:80]}")
    except Exception as e:
        print(f"  +{(i+1)*15:>3}s: Error: {e}")

# Also check autofreq log at end
print("\nAutofreq log (last 10 entries):")
try:
    r = httpx.get(f"http://{ip}/cgi-bin/get_autofreq_log.cgi", auth=auth, timeout=5.0)
    lines = r.text.strip().split("\n")
    for l in lines[:10]:
        print(f"  {l.strip()}")
except Exception as e:
    print(f"  Error: {e}")

# Put it back to sleep
print("\nPutting miner back to sleep...")
r = httpx.post(f"http://{ip}/cgi-bin/do_sleep_mode.cgi", data={"mode": "1"}, auth=auth, timeout=5.0)
print(f"Sleep response: {r.status_code}")
