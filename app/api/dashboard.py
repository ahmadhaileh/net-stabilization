"""
Dashboard API Routes.

These routes provide internal APIs for the web dashboard to monitor
and control the fleet. They are NOT part of the EMS protocol.
"""
from typing import List, Optional
from datetime import datetime

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field
import structlog

from app.config import get_settings
from app.models.state import FleetStatus, MinerState, CommandLog, SystemConfig
from app.services.fleet_manager import get_fleet_manager
from app.services.awesome_miner import get_awesome_miner_client
from app.services.miner_discovery import get_discovery_service

logger = structlog.get_logger()

router = APIRouter(prefix="/dashboard/api", tags=["Dashboard API"])


# Response models for dashboard
class FleetStatusResponse(BaseModel):
    """Extended fleet status for dashboard."""
    state: str
    is_available_for_dispatch: bool
    running_status: int
    rated_power_kw: float
    active_power_kw: float
    target_power_kw: Optional[float]
    total_miners: int
    online_miners: int
    mining_miners: int
    manual_override_active: bool
    override_target_power_kw: Optional[float]
    last_update: datetime
    last_ems_command: Optional[datetime]
    errors: List[str]


class MinerStatusResponse(BaseModel):
    """Miner status for dashboard."""
    miner_id: str
    name: str
    is_online: bool
    is_mining: bool
    power_kw: float
    rated_power_kw: float
    target_power_kw: Optional[float]
    last_update: datetime
    error: Optional[str]


class OverrideRequest(BaseModel):
    """Request to set manual override."""
    enabled: bool
    target_power_kw: Optional[float] = Field(
        None,
        ge=0,
        description="Target power when override is enabled (None to stop all)"
    )


class ConfigUpdateRequest(BaseModel):
    """Request to update configuration."""
    rated_power_kw_override: Optional[float] = None
    power_distribution_strategy: Optional[str] = None
    miner_priority: Optional[List[int]] = None
    max_power_change_rate_kw_per_sec: Optional[float] = None
    min_miner_power_percent: Optional[float] = None


class ManualControlRequest(BaseModel):
    """Request to manually control a miner."""
    action: str = Field(..., pattern="^(start|stop|restart|enable|disable)$")


# =========================================================================
# Status Endpoints
# =========================================================================

@router.get(
    "/status",
    response_model=FleetStatusResponse,
    summary="Get detailed fleet status"
)
async def get_fleet_status():
    """Get detailed fleet status for dashboard display."""
    fleet_manager = get_fleet_manager()
    s = fleet_manager.status
    
    return FleetStatusResponse(
        state=s.state.value,
        is_available_for_dispatch=s.is_available_for_dispatch,
        running_status=s.running_status.value,
        rated_power_kw=s.rated_power_kw,
        active_power_kw=s.active_power_kw,
        target_power_kw=s.target_power_kw,
        total_miners=s.total_miners,
        online_miners=s.online_miners,
        mining_miners=s.mining_miners,
        manual_override_active=s.manual_override_active,
        override_target_power_kw=s.override_target_power_kw,
        last_update=s.last_update,
        last_ems_command=s.last_ems_command,
        errors=s.errors
    )


@router.get(
    "/miners",
    response_model=List[MinerStatusResponse],
    summary="Get all miners status"
)
async def get_miners_status():
    """Get status of all miners."""
    fleet_manager = get_fleet_manager()
    
    return [
        MinerStatusResponse(
            miner_id=m.miner_id,
            name=m.name,
            is_online=m.is_online,
            is_mining=m.is_mining,
            power_kw=m.power_kw,
            rated_power_kw=m.rated_power_kw,
            target_power_kw=m.target_power_kw,
            last_update=m.last_update,
            error=m.error
        )
        for m in fleet_manager.status.miners
    ]


@router.get(
    "/miners/{miner_id}",
    response_model=MinerStatusResponse,
    summary="Get single miner status"
)
async def get_miner_status(miner_id: int):
    """Get status of a specific miner."""
    fleet_manager = get_fleet_manager()
    
    for m in fleet_manager.status.miners:
        if m.miner_id == miner_id:
            return MinerStatusResponse(
                miner_id=m.miner_id,
                name=m.name,
                is_online=m.is_online,
                is_mining=m.is_mining,
                power_kw=m.power_kw,
                rated_power_kw=m.rated_power_kw,
                target_power_kw=m.target_power_kw,
                last_update=m.last_update,
                error=m.error
            )
    
    raise HTTPException(status_code=404, detail="Miner not found")


# =========================================================================
# Control Endpoints
# =========================================================================

@router.post(
    "/override",
    summary="Set manual override"
)
async def set_override(request: OverrideRequest):
    """
    Enable or disable manual override mode.
    
    When enabled, EMS commands are ignored and the fleet operates
    at the specified target power.
    """
    fleet_manager = get_fleet_manager()
    
    success, message = await fleet_manager.set_manual_override(
        enabled=request.enabled,
        target_power_kw=request.target_power_kw
    )
    
    return {
        "success": success,
        "message": message,
        "override_active": request.enabled
    }


@router.post(
    "/miners/{miner_id}/control",
    summary="Manual miner control"
)
async def control_miner(miner_id: str, request: ManualControlRequest):
    """
    Manually control a specific miner.
    
    Available actions: start, stop, restart, enable, disable
    """
    settings = get_settings()
    
    if settings.miner_discovery_enabled:
        # Direct mode - use discovery service
        discovery = get_discovery_service()
        
        if request.action == "start":
            success, msg = await discovery.set_miner_active(miner_id)
        elif request.action == "stop":
            success, msg = await discovery.set_miner_idle(miner_id)
        elif request.action == "restart":
            success, msg = await discovery.restart_miner(miner_id)
        else:
            raise HTTPException(
                status_code=400,
                detail=f"Action '{request.action}' not supported in direct mode. Use start/stop/restart."
            )
    else:
        # AwesomeMiner mode
        am_client = get_awesome_miner_client()
        
        action_map = {
            "start": am_client.start_miner,
            "stop": am_client.stop_miner,
            "restart": am_client.restart_miner,
            "enable": am_client.enable_miner,
            "disable": am_client.disable_miner
        }
        
        action_func = action_map.get(request.action)
        if not action_func:
            raise HTTPException(status_code=400, detail="Invalid action")
        
        success = await action_func(int(miner_id))
        msg = "Command sent" if success else "Command failed"
    
    logger.info(
        "Manual miner control",
        miner_id=miner_id,
        action=request.action,
        success=success
    )
    
    return {
        "success": success,
        "miner_id": miner_id,
        "action": request.action,
        "message": msg if settings.miner_discovery_enabled else None
    }


# =========================================================================
# Configuration Endpoints
# =========================================================================

@router.get(
    "/config",
    response_model=SystemConfig,
    summary="Get current configuration"
)
async def get_config():
    """Get current runtime configuration."""
    fleet_manager = get_fleet_manager()
    return fleet_manager.config


