#!/usr/bin/env python3
"""
Collect diagnostics from all miners on 192.168.95.0/24.

For each reachable miner, fetches:
  - /cgi-bin/get_miner_status.cgi  (mining stats, chain status, errors)
  - /cgi-bin/get_system_info.cgi   (hardware, firmware, MAC)
  - /cgi-bin/get_miner_conf.cgi    (config: freq, voltage, pools)

Outputs a single JSON file: /tmp/miner_diagnostics.json
"""
import json
import sys
import socket
import concurrent.futures
from urllib.request import Request, urlopen, HTTPPasswordMgrWithDefaultRealm, HTTPDigestAuthHandler, build_opener

AUTH_USER = "root"
AUTH_PASS = "root"
NETWORK = "192.168.95"
IP_RANGE = range(1, 255)
PORT = 80
TIMEOUT = 8

ENDPOINTS = [
    "/cgi-bin/get_miner_status.cgi",
    "/cgi-bin/get_system_info.cgi",
    "/cgi-bin/get_miner_conf.cgi",
]


def fetch_endpoint(ip, endpoint):
    """Fetch a single endpoint from a miner with digest auth."""
    url = f"http://{ip}{endpoint}"
    try:
        passmgr = HTTPPasswordMgrWithDefaultRealm()
        passmgr.add_password(None, url, AUTH_USER, AUTH_PASS)
        auth_handler = HTTPDigestAuthHandler(passmgr)
        opener = build_opener(auth_handler)
        resp = opener.open(url, timeout=TIMEOUT)
        data = resp.read().decode("utf-8", errors="replace")
        try:
            return json.loads(data)
        except json.JSONDecodeError:
            return {"_raw": data[:2000]}
    except Exception as e:
        return {"_error": str(e)}


def probe_miner(ip):
    """Check if a miner is reachable on port 80, then fetch all endpoints."""
    # Quick TCP probe first
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(2)
    try:
        result = sock.connect_ex((ip, PORT))
        if result != 0:
            return None  # Not reachable
    except Exception:
        return None
    finally:
        sock.close()

    # Fetch all endpoints
    diag = {"ip": ip}
    for ep in ENDPOINTS:
        key = ep.split("/")[-1].replace(".cgi", "")
        diag[key] = fetch_endpoint(ip, ep)

    return diag


def main():
    print(f"Scanning {NETWORK}.1-254 ...", file=sys.stderr)
    ips = [f"{NETWORK}.{i}" for i in IP_RANGE]
    results = []

    with concurrent.futures.ThreadPoolExecutor(max_workers=30) as pool:
        futures = {pool.submit(probe_miner, ip): ip for ip in ips}
        done = 0
        for future in concurrent.futures.as_completed(futures):
            done += 1
            if done % 20 == 0:
                print(f"  {done}/254 probed ...", file=sys.stderr)
            result = future.result()
            if result is not None:
                results.append(result)
                ip = result["ip"]
                status = result.get("get_miner_status", {})
                if "_error" in status:
                    print(f"  {ip}: reachable but status error: {status['_error'][:60]}", file=sys.stderr)
                else:
                    print(f"  {ip}: OK", file=sys.stderr)

    results.sort(key=lambda r: list(map(int, r["ip"].split("."))))
    print(f"\nFound {len(results)} reachable miners", file=sys.stderr)

    outpath = "/tmp/miner_diagnostics.json"
    with open(outpath, "w") as f:
        json.dump(results, f, indent=2)
    print(f"Saved to {outpath}", file=sys.stderr)


if __name__ == "__main__":
    main()
