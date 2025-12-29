"""
Mock AwesomeMiner server for development and testing.

This script simulates an AwesomeMiner server with fake miners for development
when you don't have access to real mining hardware.

Run with: python scripts/mock_awesome_miner.py
"""
import asyncio
import random
from datetime import datetime
from typing import Dict, List, Optional

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import uvicorn


app = FastAPI(title="Mock AwesomeMiner API")

# Simulated miner data
class MockMiner:
    def __init__(self, miner_id: int, name: str, rated_power: float):
        self.id = miner_id
        self.name = name
        self.rated_power = rated_power  # Watts
        self.status = "Stopped"
        self.power_usage = 0.0
        self.enabled = True
    
    def start(self):
        if self.enabled:
            self.status = "Mining"
            self.power_usage = self.rated_power * random.uniform(0.85, 0.98)
    
    def stop(self):
        self.status = "Stopped"
        self.power_usage = 0.0
    
    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "hostname": f"miner-{self.id}.local",
            "status": self.status,
            "statusInfo": f"Running since {datetime.now().isoformat()}" if self.status == "Mining" else "Idle",
            "powerUsage": self.power_usage + random.uniform(-50, 50) if self.status == "Mining" else 0,
            "speedInfo": f"{random.uniform(80, 120):.1f} MH/s" if self.status == "Mining" else "0 MH/s",
            "pool": "stratum+tcp://pool.example.com:3333" if self.status == "Mining" else "",
            "coin": "ETH" if self.status == "Mining" else ""
        }


# Initialize mock miners
MOCK_MINERS: Dict[int, MockMiner] = {
    1: MockMiner(1, "Miner-Alpha", 3200),
    2: MockMiner(2, "Miner-Beta", 2800),
    3: MockMiner(3, "Miner-Gamma", 3500),
    4: MockMiner(4, "Miner-Delta", 2500),
    5: MockMiner(5, "Miner-Epsilon", 3000),
}


@app.get("/api/miners")
async def get_miners():
    """Get all miners."""
    return [miner.to_dict() for miner in MOCK_MINERS.values()]


@app.get("/api/miners/{miner_id}")
async def get_miner(miner_id: int):
    """Get specific miner."""
    if miner_id not in MOCK_MINERS:
        raise HTTPException(status_code=404, detail="Miner not found")
    return MOCK_MINERS[miner_id].to_dict()


@app.post("/api/miners/{miner_id}/start")
async def start_miner(miner_id: int):
    """Start mining on specific miner."""
    if miner_id not in MOCK_MINERS:
        raise HTTPException(status_code=404, detail="Miner not found")
    
    miner = MOCK_MINERS[miner_id]
    miner.start()
    print(f"[MOCK] Started miner {miner_id} ({miner.name})")
    return {"success": True, "message": f"Miner {miner_id} started"}


@app.post("/api/miners/{miner_id}/stop")
async def stop_miner(miner_id: int):
    """Stop mining on specific miner."""
    if miner_id not in MOCK_MINERS:
        raise HTTPException(status_code=404, detail="Miner not found")
    
    miner = MOCK_MINERS[miner_id]
    miner.stop()
    print(f"[MOCK] Stopped miner {miner_id} ({miner.name})")
    return {"success": True, "message": f"Miner {miner_id} stopped"}


@app.post("/api/miners/{miner_id}/restart")
async def restart_miner(miner_id: int):
    """Restart specific miner."""
    if miner_id not in MOCK_MINERS:
        raise HTTPException(status_code=404, detail="Miner not found")
    
    miner = MOCK_MINERS[miner_id]
    miner.stop()
    await asyncio.sleep(0.5)
    miner.start()
    print(f"[MOCK] Restarted miner {miner_id} ({miner.name})")
    return {"success": True, "message": f"Miner {miner_id} restarted"}


@app.post("/api/miners/{miner_id}/enable")
async def enable_miner(miner_id: int):
    """Enable miner."""
    if miner_id not in MOCK_MINERS:
        raise HTTPException(status_code=404, detail="Miner not found")
    
    MOCK_MINERS[miner_id].enabled = True
    print(f"[MOCK] Enabled miner {miner_id}")
    return {"success": True}


@app.post("/api/miners/{miner_id}/disable")
async def disable_miner(miner_id: int):
    """Disable miner."""
    if miner_id not in MOCK_MINERS:
        raise HTTPException(status_code=404, detail="Miner not found")
    
    miner = MOCK_MINERS[miner_id]
    miner.enabled = False
    miner.stop()
    print(f"[MOCK] Disabled miner {miner_id}")
    return {"success": True}


@app.get("/api/groups")
async def get_groups():
    """Get miner groups."""
    return {
        "groups": [
            {"id": 1, "name": "All Miners", "minerCount": len(MOCK_MINERS)}
        ]
    }


if __name__ == "__main__":
    print("=" * 50)
    print("Mock AwesomeMiner Server")
    print("=" * 50)
    print(f"Simulating {len(MOCK_MINERS)} miners:")
    for miner in MOCK_MINERS.values():
        print(f"  - {miner.name}: {miner.rated_power}W")
    print("=" * 50)
    print("Starting server on http://localhost:17790")
    print("=" * 50)
    
    uvicorn.run(app, host="0.0.0.0", port=17790)