@router.patch(
    "/config",
    summary="Update configuration"
)
async def update_config(request: ConfigUpdateRequest):
    """Update runtime configuration."""
    fleet_manager = get_fleet_manager()
    
    updates = request.model_dump(exclude_none=True)
    if updates:
        fleet_manager.update_config(**updates)
    
    return {
        "success": True,
        "config": fleet_manager.config
    }


# =========================================================================
# History Endpoints
# =========================================================================

@router.get(
    "/history",
    summary="Get command history"
)
async def get_history(limit: int = 100):
    """Get recent command history."""
    fleet_manager = get_fleet_manager()
    history = fleet_manager.get_command_history(limit)
    
    return {
        "commands": [
            {
                "timestamp": cmd.timestamp.isoformat(),
                "source": cmd.source,
                "command": cmd.command,
                "parameters": cmd.parameters,
                "success": cmd.success,
                "message": cmd.message
            }
            for cmd in history
        ]
    }


# =========================================================================
# Health Check
# =========================================================================

@router.get(
    "/health",
    summary="Dashboard health check"
)
async def health_check():
    """Check health of all services."""
    settings = get_settings()
    fleet_manager = get_fleet_manager()
    
    if settings.miner_discovery_enabled:
        # Direct mode - check if we have miners
        discovery = get_discovery_service()
        miners_count = len(discovery.miners)
        online_count = sum(1 for m in discovery.miners if m.is_online)
        
        return {
            "healthy": online_count > 0 or True,  # Healthy even with no miners (discovery mode)
            "mode": "direct",
            "services": {
                "miner_discovery": True,
                "fleet_manager": True,
                "awesome_miner": "disabled"
            },
            "miners_registered": miners_count,
            "miners_online": online_count,
            "fleet_state": fleet_manager.status.state.value,
            "timestamp": datetime.utcnow().isoformat()
        }
    else:
        # AwesomeMiner mode
        am_client = get_awesome_miner_client()
        am_healthy = await am_client.health_check()
        
        return {
            "healthy": am_healthy,
            "mode": "awesomeminer",
            "services": {
                "awesome_miner": am_healthy,
                "fleet_manager": True,
                "miner_discovery": "disabled"
            },
            "fleet_state": fleet_manager.status.state.value,
            "timestamp": datetime.utcnow().isoformat()
        }


# =========================================================================
# Fan Control Test Endpoints (for testing Vnish fan settings)
# =========================================================================

class FanControlRequest(BaseModel):
    """Request for fan control test."""
    miner_ip: str = Field(..., description="Miner IP address")


# Complete config template for Vnish 3.9.0 - MUST send all params or config corrupts!
VNISH_CONFIG_TEMPLATE = {
    "_ant_pool1url": "stratum+tcp://stratum.slushpool.com:3333",
    "_ant_pool1user": "test.worker1",
    "_ant_pool1pw": "x",
    "_ant_pool2url": "",
    "_ant_pool2user": "",
    "_ant_pool2pw": "",
    "_ant_pool3url": "",
    "_ant_pool3user": "",
    "_ant_pool3pw": "",
    "_ant_nobeeper": "false",
    "_ant_notempoverctrl": "false",
    "_ant_fan_customize_switch": "false",
    "_ant_fan_customize_value": "100",
    "_ant_freq": "550",
    "_ant_freq1": "",
    "_ant_freq2": "",
    "_ant_freq3": "",
    "_ant_voltage": "8.8",
    "_ant_voltage1": "",
    "_ant_voltage2": "",
    "_ant_voltage3": "",
    "_ant_fan_rpm_off": "0",
    "_ant_chip_freq": "",
    "_ant_autodownscale": "false",
    "_ant_autodownscale_watch": "",
    "_ant_autodownscale_watchtimer": "",
    "_ant_autodownscale_timer": "2",
    "_ant_autodownscale_after": "10",
    "_ant_autodownscale_step": "25",
    "_ant_autodownscale_min": "400",
    "_ant_autodownscale_prec": "75",
    "_ant_autodownscale_profile": "1",
    "_ant_minhr": "0",
    "_ant_asicboost": "true",
    "_ant_tempoff": "105",
    "_ant_altdf": "false",
    "_ant_presave": "",
    "_ant_name": "",
    "_ant_warn": "0",
    "_ant_maxx": "0",
    "_ant_trigger_reboot": "0",
    "_ant_target_temp": "80",
    "_ant_silentstart": "false",
    "_ant_altdfno": "0",
    "_ant_autodownscale_reboot": "",
    "_ant_hotel_fee": "false",
    "_ant_lpm_mode": "false",
    "_ant_dchain5": "",
    "_ant_dchain6": "",
    "_ant_dchain7": "",
}


async def _vnish_request(ip: str, endpoint: str, method: str = "GET", data: dict = None) -> dict:
    """Make a request to Vnish Web API with digest auth."""
    import httpx
    
    url = f"http://{ip}{endpoint}"
    auth = httpx.DigestAuth("root", "root")
    
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            if method == "POST":
                response = await client.post(
                    url, 
                    auth=auth, 
                    data=data,
                    headers={"Content-Type": "application/x-www-form-urlencoded"}
                )
            else:
                response = await client.get(url, auth=auth)
            
            if response.status_code == 200:
                try:
                    return {"success": True, "data": response.json()}
                except:
                    return {"success": True, "data": response.text}
            else:
                return {"success": False, "error": f"HTTP {response.status_code}"}
    except httpx.TimeoutException:
        return {"success": False, "error": "Timeout"}
    except Exception as e:
        return {"success": False, "error": str(e)}


@router.post(
    "/fan-test/stop-miner",
    summary="Stop miner (put in idle)"
)
async def fan_test_stop_miner(request: FanControlRequest):
    """
    Stop the miner using stop_bmminer.cgi.
    This stops the cgminer process but keeps the system running.
    """
    result = await _vnish_request(request.miner_ip, "/cgi-bin/stop_bmminer.cgi")
    
    logger.info("Fan test: stop miner", ip=request.miner_ip, result=result)
    return {
        "action": "stop_miner",
        "miner_ip": request.miner_ip,
        **result
    }


@router.post(
    "/fan-test/restart-miner",
    summary="Restart miner (resume mining)"
)
async def fan_test_restart_miner(request: FanControlRequest):
    """
    Restart the miner using reboot_cgminer.cgi.
    This restarts the cgminer process to resume mining.
    """
    # Use short timeout as this endpoint blocks for a long time
    import httpx
    
    url = f"http://{request.miner_ip}/cgi-bin/reboot_cgminer.cgi"
    auth = httpx.DigestAuth("root", "root")
    
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            try:
                await client.get(url, auth=auth)
            except httpx.TimeoutException:
                pass  # Expected - endpoint blocks until cgminer starts
        
        result = {"success": True, "data": "Restart command sent"}
    except Exception as e:
        result = {"success": False, "error": str(e)}
    
    logger.info("Fan test: restart miner", ip=request.miner_ip, result=result)
    return {
        "action": "restart_miner",
        "miner_ip": request.miner_ip,
        **result
    }


