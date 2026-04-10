"""
Dashboard API — provides endpoints for the web dashboard.

These are internal APIs, NOT part of the EMS protocol.
They bridge the dashboard JS to the Maestro.

All data comes from the Maestro's aggregated status (read from section
processes via IPC queues). This API never touches individual miners directly.
"""
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
import structlog

from app.services.maestro import get_maestro
from app.services.miner_control import discover_miners

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
    voltage: Optional[float] = None
    power_source: str = "estimate"
    target_power_kw: Optional[float] = None
    total_miners: int = 0
    online_miners: int = 0
    mining_miners: int = 0
    sleeping_miners: int = 0
    last_update: datetime = Field(default_factory=datetime.utcnow)
    last_ems_command: Optional[datetime] = None
    errors: List[str] = Field(default_factory=list)


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
        plant_power_kw=s.get("plant_power_kw"),
        voltage=s.get("voltage"),
        power_source="meter" if s.get("measured_power_kw") is not None else "estimate",
        target_power_kw=s.get("target_power_kw"),
        total_miners=s["total_miners"],
        online_miners=s["online_miners"],
        mining_miners=s["mining_miners"],
        sleeping_miners=s.get("sleeping_miners", 0),
        last_update=datetime.utcnow(),
        last_ems_command=datetime.fromisoformat(s["last_ems_command"]) if s.get("last_ems_command") else None,
    )


@router.get("/discovery/miners")
async def get_discovery_miners():
    """Get all miners across all sections (from process-isolated status)."""
    maestro = get_maestro()
    status = maestro.get_status()
    miners = []

    for section_status in status.get("sections", []):
        for miner in section_status.get("miners", []):
            miners.append(miner)

    return {"miners": miners, "total": len(miners)}


@router.get("/health")
async def health_check():
    """Dashboard health check."""
    maestro = get_maestro()
    s = maestro.get_status()

    # Check which section processes are alive
    sections_alive = sum(1 for sec in maestro.sections if sec.is_alive)

    return {
        "healthy": True,
        "mode": "maestro",
        "architecture": "process-isolated",
        "services": {
            "maestro": True,
            "sections_total": len(maestro.sections),
            "sections_alive": sections_alive,
            "power_meter": maestro.power_meter.is_healthy,
        },
        "miners_registered": s["total_miners"],
        "miners_online": s["online_miners"],
        "fleet_state": s["state"],
        "timestamp": datetime.utcnow().isoformat(),
    }


# ── Sections ──────────────────────────────────────────────────────

@router.get("/sections")
async def get_sections():
    """Get status of all sections including process health."""
    maestro = get_maestro()
    sections = []
    for sec in maestro.sections:
        status = sec.get_status()
        status["process_alive"] = sec.is_alive
        sections.append(status)

    return {"sections": sections}


@router.post("/sections/{section_id}/sleep")
async def section_sleep_all(section_id: str):
    """Deactivate (sleep) all miners in a specific section."""
    maestro = get_maestro()
    section = next((s for s in maestro.sections if s.section_id == section_id), None)
    if not section:
        raise HTTPException(status_code=404, detail=f"Section {section_id} not found")
    section.deactivate()
    logger.info("section_sleep_all", section=section_id)
    return {"success": True, "message": f"Sleep command sent to {section_id}"}


@router.post("/sections/{section_id}/wake")
async def section_wake_all(section_id: str):
    """Wake (set target to rated power) all miners in a specific section."""
    maestro = get_maestro()
    section = next((s for s in maestro.sections if s.section_id == section_id), None)
    if not section:
        raise HTTPException(status_code=404, detail=f"Section {section_id} not found")
    section.set_target(section.rated_power_kw)
    logger.info("section_wake_all", section=section_id, target_kw=section.rated_power_kw)
    return {"success": True, "message": f"Wake command sent to {section_id} (target: {section.rated_power_kw:.1f} kW)"}


# ── Miner Control ────────────────────────────────────────────────

@router.post("/miners/{miner_id}/control")
async def control_miner(miner_id: str, request: ManualControlRequest):
    """
    Manually control a miner via its section process.

    Commands funnel: Dashboard → API → Maestro → SectionProcess → Miner
    miner_id is the IP with dots replaced by underscores.
    """
    ip = miner_id.replace("_", ".")
    maestro = get_maestro()

    # Find which section owns this miner
    section = maestro.find_section_for_miner(ip)
    if not section:
        raise HTTPException(status_code=404, detail=f"Miner {ip} not found in any section")

    # Send command through the section process (funneled downward)
    section.control_miner(ip, request.action)

    logger.info(
        "manual_miner_control",
        ip=ip,
        action=request.action,
        section=section.section_id,
    )

    return {
        "success": True,
        "miner_id": miner_id,
        "action": request.action,
        "message": f"{'Wake' if request.action == 'start' else 'Sleep'} command sent via {section.section_id}",
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
