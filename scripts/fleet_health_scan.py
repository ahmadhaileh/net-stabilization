#!/usr/bin/env python3
"""Scan all miners from the server and classify their autofreq log issues."""
import asyncio
import httpx
import json
import sys

VNISH_USER = "root"
VNISH_PASS = "root"

async def get_autofreq_log(client: httpx.AsyncClient, ip: str) -> str:
    try:
        auth = httpx.DigestAuth(VNISH_USER, VNISH_PASS)
        r = await client.get(f"http://{ip}/cgi-bin/get_autofreq_log.cgi", auth=auth, timeout=5.0)
        return r.text.strip()
    except Exception:
        return ""

async def get_status(client: httpx.AsyncClient, ip: str) -> dict:
    try:
        auth = httpx.DigestAuth(VNISH_USER, VNISH_PASS)
        r = await client.get(f"http://{ip}/cgi-bin/get_miner_status.cgi", auth=auth, timeout=5.0)
        return r.json()
    except Exception:
        return {}

async def scan_miner(client: httpx.AsyncClient, sem: asyncio.Semaphore, ip: str) -> dict:
    async with sem:
        log_text = await get_autofreq_log(client, ip)
        status = await get_status(client, ip)

        # Parse status
        summary = status.get("SUMMARY", [{}])[0] if status.get("SUMMARY") else {}
        ghs = summary.get("GHS 5s", 0)
        elapsed = summary.get("Elapsed", 0)

        # Parse autofreq log
        lines = [l.strip() for l in log_text.split("\n") if l.strip()] if log_text else []
        today_lines = [l for l in lines if "Apr 13" in l]

        pool_fails = sum(1 for l in today_lines if "FAILED" in l)
        fan_errors = sum(1 for l in today_lines if "FAN ERROR" in l)
        online_count = sum(1 for l in today_lines if "Online" in l)
        temp_errors = sum(1 for l in today_lines if "TEMP" in l.upper() and "ERROR" in l.upper())
        hw_errors = sum(1 for l in today_lines if "HW ERROR" in l.upper())

        # Determine current state
        if ghs > 0 and elapsed > 0:
            state = "mining"
        elif status and not ghs:
            state = "sleeping"
        else:
            state = "offline"

        # Classify issues
        issues = []
        if fan_errors > 0:
            # Get fan detail from latest entry
            fan_lines = [l for l in today_lines if "FAN ERROR" in l]
            detail = fan_lines[0] if fan_lines else ""
            issues.append(f"FAN_ERROR({fan_errors}x today): {detail[-60:]}")
        if pool_fails > 0:
            issues.append(f"POOL_FAIL({pool_fails}x today, {online_count}x Online)")
        if temp_errors > 0:
            issues.append(f"TEMP_ERROR({temp_errors}x today)")
        if hw_errors > 0:
            issues.append(f"HW_ERROR({hw_errors}x today)")
        if not lines:
            issues.append("NO_LOG_DATA")
        if not today_lines and lines:
            last = lines[0][:60] if lines else "?"
            issues.append(f"NO_ENTRIES_TODAY (last: {last})")

        return {
            "ip": ip,
            "state": state,
            "ghs": ghs,
            "issues": issues,
            "pool_fails": pool_fails,
            "fan_errors": fan_errors,
            "online_count": online_count,
        }

async def main():
    # First discover all miners via port 80
    import ipaddress
    network = ipaddress.ip_network("192.168.95.0/24", strict=False)
    
    print("Discovering miners...", file=sys.stderr)
    found = []
    
    async def probe(ip_str):
        try:
            r, w = await asyncio.wait_for(asyncio.open_connection(ip_str, 80), timeout=1.0)
            w.close()
            await w.wait_closed()
            found.append(ip_str)
        except:
            pass
    
    await asyncio.gather(*[probe(str(ip)) for ip in network.hosts()])
    found.sort(key=lambda x: list(map(int, x.split("."))))
    print(f"Found {len(found)} miners", file=sys.stderr)

    # Scan all miners
    sem = asyncio.Semaphore(30)
    async with httpx.AsyncClient() as client:
        tasks = [scan_miner(client, sem, ip) for ip in found]
        results = await asyncio.gather(*tasks)

    # Sort by IP
    results.sort(key=lambda r: list(map(int, r["ip"].split("."))))

    # Print per-miner report
    print("=" * 80)
    print(f"FLEET HEALTH REPORT - {len(results)} miners scanned")
    print("=" * 80)
    
    # Category counters
    clean = []
    pool_issue = []
    fan_issue = []
    temp_issue = []
    hw_issue = []
    no_log = []
    no_today = []

    for r in results:
        issues_str = "; ".join(r["issues"]) if r["issues"] else "CLEAN"
        print(f"{r['ip']:>18s}  [{r['state']:>8s}]  {issues_str}")
        
        if not r["issues"]:
            clean.append(r["ip"])
        for iss in r["issues"]:
            if "FAN_ERROR" in iss:
                fan_issue.append(r["ip"])
            if "POOL_FAIL" in iss:
                pool_issue.append(r["ip"])
            if "TEMP_ERROR" in iss:
                temp_issue.append(r["ip"])
            if "HW_ERROR" in iss:
                hw_issue.append(r["ip"])
            if "NO_LOG_DATA" in iss:
                no_log.append(r["ip"])
            if "NO_ENTRIES_TODAY" in iss:
                no_today.append(r["ip"])

    print()
    print("=" * 80)
    print("SUMMARY")
    print("=" * 80)
    print(f"Total miners:           {len(results)}")
    print(f"Clean (no issues):      {len(clean)}")
    print(f"Pool failures today:    {len(pool_issue)}")
    print(f"Fan errors today:       {len(fan_issue)}")
    print(f"Temp errors today:      {len(temp_issue)}")
    print(f"HW errors today:        {len(hw_issue)}")
    print(f"No log data at all:     {len(no_log)}")
    print(f"No entries today:       {len(no_today)}")
    print()
    
    states = {}
    for r in results:
        states[r["state"]] = states.get(r["state"], 0) + 1
    print("Current states:")
    for s, c in sorted(states.items()):
        print(f"  {s}: {c}")
    
    print()
    if fan_issue:
        print(f"FAN ERROR miners: {', '.join(fan_issue)}")
    if temp_issue:
        print(f"TEMP ERROR miners: {', '.join(temp_issue)}")
    if hw_issue:
        print(f"HW ERROR miners: {', '.join(hw_issue)}")
    if no_log:
        print(f"No log data: {', '.join(no_log)}")

asyncio.run(main())
