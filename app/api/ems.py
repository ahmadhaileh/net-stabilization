"""
EMS API Routes.

These routes implement the EMS protocol specification for third-party device integration.
All endpoints are exposed under /api/ prefix.
"""
from fastapi import APIRouter, HTTPException, status
from fastapi.responses import JSONResponse
import structlog

from app.models.ems import (
    StatusResponse,
    ActivateRequest,
    DeactivateRequest,
    CommandResponse,
    RunningStatus
)
from app.services.fleet_manager import get_fleet_manager

logger = structlog.get_logger()

router = APIRouter(prefix="/api", tags=["EMS Protocol"])


@router.get(
    "/status",
    response_model=StatusResponse,
    summary="Get fleet operational status",
    description="""
    Retrieves the real-time operational state of the mining fleet.
    
    This endpoint is called periodically by the EMS (e.g., every 1–5 seconds).
    Response time must be ≤ 1 second.
    """,
    responses={
        200: {
            "description": "Status successfully retrieved",
            "content": {
                "application/json": {
                    "example": {
                        "isAvailableForDispatch": True,
                        "runningStatus": 2,
                        "ratedPowerInKw": 500.0,
                        "activePowerInKw": 450.0
                    }
                }
            }
        },
        503: {
            "description": "Fleet is offline or temporarily not reachable",
            "content": {
                "application/json": {
                    "example": {
                        "accepted": False,
                        "message": "Fleet is currently unavailable"
                    }
                }
            }
        },
        500: {
            "description": "Internal server error",
            "content": {
                "application/json": {
                    "example": {
                        "accepted": False,
                        "message": "Internal error occurred"
                    }
                }
            }
        }
    }
)
async def get_status():
    """
    Get the current operational status of the mining fleet.
    
    Returns the aggregated status including:
    - Availability for dispatch
    - Running status (1=StandBy, 2=Running)
    - Rated power capacity (kW)
    - Current active power consumption (kW)
    """
    try:
        fleet_manager = get_fleet_manager()
        status = fleet_manager.status
        
        # Log for debugging
        logger.debug(
            "Status request",
            available=status.is_available_for_dispatch,
            running_status=status.running_status,
            active_power=status.active_power_kw
        )
        
        return StatusResponse(
            is_available_for_dispatch=status.is_available_for_dispatch,
            running_status=status.running_status.value,
            rated_power_in_kw=round(status.rated_power_kw, 2),
            active_power_in_kw=round(status.active_power_kw, 2)
        )
        
    except Exception as e:
        logger.error("Failed to get status", error=str(e))
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={
                "accepted": False,
                "message": f"Fleet status unavailable: {str(e)}"
            }
        )


@router.post(
    "/activate",
    response_model=CommandResponse,
    summary="Activate fleet at specified power",
    description="""
    Requests the fleet to start operation at a specified activation power.
    
    The EMS uses this endpoint when it needs the fleet to consume a given
    amount of power. Response time must be ≤ 2 seconds.
    """,
    responses={
        200: {
            "description": "Activation command accepted",
            "content": {
                "application/json": {
                    "example": {
                        "accepted": True,
                        "message": "Fleet activated successfully."
                    }
                }
            }
        },
        400: {
            "description": "Invalid request (e.g., invalid power value)",
            "content": {
                "application/json": {
                    "example": {
                        "accepted": False,
                        "message": "Requested power exceeds rated limits."
                    }
                }
            }
        },
        409: {
            "description": "Fleet in fault state or not available",
            "content": {
                "application/json": {
                    "example": {
                        "accepted": False,
                        "message": "Fleet is in fault state."
                    }
                }
            }
        },
        500: {
            "description": "Internal server error"
        }
    }
)
async def activate(request: ActivateRequest):
    """
    Activate the mining fleet at the requested power level.
    
    The fleet will attempt to start enough miners to reach the requested
    power consumption. If the requested power exceeds the rated capacity,
    a 400 error is returned.
    """
    try:
        fleet_manager = get_fleet_manager()
        
        # Check for manual override
        if fleet_manager.status.manual_override_active:
            logger.warning(
                "Activation rejected - manual override active",
                requested_power=request.activation_power_in_kw
            )
            return JSONResponse(
                status_code=status.HTTP_409_CONFLICT,
                content={
                    "accepted": False,
                    "message": "Manual override is active. Command rejected."
                }
            )
        
        logger.info(
            "Activation request received",
            power_kw=request.activation_power_in_kw
        )
        
        # Attempt activation
        success, message = await fleet_manager.activate(
            request.activation_power_in_kw
        )
        
        if success:
            return CommandResponse(accepted=True, message=message)
        else:
            # Determine appropriate error code
            if "exceeds" in message.lower():
                status_code = status.HTTP_400_BAD_REQUEST
            elif "not available" in message.lower() or "fault" in message.lower():
                status_code = status.HTTP_409_CONFLICT
            else:
                status_code = status.HTTP_500_INTERNAL_SERVER_ERROR
            
            return JSONResponse(
                status_code=status_code,
                content={"accepted": False, "message": message}
            )
            
    except Exception as e:
        logger.error("Activation failed", error=str(e))
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={
                "accepted": False,
                "message": f"Internal error: {str(e)}"
            }
        )


@router.post(
    "/deactivate",
    response_model=CommandResponse,
    summary="Deactivate fleet",
    description="""
    Stops the fleet's active operation and returns it to StandBy mode.
    
    The deactivation process begins immediately. If the fleet is already
    in StandBy mode, success is returned (idempotent behavior).
    Response time must be ≤ 2 seconds.
    """,
    responses={
        200: {
            "description": "Deactivation command accepted",
            "content": {
                "application/json": {
                    "example": {
                        "accepted": True,
                        "message": "Fleet deactivation command accepted."
                    }
                }
            }
        },
        409: {
            "description": "Fleet cannot deactivate due to fault or unsafe condition",
            "content": {
                "application/json": {
                    "example": {
                        "accepted": False,
                        "message": "Fleet is in a fault state and cannot be deactivated."
                    }
                }
            }
        },
        500: {
            "description": "Internal server error"
        }
    }
)
async def deactivate(request: DeactivateRequest = None):
    """
    Deactivate the mining fleet and return to standby mode.
    
    All miners will be stopped. If the fleet is already in standby,
    this is a no-op and success is returned.
    """
    try:
        fleet_manager = get_fleet_manager()
        
        # Check for manual override
        if fleet_manager.status.manual_override_active:
            logger.warning("Deactivation rejected - manual override active")
            return JSONResponse(
                status_code=status.HTTP_409_CONFLICT,
                content={
                    "accepted": False,
                    "message": "Manual override is active. Command rejected."
                }
            )
        
        logger.info("Deactivation request received")
        
        # Attempt deactivation
        success, message = await fleet_manager.deactivate()
        
        if success:
            return CommandResponse(accepted=True, message=message)
        else:
            status_code = (
                status.HTTP_409_CONFLICT
                if "fault" in message.lower()
                else status.HTTP_500_INTERNAL_SERVER_ERROR
            )
            return JSONResponse(
                status_code=status_code,
                content={"accepted": False, "message": message}
            )
            
    except Exception as e:
        logger.error("Deactivation failed", error=str(e))
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={
                "accepted": False,
                "message": f"Internal error: {str(e)}"
            }
        )
