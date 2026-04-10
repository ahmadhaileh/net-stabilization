"""Test miner-mode config property (used by Awesome Miner for sleep/wake)."""
import httpx
import asyncio
import json
import sys

async def main():
    ip = sys.argv[1] if len(sys.argv) > 1 else "192.168.95.144"
    auth = httpx.DigestAuth("root", "root")
    
    async with httpx.AsyncClient(timeout=15.0) as client:
        # Get current config
        print(f"=== Config from {ip} ===")
        r = await client.get(f"http://{ip}/cgi-bin/get_miner_conf.cgi", auth=auth)
        config = r.json()
        print(f"Keys: {list(config.keys())}")
        if "miner-mode" in config:
            print(f"miner-mode = {config['miner-mode']}")
        else:
            print("NO miner-mode in config")
        print(json.dumps(config, indent=2))

asyncio.run(main())
