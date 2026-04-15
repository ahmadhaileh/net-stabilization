#!/usr/bin/env python3
"""Check detailed autofreq logs and pool config from specific miners."""
import httpx
import json

VNISH_USER = "root"
VNISH_PASS = "root"

# Sample: sleeping, worst pool-fail, clean, fan-error
miners = ["192.168.95.38", "192.168.95.74", "192.168.95.27", "192.168.95.42"]

for ip in miners:
    print("\n" + "=" * 60)
    print(f"MINER {ip}")
    print("=" * 60)
    auth = httpx.DigestAuth(VNISH_USER, VNISH_PASS)

    # Autofreq log (last 40 lines)
    try:
        r = httpx.get(f"http://{ip}/cgi-bin/get_autofreq_log.cgi", auth=auth, timeout=5.0)
        lines = r.text.strip().split("\n")
        print(f"  Total log lines: {len(lines)}")
        for l in lines[-40:]:
            print(f"  LOG: {l.strip()}")
    except Exception as e:
        print(f"  LOG ERROR: {e}")

    # Miner status
    try:
        r = httpx.get(f"http://{ip}/cgi-bin/get_miner_status.cgi", auth=auth, timeout=5.0)
        print(f"  STATUS ({r.status_code}): {r.text[:400]}")
    except Exception as e:
        print(f"  STATUS ERROR: {e}")

    # Pool config
    try:
        r = httpx.get(f"http://{ip}/cgi-bin/get_miner_conf.cgi", auth=auth, timeout=5.0)
        data = r.json()
        pools = data.get("pools", data.get("Pools", []))
        print(f"  POOLS CONFIG: {json.dumps(pools, indent=2)[:500]}")
        # Also print autofreq settings
        for key in data:
            if "freq" in key.lower() or "auto" in key.lower() or "sleep" in key.lower():
                print(f"  {key}: {data[key]}")
    except Exception as e:
        print(f"  CONF ERROR: {e}")