@router.post(
    "/fan-test/set-immersion",
    summary="Set immersion mode (disable fan check alarm)"
)
async def fan_test_set_immersion(request: FanControlRequest):
    """
    Enable immersion mode: _ant_fan_rpm_off=1
    NOTE: This only disables the fan CHECK/ALARM - fans still run!
    """
    config = VNISH_CONFIG_TEMPLATE.copy()
    config["_ant_fan_rpm_off"] = "1"
    
    result = await _vnish_request(
        request.miner_ip, 
        "/cgi-bin/set_miner_conf_custom.cgi",
        method="POST",
        data=config
    )
    
    logger.info("Fan test: set immersion mode", ip=request.miner_ip, result=result)
    return {
        "action": "set_immersion",
        "description": "Immersion mode ON (fan check disabled, fans STILL RUN)",
        "miner_ip": request.miner_ip,
        **result
    }


@router.post(
    "/fan-test/set-fan-zero",
    summary="Set fans to 0% PWM"
)
async def fan_test_set_fan_zero(request: FanControlRequest):
    """
    Set fans to 0% using fan_customize_switch=true, fan_customize_value=0.
    This should actually stop the fans (0% PWM duty cycle).
    """
    config = VNISH_CONFIG_TEMPLATE.copy()
    config["_ant_fan_customize_switch"] = "true"
    config["_ant_fan_customize_value"] = "0"
    
    result = await _vnish_request(
        request.miner_ip, 
        "/cgi-bin/set_miner_conf_custom.cgi",
        method="POST",
        data=config
    )
    
    logger.info("Fan test: set fan zero", ip=request.miner_ip, result=result)
    return {
        "action": "set_fan_zero",
        "description": "Fan PWM set to 0%",
        "miner_ip": request.miner_ip,
        **result
    }


@router.post(
    "/fan-test/set-both",
    summary="Set immersion mode AND 0% fan"
)
async def fan_test_set_both(request: FanControlRequest):
    """
    Set both immersion mode AND 0% fan PWM.
    Combines both settings for maximum power saving.
    """
    config = VNISH_CONFIG_TEMPLATE.copy()
    config["_ant_fan_rpm_off"] = "1"
    config["_ant_fan_customize_switch"] = "true"
    config["_ant_fan_customize_value"] = "0"
    
    result = await _vnish_request(
        request.miner_ip, 
        "/cgi-bin/set_miner_conf_custom.cgi",
        method="POST",
        data=config
    )
    
    logger.info("Fan test: set both", ip=request.miner_ip, result=result)
    return {
        "action": "set_both",
        "description": "Immersion mode ON + Fan PWM 0%",
        "miner_ip": request.miner_ip,
        **result
    }


@router.post(
    "/fan-test/reset-fan",
    summary="Reset fan settings to default"
)
async def fan_test_reset_fan(request: FanControlRequest):
    """
    Reset fan settings to default (auto fan control).
    """
    config = VNISH_CONFIG_TEMPLATE.copy()
    config["_ant_fan_rpm_off"] = "0"
    config["_ant_fan_customize_switch"] = "false"
    config["_ant_fan_customize_value"] = "100"
    
    result = await _vnish_request(
        request.miner_ip, 
        "/cgi-bin/set_miner_conf_custom.cgi",
        method="POST",
        data=config
    )
    
    logger.info("Fan test: reset fan settings", ip=request.miner_ip, result=result)
    return {
        "action": "reset_fan",
        "description": "Fan settings reset to auto",
        "miner_ip": request.miner_ip,
        **result
    }


@router.get(
    "/fan-test/status/{miner_ip}",
    summary="Get miner fan status"
)
async def fan_test_get_status(miner_ip: str):
    """
    Get current miner status including fan speed.
    """
    status_result = await _vnish_request(miner_ip, "/cgi-bin/get_miner_status.cgi")
    config_result = await _vnish_request(miner_ip, "/cgi-bin/get_miner_conf.cgi")
    
    return {
        "miner_ip": miner_ip,
        "status": status_result,
        "config": config_result
    }


# =========================================================================
# Discovery Endpoints (Direct Mode Only)
# =========================================================================

class AddMinerRequest(BaseModel):
    """Request to manually add a miner."""
    ip: str
    port: int = 4028
    rated_power_watts: float = 3000.0


class DiscoveryRequest(BaseModel):
    """Request to run discovery."""
    network_cidr: Optional[str] = None


@router.post(
    "/discovery/scan",
    summary="Run miner discovery"
)
async def run_discovery(request: Optional[DiscoveryRequest] = None):
    """
    Scan the network for miners.
    
    Only available in direct mode (miner_discovery_enabled=True).
    """
    settings = get_settings()
    if not settings.miner_discovery_enabled:
        raise HTTPException(
            status_code=400,
            detail="Discovery not available. Set MINER_DISCOVERY_ENABLED=true"
        )
    
    fleet_manager = get_fleet_manager()
    cidr = request.network_cidr if request else None
    
    try:
        count = await fleet_manager.run_discovery()
        discovery = get_discovery_service()
        
        return {
            "success": True,
            "miners_found": count,
            "network_scanned": cidr or settings.miner_network_cidr,
            "miners": [
                {
                    "id": m.id,
                    "ip": m.ip,
                    "model": m.model,
                    "type": m.miner_type.value,
                    "is_online": m.is_online,
                    "is_mining": m.is_mining
                }
                for m in discovery.miners
            ]
        }
    except Exception as e:
        logger.error("Discovery failed", error=str(e))
        return {
            "success": False,
            "error": str(e)
        }


@router.post(
    "/discovery/add",
    summary="Manually add a miner"
)
async def add_miner(request: AddMinerRequest):
    """
    Manually add a miner by IP address.
    
    Only available in direct mode.
    """
    settings = get_settings()
    if not settings.miner_discovery_enabled:
        raise HTTPException(
            status_code=400,
            detail="Discovery not available. Set MINER_DISCOVERY_ENABLED=true"
        )
    
    discovery = get_discovery_service()
    success, miner = await discovery.add_miner(
        ip=request.ip,
        port=request.port,
        rated_power_watts=request.rated_power_watts
    )
    
    if success and miner:
        return {
            "success": True,
            "miner": {
                "id": miner.id,
                "ip": miner.ip,
                "model": miner.model,
                "type": miner.miner_type.value,
                "is_online": miner.is_online,
                "rated_power_watts": miner.rated_power_watts
            }
        }
    else:
        return {
            "success": False,
            "error": f"Could not connect to miner at {request.ip}:{request.port}"
        }


@router.delete(
    "/discovery/miners/{miner_id}",
    summary="Remove a miner"
)
async def remove_miner(miner_id: str):
    """Remove a miner from the registry."""
    settings = get_settings()
    if not settings.miner_discovery_enabled:
        raise HTTPException(
            status_code=400,
            detail="Discovery not available. Set MINER_DISCOVERY_ENABLED=true"
        )
    
    discovery = get_discovery_service()
    success = discovery.remove_miner(miner_id)
    
    return {
        "success": success,
        "miner_id": miner_id
    }


