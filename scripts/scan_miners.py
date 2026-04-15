#!/usr/bin/env python3
"""Scan all miners and classify their autofreq log behavior."""
import subprocess
import sys

ips = [f"192.168.95.{i}" for i in list(range(10, 46)) + list(range(100, 243))]

pool_fail = 0
fan_error = 0
online_only = 0
unreachable = 0
other = 0
fan_ips = []
online_ips = []
other_ips = []
pool_fail_ips = []

for idx, ip in enumerate(ips):
    if idx % 20 == 0:
        print(f"Progress: {idx}/{len(ips)}...", file=sys.stderr)
    try:
        r = subprocess.run(
            ["curl", "-s", "--connect-timeout", "2", "--digest", "-u", "root:root",
             f"http://{ip}/cgi-bin/get_autofreq_log.cgi"],
            capture_output=True, text=True, timeout=5
        )
        log = r.stdout.strip()
        if not log:
            unreachable += 1
            continue

        today = [l for l in log.split("\n") if "Apr 13" in l]
        pf = sum(1 for l in today if "FAILED" in l)
        fe = sum(1 for l in today if "FAN ERROR" in l)
        on = sum(1 for l in today if "Online" in l)

        if fe > 0:
            fan_error += 1
            fan_ips.append(f"{ip} fan={fe} pf={pf}")
        elif pf > 0:
            pool_fail += 1
            pool_fail_ips.append(f"{ip} fails={pf} online={on}")
        elif on > 0:
            online_only += 1
            online_ips.append(f"{ip} online={on}")
        elif not today:
            other += 1
            other_ips.append(f"{ip} no_entries_today")
        else:
            other += 1
            other_ips.append(ip)
    except Exception:
        unreachable += 1

print("=" * 50)
print("FLEET AUTOFREQ LOG SCAN - Apr 13")
print("=" * 50)
print(f"Pool fail + cycling:  {pool_fail} miners")
print(f"Fan errors:           {fan_error} miners")
print(f"Online only (clean):  {online_only} miners")
print(f"Unreachable:          {unreachable} miners")
print(f"Other:                {other} miners")
print(f"Total scanned:        {len(ips)}")
print()
print("--- Fan Error miners ---")
for m in fan_ips:
    print(f"  {m}")
print()
print("--- Online only (no pool failures today) ---")
for m in online_ips:
    print(f"  {m}")
print()
print("--- Pool fail + cycling (sample) ---")
for m in pool_fail_ips[:15]:
    print(f"  {m}")
if len(pool_fail_ips) > 15:
    print(f"  ... and {len(pool_fail_ips) - 15} more")
print()
print("--- Other ---")
for m in other_ips:
    print(f"  {m}")
