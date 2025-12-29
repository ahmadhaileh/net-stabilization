"""
Pydantic models for the EMS API protocol.
These models define the exact JSON structures required by the EMS specification.
"""
from enum import IntEnum
from typing import Optional

from pydantic import BaseModel, Field


class RunningStatus(IntEnum):
    """
    Device running status as defined by EMS specification.
    
    Values:
        STANDBY (1): Device is connected but not running, waiting for commands (idle state)
        RUNNING (2): Device is activated and consuming power
    """
    STANDBY = 1
    RUNNING = 2


class StatusResponse(BaseModel):
    """
    Response model for GET /api/status endpoint.
    
    This is sent to the EMS to report the current operational state of the mining fleet.
    """
    is_available_for_dispatch: bool = Field(
        ...,
        alias="isAvailableForDispatch",
        description="Indicates whether the fleet is ready to accept an activation request"
    )
    running_status: int = Field(
        ...,
        alias="runningStatus",
        description="Numerical value: 1 = StandBy, 2 = Running"
    )
    rated_power_in_kw: float = Field(
        ...,
        alias="ratedPowerInKw",
        description="Maximum continuous power rating of the fleet in kilowatts"
    )
    active_power_in_kw: float = Field(
        ...,
        alias="activePowerInKw",
        description="Real-time active power currently being consumed"
    )

    class Config:
        populate_by_name = True


class ActivateRequest(BaseModel):
    """
    Request model for POST /api/activate endpoint.
    
    Sent by EMS to request the fleet to start operation at a specified power level.
    """
    activation_power_in_kw: float = Field(
        ...,
        alias="activationPowerInKw",
        ge=0,
        description="Power level requested by EMS (in kW). Must not exceed rated power."
    )

    class Config:
        populate_by_name = True


class CommandResponse(BaseModel):
    """
    Response model for activation/deactivation commands.
    
    Standard response format for POST /api/activate and POST /api/deactivate.
    """
    accepted: bool = Field(
        ...,
        description="Whether the command was accepted"
    )
    message: str = Field(
        ...,
        description="Human-readable explanation of the result"
    )


class DeactivateRequest(BaseModel):
    """
    Request model for POST /api/deactivate endpoint.
    
    The EMS spec indicates this should be an empty JSON object.
    """
    pass