@router.get(
    "/discovery/miners",
    summary="Get all discovered miners"
)
async def get_discovered_miners():
    """Get list of all discovered/registered miners with details."""
    settings = get_settings()
    if not settings.miner_discovery_enabled:
        raise HTTPException(
            status_code=400,
            detail="Discovery not available. Set MINER_DISCOVERY_ENABLED=true"
        )
    
    discovery = get_discovery_service()
    
    return {
        "miners": [
            {
                "id": m.id,
                "ip": m.ip,
                "port": m.port,
                "model": m.model,
                "type": m.miner_type.value,
                "firmware_type": m.firmware_type.value if hasattr(m, 'firmware_type') else "unknown",
                "firmware_version": m.firmware_version if hasattr(m, 'firmware_version') else "",
                "is_online": m.is_online,
                "is_mining": m.is_mining,
                "hashrate_ghs": m.hashrate_ghs,
                "power_watts": m.power_watts,
                "power_kw": m.power_kw,
                "rated_power_watts": m.rated_power_watts,
                "rated_power_kw": m.rated_power_kw,
                "temperature_c": m.temperature_c,
                "fan_speed_pct": m.fan_speed_pct,
                "power_mode": m.power_mode.value,
                "pool_url": m.pool_url,
                "uptime_seconds": m.uptime_seconds,
                "last_seen": m.last_seen.isoformat(),
                "consecutive_failures": m.consecutive_failures
            }
            for m in discovery.miners
        ],
        "total": len(discovery.miners),
        "online": sum(1 for m in discovery.miners if m.is_online),
        "mining": sum(1 for m in discovery.miners if m.is_mining)
    }


# =========================================================================
# Comprehensive Miner Details Endpoint
# =========================================================================

