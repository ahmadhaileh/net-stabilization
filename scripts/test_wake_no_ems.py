#!/usr/bin/env python3
"""
Wake a single miner and monitor boot WITHOUT EMS interference.

This script talks directly to the miner via Vnish API — no container needed.
Run from any machine that can reach the miner on the LAN.
"""
import httpx
import time
import json
import sys

ip = sys.argv[1] if len(sys.argv) > 1 else "192.168.95.27"
auth = httpx.DigestAuth("root", "root")

print(f"Testing miner {ip} (EMS container should be stopped)")
print("=" * 60)

# Check current state
try:
    r = httpx.get(f"http://{ip}/cgi-bin/get_miner_status.cgi", auth=auth, timeout=5.0)
    print(f"BEFORE STATUS ({r.status_code}):")
    try:
        d = r.json()
        s = d.get("summary", {})
        print(f"  GHS5s: {s.get('ghs5s', '?')}, GHSav: {s.get('ghsav', '?')}, Elapsed: {s.get('elapsed', '?')}")
        pools = d.get("pools", [])
        if pools and isinstance(pools[0], dict):
            p = pools[0]
            print(f"  Pool: {p.get('url', '?')}, Status: {p.get('status', '?')}, Accepted: {p.get('accepted', '?')}")
        print(f"  (Valid JSON = True)")
    except Exception:
        print(f"  (Malformed JSON)")
        print(f"  First 200 chars: {r.text[:200]}")
except Exception as e:
    print(f"  Error: {e}")

# Also check CGMiner TCP port 4028
print("\nCGMiner TCP test (port 4028):")
import socket
try:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(3.0)
    s.connect((ip, 4028))
    s.sendall(json.dumps({"command": "summary"}).encode())
    data = b""
    while True:
        try:
            chunk = s.recv(4096)
            if not chunk:
                break
            data += chunk
        except socket.timeout:
            break
    s.close()
    cleaned = data.replace(b"\x00", b"").decode()
    d = json.loads(cleaned)
    ss = d.get("SUMMARY", [{}])[0]
    print(f"  GHS 5s: {ss.get('GHS 5s', '?')}, Elapsed: {ss.get('Elapsed', '?')}")
except Exception as e:
    print(f"  CGMiner TCP: {e}")

# Wake it
print("\n" + "=" * 60)
print("WAKING MINER...")
r = httpx.post(f"http://{ip}/cgi-bin/do_sleep_mode.cgi", data={"mode": "0"}, auth=auth, timeout=5.0)
print(f"Wake response: {r.status_code} {r.text[:100]}")

# Poll every 10s for 3.5 minutes — watching BOTH Vnish HTTP and CGMiner TCP
print("\nMonitoring (both Vnish HTTP and CGMiner TCP):")
print(f"{'Time':>6}  {'Vnish HTTP':^40}  {'CGMiner TCP':^30}")
print("-" * 80)

for i in range(21):
    time.sleep(10)
    t = (i + 1) * 10

    # Vnish HTTP
    vnish_str = ""
    try:
        r = httpx.get(f"http://{ip}/cgi-bin/get_miner_status.cgi", auth=auth, timeout=5.0)
        try:
            d = r.json()
            s = d.get("summary", {})
            ghs = s.get("ghs5s", "0")
            elapsed = s.get("elapsed", 0)
            pools = d.get("pools", [])
            pstatus = pools[0].get("status", "?") if pools and isinstance(pools[0], dict) else "?"
            vnish_str = f"GHS={ghs} E={elapsed} Pool={pstatus}"
        except Exception:
            vnish_str = f"MALFORMED_JSON (HTTP {r.status_code})"
    except Exception as e:
        vnish_str = f"TIMEOUT/ERR: {e}"

    # CGMiner TCP
    cgm_str = ""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(3.0)
        s.connect((ip, 4028))
        s.sendall(json.dumps({"command": "summary"}).encode())
        data = b""
        while True:
            try:
                chunk = s.recv(4096)
                if not chunk:
                    break
                data += chunk
            except socket.timeout:
                break
        s.close()
        cleaned = data.replace(b"\x00", b"").decode()
        d = json.loads(cleaned)
        ss = d.get("SUMMARY", [{}])[0]
        ghs = ss.get("GHS 5s", "?")
        elapsed = ss.get("Elapsed", "?")
        cgm_str = f"GHS={ghs} E={elapsed}"
    except Exception as e:
        cgm_str = f"N/A ({type(e).__name__})"

    print(f"+{t:>4}s  {vnish_str:<40}  {cgm_str:<30}")

# Final autofreq check
print("\nAutofreq log (latest entries):")
try:
    r = httpx.get(f"http://{ip}/cgi-bin/get_autofreq_log.cgi", auth=auth, timeout=5.0)
    lines = r.text.strip().split("\n")
    for l in lines[:15]:
        print(f"  {l.strip()}")
except Exception as e:
    print(f"  Error: {e}")

# Put it back to sleep
print("\nPutting miner back to sleep...")
r = httpx.post(f"http://{ip}/cgi-bin/do_sleep_mode.cgi", data={"mode": "1"}, auth=auth, timeout=5.0)
print(f"Sleep response: {r.status_code}")
