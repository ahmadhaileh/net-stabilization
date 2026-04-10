"""
Dashboard API — provides endpoints for the web dashboard.

These are internal APIs, NOT part of the EMS protocol.
They bridge the dashboard JS to the Maestro.
"""
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
import structlog

from app.services.maestro import get_maestro
from app.services.miner_control import (
    Miner,
    MinerState,
    sleep_miner,
    wake_miner,
    poll_miner,
    discover_miners,
)

logger = structlog.get_logger()

router = APIRouter(prefix="/dashboard/api", tags=["Dashboard API"])


# ── Response Models ───────────────────────────────────────────────

class FleetStatusResponse(BaseModel):
    state: str
    is_available_for_dispatch: bool
    running_status: int
    rated_power_kw: float
    active_power_kw: float
    measured_power_kw: Optional[float] = None
    plant_power_kw: Optional[float] = None
    estimated_power_kw: float = 0.0
    voltage: Optional[float] = None
    power_source: str = "estimate"
    target_power_kw: Optional[float] = None
    total_miners: int = 0
    online_miners: int = 0
    mining_miners: int = 0
    manual_override_active: bool = False
    override_target_power_kw: Optional[float] = None
    last_update: datetime = Field(default_factory=datetime.utcnow)
    last_ems_command: Optional[datetime] = None
    errors: List[str] = Field(default_factory=list)
    power_control_mode: str = "on_off"
    dev_mode: bool = False
    active_sections: Optional[List[int]] = None


class MinerStatusResponse(BaseModel):
    miner_id: str
    name: str
    is_online: bool
    is_mining: bool
    power_kw: float
    rated_power_kw: float
    target_power_kw: Optional[float] = None
    last_update: datetime = Field(default_factory=datetime.utcnow)
    error: Optional[str] = None


class ManualControlRequest(BaseModel):
    action: str = Field(..., pattern="^(start|stop)$")


# ── Status ────────────────────────────────────────────────────────

@router.get("/status", response_model=FleetStatusResponse)
async def get_fleet_status():
    """Get detailed fleet status for dashboard display."""
    maestro = get_maestro()
    s = maestro.get_status()

    return FleetStatusResponse(
        state=s["state"],
        is_available_for_dispatch=s["is_available_for_dispatch"],
        running_status=s["running_status"],
        rated_power_kw=s["rated_power_kw"],
        active_power_kw=s["active_power_kw"],
        measured_power_kw=s.get("measured_power_kw"),
        voltage=s.get("voltage"),
        power_source="meter" if s.get("measured_power_kw") is not None else "estimate",
        target_power_kw=s.get("target_power_kw"),
        total_miners=s["total_miners"],
        online_miners=s["online_miners"],
        mining_miners=s["mining_miners"],
        last_update=datetime.utcnow(),
        last_ems_command=datetime.fromisoformat(s["last_ems_command"]) if s.get("last_ems_command") else None,
    )


@router.get("/discovery/miners")
async def get_discovery_miners():
    """Get all miners across all sections."""
    maestro = get_maestro()
    miners = []

    for section in maestro.sections:
        for ip, miner in section.miners.items():
            miners.append({
                "ip": ip,
                "id": ip.replace(".", "_"),
                "model": miner.model,
                "firmware": miner.firmware,
                "mac_address": miner.mac_address,
                "is_online": miner.state != MinerState.OFFLINE,
                "is_mining": miner.state == MinerState.MINING,
                "hashrate_ghs": round(miner.hashrate_ghs, 1),
                "power_watts": round(miner.power_watts, 1),
                "temperature_c": round(miner.temperature_c, 1),
                "fan_speed_pct": round(miner.fan_speed_pct, 1),
                "state": miner.state.value,
                "section": section.section_id,
                "last_seen": miner.last_seen.isoformat() if miner.last_seen else None,
            })

    return {"miners": miners, "total": len(miners)}


@router.get("/health")
async def health_check():
    """Dashboard health check."""
    maestro = get_maestro()
    s = maestro.get_status()

    return {
        "healthy": True,
        "mode": "maestro",
        "services": {
            "maestro": True,
            "sections": len(maestro.sections),
            "power_meter": maestro.power_meter.is_healthy,
        },
        "miners_registered": s["total_miners"],
        "miners_online": s["online_miners"],
        "fleet_state": s["state"],
        "timestamp": datetime.utcnow().isoformat(),
    }