@router.get(
    "/miner/{miner_ip}/details",
    summary="Get comprehensive miner details"
)
async def get_miner_details(miner_ip: str):
    """
    Fetch comprehensive details from a miner including:
    - Full status from get_miner_status.cgi
    - System info from get_system_info.cgi
    - Current config from get_miner_conf.cgi
    - Per-board statistics
    - Share statistics
    - Pool details
    """
    import httpx
    from httpx import DigestAuth
    
    auth = DigestAuth("root", "root")
    base_url = f"http://{miner_ip}"
    
    result = {
        "ip": miner_ip,
        "status": None,
        "system": None,
        "config": None,
        "boards": [],
        "pools": [],
        "shares": {
            "accepted": 0,
            "rejected": 0,
            "stale": 0,
            "hw_errors": 0,
            "reject_rate": 0.0
        },
        "summary": {},
        "error": None
    }
    
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            # Fetch all three endpoints in parallel
            status_task = client.get(f"{base_url}/cgi-bin/get_miner_status.cgi", auth=auth)
            system_task = client.get(f"{base_url}/cgi-bin/get_system_info.cgi", auth=auth)
            config_task = client.get(f"{base_url}/cgi-bin/get_miner_conf.cgi", auth=auth)
            
            import asyncio
            responses = await asyncio.gather(
                status_task, system_task, config_task,
                return_exceptions=True
            )
            
            # Process status response
            if not isinstance(responses[0], Exception) and responses[0].status_code == 200:
                try:
                    status_data = responses[0].json()
                    result["status"] = status_data
                    
                    # Vnish 3.9.x format uses lowercase keys: summary, pools, devs
                    # CGMiner format uses uppercase: STATUS, POOLS, STATS
                    
                    # Extract summary data (try both formats)
                    summary_data = status_data.get("summary") or status_data.get("STATUS", {})
                    if isinstance(summary_data, list) and len(summary_data) > 0:
                        summary_data = summary_data[0]
                    
                    if summary_data:
                        # Parse Vnish format (lowercase)
                        result["summary"] = {
                            "elapsed": summary_data.get("elapsed", summary_data.get("Elapsed", 0)),
                            "hashrate_ghs_5s": float(summary_data.get("ghs5s", summary_data.get("GHS 5s", 0)) or 0),
                            "hashrate_ghs_avg": float(summary_data.get("ghsav", summary_data.get("GHS av", 0)) or 0),
                            "accepted": summary_data.get("accepted", summary_data.get("Accepted", 0)),
                            "rejected": summary_data.get("rejected", summary_data.get("Rejected", 0)),
                            "stale": summary_data.get("stale", summary_data.get("Stale", 0)),
                            "hw_errors": summary_data.get("hw", summary_data.get("Hardware Errors", 0)),
                            "utility": summary_data.get("utility", summary_data.get("Utility", 0)),
                            "best_share": summary_data.get("bestshare", summary_data.get("Best Share", 0)),
                        }
                        
                        # Calculate share stats
                        accepted = result["summary"]["accepted"]
                        rejected = result["summary"]["rejected"]
                        stale = result["summary"]["stale"]
                        hw_errors = result["summary"]["hw_errors"]
                        result["shares"]["accepted"] = accepted
                        result["shares"]["rejected"] = rejected
                        result["shares"]["stale"] = stale
                        result["shares"]["hw_errors"] = hw_errors
                        if accepted + rejected > 0:
                            result["shares"]["reject_rate"] = round(rejected / (accepted + rejected) * 100, 2)
                    
                    # Extract per-board stats from devs (Vnish) or STATS (CGMiner)
                    devs_data = status_data.get("devs") or status_data.get("STATS", [])
                    if devs_data and isinstance(devs_data, list):
                        for dev in devs_data:
                            # Vnish devs format
                            board = {
                                "id": int(dev.get("index", 0)) - 5,  # Vnish uses 6,7,8 for boards 1,2,3
                                "hashrate_ghs": float(dev.get("rate", 0) or 0),
                                "chip_temp": int(dev.get("temp2", dev.get("temp", 0)) or 0),
                                "pcb_temp": int(dev.get("temp", 0) or 0),
                                "power_watts": float(dev.get("chain_consumption", 0) or 0),
                                "frequency_mhz": int(dev.get("freq", dev.get("freqavg", 0)) or 0),
                                "voltage_mv": int(dev.get("chain_vol", 0) or 0),
                                "chips_total": int(dev.get("chain_acn", 0) or 0),
                                "chips_ok": int(dev.get("chain_acn", 0) or 0),
                                "hw_errors": int(dev.get("hw", 0) or 0),
                                "chip_status": dev.get("chain_acs", "").strip(),
                                "status": "healthy"
                            }
                            
                            # Calculate active chips from chain_acs string
                            chip_status = board["chip_status"]
                            if chip_status:
                                board["chips_ok"] = chip_status.count("o") + chip_status.count("O")
                                if board["chips_ok"] < board["chips_total"]:
                                    board["status"] = "warning"
                                if board["chips_ok"] == 0:
                                    board["status"] = "error"
                            
                            # Check temperature status
                            if board["chip_temp"] > 85:
                                board["status"] = "warning"
                            if board["chip_temp"] > 95:
                                board["status"] = "error"
                            
                            result["boards"].append(board)
                        
                        # Get fan info from first dev
                        if devs_data:
                            first_dev = devs_data[0]
                            result["summary"]["fans"] = []
                            for i in range(1, 9):
                                rpm = int(first_dev.get(f"fan{i}", 0) or 0)
                                if rpm > 0:
                                    result["summary"]["fans"].append({"id": i, "rpm": rpm})
                            
                            # Get overall frequency from first dev
                            result["summary"]["frequency_mhz"] = int(first_dev.get("freq", first_dev.get("freqavg", 0)) or 0)
                            result["summary"]["voltage_mv"] = int(first_dev.get("chain_vol", 0) or 0)
                    
                    # Extract pool data (try both formats)
                    pools_data = status_data.get("pools") or status_data.get("POOLS", [])
                    if pools_data:
                        for pool in pools_data:
                            # Skip DevFee pools
                            if pool.get("user") == "DevFee" or pool.get("url") == "DevFee":
                                continue
                            pool_info = {
                                "id": pool.get("index", pool.get("POOL", 0)),
                                "url": pool.get("url", pool.get("URL", "")),
                                "worker": pool.get("user", pool.get("User", "")),
                                "status": pool.get("status", pool.get("Status", "Unknown")),
                                "priority": pool.get("priority", pool.get("Priority", 0)),
                                "accepted": pool.get("accepted", pool.get("Accepted", 0)),
                                "rejected": pool.get("rejected", pool.get("Rejected", 0)),
                                "stale": pool.get("stale", pool.get("Stale", 0)),
                                "discarded": pool.get("discarded", pool.get("Discarded", 0)),
                                "difficulty": pool.get("diff", pool.get("Diff", "")),
                                "best_share": pool.get("bestshare", pool.get("Best Share", 0)),
                                "last_share_time": pool.get("lstime", pool.get("Last Share Time", "")),
                                "getworks": pool.get("getworks", pool.get("Getworks", 0)),
                                "stratum_active": pool.get("status", pool.get("Status", "")) == "Alive",
                            }
                            result["pools"].append(pool_info)
                            
                except Exception as e:
                    logger.warning("miner_details_status_parse_error", ip=miner_ip, error=str(e))
            
            # Process system info response
            if not isinstance(responses[1], Exception) and responses[1].status_code == 200:
                try:
                    system_data = responses[1].json()
                    result["system"] = {
                        "minertype": system_data.get("minertype", ""),
                        "hostname": system_data.get("hostname", ""),
                        "macaddr": system_data.get("macaddr", ""),
                        "ipaddress": system_data.get("ipaddress", miner_ip),
                        "netmask": system_data.get("netmask", ""),
                        "gateway": system_data.get("gateway", ""),
                        "dnsservers": system_data.get("dnsservers", ""),
                        "nettype": system_data.get("nettype", ""),
                        "firmware_version": system_data.get("file_system_version", ""),
                        "kernel_version": system_data.get("system_kernel_version", ""),
                        "hardware_version": system_data.get("ant_hwv", ""),
                        "system_uptime": system_data.get("elapsed", 0)
                    }
                except Exception as e:
                    logger.warning("miner_details_system_parse_error", ip=miner_ip, error=str(e))
            
            # Process config response
            if not isinstance(responses[2], Exception) and responses[2].status_code == 200:
                try:
                    config_data = responses[2].json()
                    
                    # Helper to safely get config values (some miners have corrupted configs)
                    def safe_config_get(key: str, default=""):
                        val = config_data.get(key, default)
                        if val is None:
                            return default
                        # Check for corrupted values containing _ant_ prefix garbage
                        if isinstance(val, str) and ("_ant_" in val or val.startswith("_")):
                            return default
                        return val
                    
                    result["config"] = {
                        "pools": config_data.get("pools", []),
                        "fan_ctrl": config_data.get("bitmain-fan-ctrl", False),
                        "fan_pwm": safe_config_get("bitmain-fan-pwm", "100"),
                        "frequency": safe_config_get("bitmain-freq", ""),
                        "frequency1": safe_config_get("bitmain-freq1", ""),
                        "frequency2": safe_config_get("bitmain-freq2", ""),
                        "frequency3": safe_config_get("bitmain-freq3", ""),
                        "voltage": safe_config_get("bitmain-voltage", ""),
                        "voltage1": safe_config_get("bitmain-voltage1", ""),
                        "voltage2": safe_config_get("bitmain-voltage2", ""),
                        "voltage3": safe_config_get("bitmain-voltage3", ""),
                        "target_temp": safe_config_get("bitmain-target-temp", "75"),
                        "shutdown_temp": safe_config_get("bitmain-tempoff", "105"),
                        "fan_rpm_off": safe_config_get("bitmain-fan-rpm-off", "0"),
                        "asicboost": config_data.get("asicboost", True),
                        "beeper": not config_data.get("bitmain-nobeeper", False),
                        "autodownscale": {
                            "enabled": config_data.get("bitmain-autodownscale", False),
                            "timer": safe_config_get("bitmain-autodownscale-timer", "2"),
                            "after": safe_config_get("bitmain-autodownscale-after", "10"),
                            "step": safe_config_get("bitmain-autodownscale-step", "25"),
                            "min": safe_config_get("bitmain-autodownscale-min", "400")
                        }
                    }
                except Exception as e:
                    logger.warning("miner_details_config_parse_error", ip=miner_ip, error=str(e))
        
        return result
        
    except Exception as e:
        logger.error("miner_details_fetch_error", ip=miner_ip, error=str(e))
        result["error"] = str(e)
        return result


# =========================================================================
# Miner Configuration Update Endpoint
# =========================================================================

class MinerConfigUpdateRequest(BaseModel):
    """Request to update miner configuration."""
    frequency: Optional[int] = Field(None, ge=400, le=700, description="Global frequency MHz")
    frequency1: Optional[int] = Field(None, ge=400, le=700, description="Board 1 frequency MHz")
    frequency2: Optional[int] = Field(None, ge=400, le=700, description="Board 2 frequency MHz")
    frequency3: Optional[int] = Field(None, ge=400, le=700, description="Board 3 frequency MHz")
    voltage: Optional[int] = Field(None, ge=800, le=950, description="Global voltage mV")
    voltage1: Optional[int] = Field(None, ge=800, le=950, description="Board 1 voltage mV")
    voltage2: Optional[int] = Field(None, ge=800, le=950, description="Board 2 voltage mV")
    voltage3: Optional[int] = Field(None, ge=800, le=950, description="Board 3 voltage mV")
    fan_mode: Optional[str] = Field(None, pattern="^(auto|manual)$", description="Fan control mode")
    fan_pwm: Optional[int] = Field(None, ge=0, le=100, description="Fan PWM % (when manual)")
    target_temp: Optional[int] = Field(None, ge=60, le=90, description="Target temperature °C")
    shutdown_temp: Optional[int] = Field(None, ge=95, le=125, description="Shutdown temperature °C")
    asicboost: Optional[bool] = Field(None, description="Enable ASICBoost")
    beeper: Optional[bool] = Field(None, description="Enable beeper/buzzer")
    autodownscale_enabled: Optional[bool] = Field(None, description="Enable auto-downscale")
    autodownscale_step: Optional[int] = Field(None, ge=10, le=100, description="Auto-downscale step MHz")
    autodownscale_min: Optional[int] = Field(None, ge=300, le=600, description="Auto-downscale min freq MHz")


