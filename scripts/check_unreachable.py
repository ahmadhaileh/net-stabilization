#!/usr/bin/env python3
"""Check unreachable miners: ping + HTTP classify."""
import subprocess
import sys

ips = [f"192.168.95.{i}" for i in list(range(10, 46)) + list(range(100, 243))]

unreachable_ips = []
reachable_ips = []

for idx, ip in enumerate(ips):
    if idx % 30 == 0:
        print(f"HTTP scan: {idx}/{len(ips)}...", file=sys.stderr)
    try:
        r = subprocess.run(
            ["curl", "-s", "--connect-timeout", "2", "--digest", "-u", "root:root",
             f"http://{ip}/cgi-bin/get_autofreq_log.cgi"],
            capture_output=True, text=True, timeout=6
        )
        if r.stdout.strip():
            reachable_ips.append(ip)
        else:
            unreachable_ips.append(ip)
    except Exception:
        unreachable_ips.append(ip)

print(f"\nHTTP reachable: {len(reachable_ips)}, unreachable: {len(unreachable_ips)}", file=sys.stderr)
print(f"Deep-checking {len(unreachable_ips)} unreachable miners...\n", file=sys.stderr)

ping_ok = []
ping_fail = []

for idx, ip in enumerate(unreachable_ips):
    if idx % 10 == 0:
        print(f"Deep scan: {idx}/{len(unreachable_ips)}...", file=sys.stderr)

    try:
        ping = subprocess.run(["ping", "-c", "1", "-W", "1", ip],
                              capture_output=True, text=True, timeout=3)
        ping_alive = ping.returncode == 0
    except Exception:
        ping_alive = False

    try:
        http = subprocess.run(["curl", "-s", "--connect-timeout", "2", f"http://{ip}/"],
                              capture_output=True, text=True, timeout=6)
        http_resp = "YES" if len(http.stdout) > 10 else "empty"
    except Exception:
        http_resp = "timeout"

    line = f"{ip}: ping={'OK' if ping_alive else 'FAIL'} http_root={http_resp}"
    print(line)

    if ping_alive:
        ping_ok.append(ip)
    else:
        ping_fail.append(ip)

print()
print("=" * 50)
print("UNREACHABLE MINER CLASSIFICATION")
print("=" * 50)
print(f"Total unreachable (no autofreq response): {len(unreachable_ips)}")
print(f"Responds to ping (network up, HTTP down): {len(ping_ok)}")
print(f"Completely dead (no ping):                {len(ping_fail)}")
print()
if ping_ok:
    print("--- Ping OK but no HTTP (possibly rebooting/stuck) ---")
    for ip in ping_ok:
        print(f"  {ip}")
    print()
if ping_fail:
    print("--- Completely dead (no ping, no HTTP) ---")
    for ip in ping_fail:
        print(f"  {ip}")
