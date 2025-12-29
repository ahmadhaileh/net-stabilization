"""
Internal state models for the fleet management system.
"""
from datetime import datetime
from enum import Enum
from typing import Optional, List, Dict, Any

from pydantic import BaseModel, Field

from .ems import RunningStatus


class FleetState(str, Enum):
    """Internal fleet state tracking."""
    UNKNOWN = "unknown"
    STANDBY = "standby"
    RUNNING = "running"
    ACTIVATING = "activating"
    DEACTIVATING = "deactivating"
    FAULT = "fault"


class MinerState(BaseModel):
    """Current state of an individual miner."""
    miner_id: str  # String to support both numeric IDs and IP-based IDs
    name: str
    is_online: bool = False
    is_mining: bool = False
    power_kw: float = 0.0
    rated_power_kw: float = 0.0
    target_power_kw: Optional[float] = None
    last_update: datetime = Field(default_factory=datetime.utcnow)
    error: Optional[str] = None
    
    @property
    def is_available(self) -> bool:
        """Check if miner is available for dispatch."""
        return self.is_online and self.error is None


class FleetStatus(BaseModel):
    """Aggregated fleet status."""
    state: FleetState = FleetState.UNKNOWN
    is_available_for_dispatch: bool = False
    running_status: RunningStatus = RunningStatus.STANDBY
    
    # Power metrics (all in kW)
    rated_power_kw: float = 0.0
    active_power_kw: float = 0.0
    target_power_kw: Optional[float] = None
    
    # Fleet composition
    total_miners: int = 0
    online_miners: int = 0
    mining_miners: int = 0
    
    # Miners list
    miners: List[MinerState] = Field(default_factory=list)
    
    # Override settings
    manual_override_active: bool = False
    override_target_power_kw: Optional[float] = None
    
    # Timestamps
    last_update: datetime = Field(default_factory=datetime.utcnow)
    last_ems_command: Optional[datetime] = None
    
    # Error tracking
    errors: List[str] = Field(default_factory=list)


class CommandLog(BaseModel):
    """Log entry for commands received/executed."""
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    source: str  # "ems" or "dashboard"
    command: str  # "activate", "deactivate", "override"
    parameters: Dict[str, Any] = Field(default_factory=dict)
    success: bool = True
    message: Optional[str] = None


class SystemConfig(BaseModel):
    """Runtime system configuration (modifiable via dashboard)."""
    # Auto-calculated or manual rated power
    rated_power_kw_override: Optional[float] = None
    
    # Power distribution strategy
    # "even" - distribute evenly across miners
    # "priority" - fill miners in priority order
    power_distribution_strategy: str = "even"
    
    # Miner priority list (by miner ID) for priority strategy
    miner_priority: List[str] = Field(default_factory=list)
    
    # Safety limits
    max_power_change_rate_kw_per_sec: float = 50.0
    min_miner_power_percent: float = 0.0  # Some miners need minimum power
    
    # Timing
    activation_timeout_seconds: float = 30.0
    deactivation_timeout_seconds: float = 30.0