@router.post(
    "/miner/{miner_ip}/config",
    summary="Update miner configuration"
)
async def update_miner_config(miner_ip: str, request: MinerConfigUpdateRequest):
    """
    Update miner configuration settings including:
    - Frequency and voltage (global or per-board)
    - Fan control mode and PWM
    - Temperature targets
    - ASICBoost
    - Auto-downscale settings
    """
    import httpx
    from httpx import DigestAuth
    
    auth = DigestAuth("root", "root")
    base_url = f"http://{miner_ip}"
    
    logger.info("miner_config_update_requested", ip=miner_ip, config=request.dict(exclude_none=True))
    
    # First, get current config to preserve unchanged settings
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(
                f"{base_url}/cgi-bin/get_miner_conf.cgi",
                auth=auth
            )
            
            if response.status_code != 200:
                return {
                    "success": False,
                    "error": f"Could not read current config: HTTP {response.status_code}"
                }
            
            current_config = response.json()
    except Exception as e:
        return {
            "success": False,
            "error": f"Could not read current config: {str(e)}"
        }
    
    # Helper to get safe config values (config may be corrupted with _ant_ prefixes)
    def safe_get(key: str, default: str) -> str:
        val = current_config.get(key, default)
        # If the value looks corrupted (contains _ant_), use default instead
        if isinstance(val, str) and "_ant_" in val:
            return default
        return str(val) if val else default
    
    # Build config data, preserving current values where not specified
    pools = current_config.get("pools", [{"url": "", "user": "", "pass": ""}, {"url": "", "user": "", "pass": ""}, {"url": "", "user": "", "pass": ""}])
    while len(pools) < 3:
        pools.append({"url": "", "user": "", "pass": ""})
    
    config_data = {
        # Pool settings (preserve current)
        "_ant_pool1url": pools[0].get("url", ""),
        "_ant_pool1user": pools[0].get("user", ""),
        "_ant_pool1pw": pools[0].get("pass", "x"),
        "_ant_pool2url": pools[1].get("url", "") if len(pools) > 1 else "",
        "_ant_pool2user": pools[1].get("user", "") if len(pools) > 1 else "",
        "_ant_pool2pw": pools[1].get("pass", "x") if len(pools) > 1 else "",
        "_ant_pool3url": pools[2].get("url", "") if len(pools) > 2 else "",
        "_ant_pool3user": pools[2].get("user", "") if len(pools) > 2 else "",
        "_ant_pool3pw": pools[2].get("pass", "x") if len(pools) > 2 else "",
        
        # Frequency settings (use safe_get to handle corrupted config)
        "_ant_freq": str(request.frequency) if request.frequency else safe_get("bitmain-freq", "550"),
        "_ant_freq1": str(request.frequency1) if request.frequency1 else safe_get("bitmain-freq1", ""),
        "_ant_freq2": str(request.frequency2) if request.frequency2 else safe_get("bitmain-freq2", ""),
        "_ant_freq3": str(request.frequency3) if request.frequency3 else safe_get("bitmain-freq3", ""),
        
        # Voltage settings
        "_ant_voltage": str(request.voltage / 100) if request.voltage else safe_get("bitmain-voltage", "8.8"),
        "_ant_voltage1": str(request.voltage1 / 100) if request.voltage1 else safe_get("bitmain-voltage1", ""),
        "_ant_voltage2": str(request.voltage2 / 100) if request.voltage2 else safe_get("bitmain-voltage2", ""),
        "_ant_voltage3": str(request.voltage3 / 100) if request.voltage3 else safe_get("bitmain-voltage3", ""),
        
        # Fan settings
        "_ant_fan_customize_switch": "true" if request.fan_mode == "manual" else (
            "false" if request.fan_mode == "auto" else safe_get("bitmain-fan-ctrl", "false")
        ),
        "_ant_fan_customize_value": str(request.fan_pwm) if request.fan_pwm is not None else safe_get("bitmain-fan-pwm", "100"),
        "_ant_fan_rpm_off": safe_get("bitmain-fan-rpm-off", "0"),
        
        # Temperature settings
        "_ant_target_temp": str(request.target_temp) if request.target_temp else safe_get("bitmain-target-temp", "75"),
        "_ant_tempoff": str(request.shutdown_temp) if request.shutdown_temp else safe_get("bitmain-tempoff", "105"),
        
        # ASICBoost
        "_ant_asicboost": "true" if request.asicboost else (
            "false" if request.asicboost is False else safe_get("asicboost", "true")
        ),
        
        # Beeper
        "_ant_nobeeper": "false" if request.beeper else (
            "true" if request.beeper is False else safe_get("bitmain-nobeeper", "false")
        ),
        
        # Auto-downscale
        "_ant_autodownscale": "true" if request.autodownscale_enabled else (
            "false" if request.autodownscale_enabled is False else safe_get("bitmain-autodownscale", "false")
        ),
        "_ant_autodownscale_step": str(request.autodownscale_step) if request.autodownscale_step else safe_get("bitmain-autodownscale-step", "25"),
        "_ant_autodownscale_min": str(request.autodownscale_min) if request.autodownscale_min else safe_get("bitmain-autodownscale-min", "400"),
        "_ant_autodownscale_timer": safe_get("bitmain-autodownscale-timer", "2"),
        "_ant_autodownscale_after": safe_get("bitmain-autodownscale-after", "10"),
        "_ant_autodownscale_prec": safe_get("bitmain-autodownscale-prec", "75"),
        "_ant_autodownscale_profile": safe_get("bitmain-autodownscale-profile", "1"),
        "_ant_autodownscale_hw": safe_get("bitmain-autodownscale-hw", "0"),
        
        # Other preserved settings
        "_ant_notempoverctrl": safe_get("bitmain-notempoverctrl", "false"),
        "_ant_chip_freq": safe_get("bitmain-chip-freq", ""),
        "_ant_minhr": safe_get("bitmain-minhr", "0"),
        "_ant_maxx": safe_get("bitmain-maxx", "0"),
        "_ant_trigger_reboot": safe_get("bitmain-trigger-reboot", "0"),
    }
    
    # Apply the config
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                f"{base_url}/cgi-bin/set_miner_conf_custom.cgi",
                auth=auth,
                data=config_data,
                headers={"Content-Type": "application/x-www-form-urlencoded"}
            )
            
            if response.status_code == 200 and response.text.strip() == "ok":
                logger.info("miner_config_update_success", ip=miner_ip)
                return {
                    "success": True,
                    "message": "Configuration updated successfully"
                }
            else:
                logger.error("miner_config_update_failed", ip=miner_ip, status=response.status_code, response=response.text[:200])
                return {
                    "success": False,
                    "error": f"Failed to update config: {response.text[:100]}"
                }
    except Exception as e:
        logger.error("miner_config_update_exception", ip=miner_ip, error=str(e))
        return {
            "success": False,
            "error": f"Config update failed: {str(e)}"
        }


