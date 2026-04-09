"""
API routes — EMS protocol + dashboard.

EMS contract (must be preserved exactly):
  GET  /api/status      → {isAvailableForDispatch, runningStatus, ratedPowerInKw, activePowerInKw}
  POST /api/activate    → {accepted, message}
  POST /api/deactivate  → {accepted, message}

Dashboard routes serve the monitoring UI and per-section status.
"""
from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field
import structlog

from app.v2.maestro import get_maestro

logger = structlog.get_logger()
templates = Jinja2Templates(directory="app/v2/templates")

# ---------------------------------------------------------------------------
# EMS Protocol
# ---------------------------------------------------------------------------
ems = APIRouter(prefix="/api", tags=["EMS"])


class ActivateRequest(BaseModel):
    activation_power_in_kw: float = Field(..., alias="activationPowerInKw", ge=0)

    class Config:
        populate_by_name = True


@ems.get("/status")
async def ems_status():
    m = get_maestro()
    running = 2 if m.state in ("activating", "running") else 1
    return {
        "isAvailableForDispatch": True,
        "runningStatus": running,
        "ratedPowerInKw": round(m.rated_kw, 2),
        "activePowerInKw": round(m.active_power_kw, 2),
    }


@ems.post("/activate")
async def ems_activate(body: ActivateRequest):
    m = get_maestro()
    target = body.activation_power_in_kw
    logger.info("EMS activate", target_kw=target)
    ok, msg = await m.activate(target)
    return {"accepted": ok, "message": msg}


@ems.post("/deactivate")
async def ems_deactivate():
    m = get_maestro()
    logger.info("EMS deactivate")
    ok, msg = await m.deactivate()
    return {"accepted": ok, "message": msg}


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------
dash = APIRouter(tags=["Dashboard"])


@dash.get("/", response_class=HTMLResponse, include_in_schema=False)
async def dashboard_page(request: Request):
    return templates.TemplateResponse("dashboard.html", {"request": request})


@dash.get("/dashboard/api/status")
async def dashboard_status():
    m = get_maestro()
    return m.status_dict()


@dash.get("/dashboard/api/miners")
async def dashboard_miners():
    m = get_maestro()
    miners = []
    for sec in m.managers:
        for mi in sec.miners.values():
            miners.append({
                "ip": mi.ip,
                "section": sec.name,
                "state": mi.state.value,
                "hashrate_ghs": round(mi.hashrate_ghs, 1),
                "power_watts": round(mi.power_watts, 1),
                "temperature_c": round(mi.temperature_c, 1),
                "fan_speed_pct": round(mi.fan_speed_pct, 1),
                "uptime_seconds": mi.uptime_seconds,
                "model": mi.model,
                "last_seen": mi.last_seen.isoformat() if mi.last_seen else None,
            })
    return miners


@dash.get("/health")
async def health():
    m = get_maestro()
    return {
        "status": "healthy",
        "state": m.state,
        "miners": m.total_miners,
        "mining": m.mining_count,
    }