@router.get("/history")
async def get_history(limit: int = 100):
    """Get command history (stub — to be backed by DB)."""
    return {"commands": []}


# ── Sections ──────────────────────────────────────────────────────

@router.get("/sections")
async def get_sections():
    """Get status of all sections."""
    maestro = get_maestro()
    return {
        "sections": [s.get_status() for s in maestro.sections]
    }


# ── Miner Control ────────────────────────────────────────────────

@router.post("/miners/{miner_id}/control")
async def control_miner(miner_id: str, request: ManualControlRequest):
    """
    Manually control a miner.
    
    Actions: start (wake), stop (sleep).
    miner_id is the IP with dots replaced by underscores.
    """
    ip = miner_id.replace("_", ".")
    maestro = get_maestro()

    # Find the miner across sections
    miner = None
    for section in maestro.sections:
        if ip in section.miners:
            miner = section.miners[ip]
            break

    if not miner:
        raise HTTPException(status_code=404, detail=f"Miner {ip} not found")

    if request.action == "start":
        ok = await wake_miner(ip)
        msg = "Wake command sent" if ok else "Wake failed"
    elif request.action == "stop":
        ok = await sleep_miner(ip)
        msg = "Sleep command sent" if ok else "Sleep failed"
    else:
        raise HTTPException(status_code=400, detail=f"Unknown action: {request.action}")

    logger.info("manual_miner_control", ip=ip, action=request.action, success=ok)

    return {
        "success": ok,
        "miner_id": miner_id,
        "action": request.action,
        "message": msg,
    }


@router.post("/discovery/scan")
async def trigger_scan():
    """Trigger a network scan for miners."""
    from app.config import get_settings
    settings = get_settings()

    ips = await discover_miners(
        settings.miner_network_cidr,
        timeout=settings.miner_scan_timeout,
    )

    return {
        "success": True,
        "miners_found": len(ips),
        "ips": ips,
    }


# ── Snapshots (historical data) ──────────────────────────────────

@router.get("/fleet-snapshots")
async def get_fleet_snapshots(hours: int = 24, limit: int = 1440):
    """Return fleet-level snapshots from the database."""
    try:
        from app.database import get_db_service
        db = get_db_service()
        snapshots = db.get_fleet_snapshots(hours=hours, limit=limit)

        return {
            "snapshots": [
                {
                    "timestamp": s.timestamp.isoformat(),
                    "total_hashrate_ghs": s.total_hashrate_ghs,
                    "total_power_watts": s.total_power_watts,
                    "avg_temperature": s.avg_temperature,
                    "miners_online": s.miners_online,
                    "miners_mining": s.miners_mining,
                    "miners_total": s.miners_total,
                    "fleet_state": s.fleet_state,
                    "measured_power_kw": s.measured_power_kw,
                    "plant_power_kw": s.plant_power_kw,
                    "voltage": s.voltage,
                    "target_power_kw": s.target_power_kw,
                }
                for s in reversed(snapshots)
            ]
        }
    except Exception as e:
        logger.warning("fleet_snapshots_error", error=str(e))
        return {"snapshots": []}


@router.get("/miner-snapshots/{miner_ip}")
async def get_miner_snapshots(miner_ip: str, hours: int = 24, limit: int = 1440):
    """Return per-miner snapshots from the database."""
    try:
        from app.database import get_db_service
        db = get_db_service()
        snapshots = db.get_miner_snapshots(miner_ip=miner_ip, hours=hours, limit=limit)

        return {
            "miner_ip": miner_ip,
            "snapshots": [
                {
                    "timestamp": s.timestamp.isoformat(),
                    "hashrate_ghs": s.hashrate_ghs,
                    "power_watts": s.power_watts,
                    "temperature": s.temperature,
                    "fan_speed": s.fan_speed,
                    "frequency": getattr(s, "frequency", None),
                    "is_mining": s.is_mining,
                }
                for s in reversed(snapshots)
            ],
        }
    except Exception as e:
        logger.warning("miner_snapshots_error", error=str(e))
        return {"miner_ip": miner_ip, "snapshots": []}