# =========================================================================
# Vnish Advanced Data Endpoints (Chip Hashrate, Firmware Detection)
# =========================================================================

@router.get(
    "/miner/{miner_ip}/chip-hashrate",
    summary="Get per-chip hashrate data"
)
async def get_chip_hashrate(miner_ip: str):
    """
    Fetch per-chip hashrate data from Vnish firmware.
    
    Returns detailed hashrate for each ASIC chip on all hashboards.
    Useful for identifying underperforming or failing chips.
    
    Response structure:
    {
        "boards": [
            {
                "id": 1,
                "chips": [
                    {"index": 0, "hashrate_mhs": 69, "status": "ok"},
                    {"index": 1, "hashrate_mhs": 67, "status": "ok"},
                    ...
                ],
                "avg_hashrate_mhs": 68.5,
                "min_hashrate_mhs": 45,
                "max_hashrate_mhs": 72,
                "bad_chips": 2
            },
            ...
        ],
        "total_chips": 189,
        "healthy_chips": 187,
        "warning_chips": 2,
        "dead_chips": 0
    }
    """
    import httpx
    from httpx import DigestAuth
    
    auth = DigestAuth("root", "root")
    
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(
                f"http://{miner_ip}/cgi-bin/chip_hr.json",
                auth=auth
            )
            
            if response.status_code != 200:
                return {
                    "success": False,
                    "error": f"Could not fetch chip hashrate: HTTP {response.status_code}",
                    "firmware_support": False
                }
            
            data = response.json()
            chip_hr_data = data.get("chiphr", [])
            
            # Process the chip data
            boards = []
            total_chips = 0
            healthy_chips = 0
            warning_chips = 0
            dead_chips = 0
            
            for board_idx, board_data in enumerate(chip_hr_data):
                chips = []
                hashrates = []
                
                # Parse Asic00, Asic01, ... AsicNN format
                for key, value in sorted(board_data.items()):
                    if key.startswith("Asic"):
                        try:
                            chip_idx = int(key.replace("Asic", ""))
                            hr = int(value) if value else 0
                            hashrates.append(hr)
                            
                            # Determine chip status based on hashrate
                            # Typical good S9 chip: 60-75 MH/s
                            status = "ok"
                            if hr == 0:
                                status = "dead"
                                dead_chips += 1
                            elif hr < 40:
                                status = "warning"
                                warning_chips += 1
                            else:
                                healthy_chips += 1
                            
                            chips.append({
                                "index": chip_idx,
                                "hashrate_mhs": hr,
                                "status": status
                            })
                            total_chips += 1
                        except (ValueError, TypeError):
                            pass
                
                # Calculate board stats
                if hashrates:
                    non_zero_rates = [h for h in hashrates if h > 0]
                    boards.append({
                        "id": board_idx + 1,
                        "chips": chips,
                        "chip_count": len(chips),
                        "avg_hashrate_mhs": round(sum(non_zero_rates) / len(non_zero_rates), 1) if non_zero_rates else 0,
                        "min_hashrate_mhs": min(non_zero_rates) if non_zero_rates else 0,
                        "max_hashrate_mhs": max(non_zero_rates) if non_zero_rates else 0,
                        "total_hashrate_ghs": round(sum(hashrates) / 1000, 2),
                        "bad_chips": sum(1 for c in chips if c["status"] != "ok")
                    })
            
            return {
                "success": True,
                "firmware_support": True,
                "boards": boards,
                "total_chips": total_chips,
                "healthy_chips": healthy_chips,
                "warning_chips": warning_chips,
                "dead_chips": dead_chips,
                "health_percent": round(healthy_chips / total_chips * 100, 1) if total_chips > 0 else 0
            }
            
    except Exception as e:
        logger.warning("chip_hashrate_fetch_error", ip=miner_ip, error=str(e))
        return {
            "success": False,
            "error": str(e),
            "firmware_support": False
        }


@router.get(
    "/miner/{miner_ip}/firmware-info",
    summary="Get firmware type and version"
)
async def get_firmware_info(miner_ip: str):
    """
    Detect firmware type (Stock, Vnish, BraiinsOS, etc.) and version.
    
    Checks various endpoints to determine firmware:
    - Vnish: /cgi-bin/get_system_info.cgi has vnish-specific fields
    - BraiinsOS: /api/bos/ endpoints
    - Stock: Standard CGMiner responses only
    """
    import httpx
    from httpx import DigestAuth
    
    auth = DigestAuth("root", "root")
    result = {
        "firmware_type": "unknown",
        "firmware_version": "",
        "firmware_features": [],
        "detected_by": ""
    }
    
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            # Try Vnish system info first
            try:
                response = await client.get(
                    f"http://{miner_ip}/cgi-bin/get_system_info.cgi",
                    auth=auth
                )
                if response.status_code == 200:
                    data = response.json()
                    fw_version = data.get("file_system_version", "")
                    
                    # Check for Vnish signature
                    if "vnish" in fw_version.lower() or "Vnish" in fw_version:
                        result["firmware_type"] = "vnish"
                        result["firmware_version"] = fw_version
                        result["detected_by"] = "system_info"
                        result["firmware_features"] = [
                            "chip_hashrate",
                            "auto_scaling",
                            "profile_switching",
                            "sleep_mode",
                            "per_board_tuning"
                        ]
                        return result
                    elif "braiins" in fw_version.lower() or "bos" in fw_version.lower():
                        result["firmware_type"] = "braiins"
                        result["firmware_version"] = fw_version
                        result["detected_by"] = "system_info"
                        result["firmware_features"] = ["autotuning", "devfee_redirect"]
                        return result
                    elif "marathon" in fw_version.lower():
                        result["firmware_type"] = "marathon"
                        result["firmware_version"] = fw_version
                        result["detected_by"] = "system_info"
                        return result
                    else:
                        # Has system info but not Vnish - could be stock with custom
                        result["firmware_type"] = "stock"
                        result["firmware_version"] = fw_version
                        result["detected_by"] = "system_info"
                        return result
            except:
                pass
            
            # Try BraiinsOS API
            try:
                response = await client.get(f"http://{miner_ip}/api/bos/info")
                if response.status_code == 200:
                    data = response.json()
                    result["firmware_type"] = "braiins"
                    result["firmware_version"] = data.get("version", "")
                    result["detected_by"] = "bos_api"
                    result["firmware_features"] = ["autotuning", "devfee_redirect"]
                    return result
            except:
                pass
            
            # Try chip_hr.json (Vnish-specific)
            try:
                response = await client.get(
                    f"http://{miner_ip}/cgi-bin/chip_hr.json",
                    auth=auth
                )
                if response.status_code == 200:
                    # Has chip hashrate = Vnish
                    result["firmware_type"] = "vnish"
                    result["detected_by"] = "chip_hr_endpoint"
                    result["firmware_features"] = [
                        "chip_hashrate",
                        "auto_scaling",
                        "profile_switching"
                    ]
                    return result
            except:
                pass
            
            # Default to stock
            result["firmware_type"] = "stock"
            result["detected_by"] = "fallback"
            
    except Exception as e:
        logger.warning("firmware_detection_error", ip=miner_ip, error=str(e))
        result["error"] = str(e)
    
    return result


@router.get(
    "/miner/{miner_ip}/autofreq-log",
    summary="Get auto-frequency tuning log"
)
async def get_autofreq_log(miner_ip: str):
    """
    Get the auto-frequency tuning log from Vnish firmware.
    
    Shows timestamped entries of frequency adjustments,
    pool connections, and other tuning events.
    """
    import httpx
    from httpx import DigestAuth
    
    auth = DigestAuth("root", "root")
    
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(
                f"http://{miner_ip}/cgi-bin/get_autofreq_log.cgi",
                auth=auth
            )
            
            if response.status_code != 200:
                return {
                    "success": False,
                    "error": f"HTTP {response.status_code}"
                }
            
            # Parse log entries
            try:
                data = response.json()
                log_text = data.get("response", "") if isinstance(data, dict) else str(data)
            except:
                log_text = response.text
            
            # Parse log lines
            lines = log_text.strip().split("\n") if log_text else []
            entries = []
            for line in lines:
                if line.strip():
                    entries.append({"text": line.strip()})
            
            return {
                "success": True,
                "log_entries": entries,
                "raw_log": log_text
            }
            
    except Exception as e:
        return {
            "success": False,
            "error": str(e)
        }


# =========================================================================
# Pool Configuration Endpoint
# =========================================================================

class PoolUpdateRequest(BaseModel):
    """Request to update pool settings on a miner."""
    miner_ip: str = Field(..., description="Miner IP address")
    pool_url: str = Field(..., description="Pool stratum URL")
    worker: str = Field(..., description="Worker name")
    password: str = Field(default="x", description="Pool password")


@router.post(
    "/pool/update",
    summary="Update pool settings on a miner"
)
async def update_pool_settings(request: PoolUpdateRequest):
    """
    Update pool configuration on a Vnish 3.9.x miner.
    
    Uses set_miner_conf_custom.cgi with complete config including pools.
    This endpoint requires sending the full config body.
    """
    import httpx
    from httpx import DigestAuth
    
    logger.info("pool_update_requested", miner=request.miner_ip, pool=request.pool_url)
    
    # Get current config first to preserve other settings
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(
                f"http://{request.miner_ip}/cgi-bin/get_miner_conf.cgi",
                auth=DigestAuth("root", "root")
            )
            
            if response.status_code != 200:
                return {
                    "success": False,
                    "error": f"Could not read miner config: HTTP {response.status_code}"
                }
            
            current_config = response.json()
    except Exception as e:
        logger.error("pool_update_read_failed", error=str(e))
        return {
            "success": False,
            "error": f"Could not read miner config: {str(e)}"
        }
    
    # Helper to get safe config values (config may be corrupted with _ant_ prefixes)
    def safe_get(key: str, default: str) -> str:
        val = current_config.get(key, default)
        if isinstance(val, str) and "_ant_" in val:
            return default
        return str(val) if val else default
    
    # Build full config for Vnish 3.9.x set_miner_conf_custom.cgi
    # This endpoint needs the _ant_ prefixed field names
    config_data = {
        # Pool settings
        "_ant_pool1url": request.pool_url,
        "_ant_pool1user": request.worker,
        "_ant_pool1pw": request.password,
        "_ant_pool2url": "",
        "_ant_pool2user": "",
        "_ant_pool2pw": "",
        "_ant_pool3url": "",
        "_ant_pool3user": "",
        "_ant_pool3pw": "",
        # Preserve existing settings or use safe defaults
        "_ant_nobeeper": "false",
        "_ant_notempoverctrl": "false",
        "_ant_fan_customize_switch": safe_get("bitmain-fan-ctrl", "false"),
        "_ant_fan_customize_value": safe_get("bitmain-fan-pwm", "100"),
        "_ant_freq": safe_get("bitmain-freq", "550"),
        "_ant_freq1": safe_get("bitmain-freq1", ""),
        "_ant_freq2": safe_get("bitmain-freq2", ""),
        "_ant_freq3": safe_get("bitmain-freq3", ""),
        "_ant_voltage": safe_get("bitmain-voltage", "8.8"),
        "_ant_voltage1": safe_get("bitmain-voltage1", ""),
        "_ant_voltage2": safe_get("bitmain-voltage2", ""),
        "_ant_voltage3": safe_get("bitmain-voltage3", ""),
        "_ant_fan_rpm_off": safe_get("bitmain-fan-rpm-off", "0"),
        "_ant_chip_freq": safe_get("bitmain-chip-freq", ""),
        "_ant_minhr": safe_get("bitmain-minhr", "0"),
        "_ant_asicboost": safe_get("asicboost", "true"),
        "_ant_tempoff": safe_get("bitmain-tempoff", "105"),
        "_ant_target_temp": safe_get("bitmain-target-temp", "75"),
        "_ant_trigger_reboot": safe_get("bitmain-trigger-reboot", "0"),
        "_ant_autodownscale_timer": safe_get("bitmain-autodownscale-timer", "2"),
        "_ant_autodownscale_after": safe_get("bitmain-autodownscale-after", "10"),
        "_ant_autodownscale_step": safe_get("bitmain-autodownscale-step", "25"),
        "_ant_autodownscale_min": safe_get("bitmain-autodownscale-min", "400"),
        "_ant_autodownscale_prec": safe_get("bitmain-autodownscale-prec", "75"),
        "_ant_autodownscale_profile": safe_get("bitmain-autodownscale-profile", "1"),
        "_ant_autodownscale_hw": safe_get("bitmain-autodownscale-hw", "0"),
        "_ant_maxx": safe_get("bitmain-maxx", "0"),
    }
    
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                f"http://{request.miner_ip}/cgi-bin/set_miner_conf_custom.cgi",
                auth=DigestAuth("root", "root"),
                data=config_data,  # Form data
                headers={"Content-Type": "application/x-www-form-urlencoded"}
            )
            
            if response.status_code == 200 and response.text.strip() == "ok":
                logger.info("pool_update_success", miner=request.miner_ip)
                return {
                    "success": True,
                    "message": f"Pool updated to {request.pool_url}"
                }
            else:
                logger.error("pool_update_failed", status=response.status_code, response=response.text[:200])
                return {
                    "success": False,
                    "error": f"Failed to update pool: {response.text[:100]}"
                }
                
    except Exception as e:
        logger.error("pool_update_exception", error=str(e))
        return {
            "success": False,
            "error": f"Pool update failed: {str(e)}"
        }

