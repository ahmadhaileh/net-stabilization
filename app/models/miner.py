"""
Pydantic models for AwesomeMiner API responses.
Based on AwesomeMiner Remote API documentation.
"""
from typing import Optional, List
from enum import Enum

from pydantic import BaseModel, Field


class MinerStatus(str, Enum):
    """Miner status values from AwesomeMiner."""
    MINING = "Mining"
    DISABLED = "Disabled"
    OFFLINE = "Offline"
    ERROR = "Error"
    STOPPED = "Stopped"
    BENCHMARKING = "Benchmarking"
    PENDING = "Pending"
    UPDATING = "Updating"


class GpuInfo(BaseModel):
    """GPU information from AwesomeMiner."""
    name: Optional[str] = None
    temperature: Optional[float] = None
    fan_speed: Optional[int] = Field(None, alias="fanSpeed")
    power: Optional[float] = None  # Power in watts
    hashrate: Optional[float] = None
    
    class Config:
        populate_by_name = True


class MinerInfo(BaseModel):
    """
    Individual miner information from AwesomeMiner.
    """
    id: int
    name: str
    hostname: Optional[str] = None
    status: Optional[str] = None
    status_info: Optional[str] = Field(None, alias="statusInfo")
    
    # Power information
    power_usage: Optional[float] = Field(None, alias="powerUsage")  # Watts
    
    # Hardware info
    gpu_list: Optional[List[GpuInfo]] = Field(None, alias="gpuList")
    
    # Hashrate
    speed_info: Optional[str] = Field(None, alias="speedInfo")
    hashrate: Optional[float] = None
    
    # Pool info
    pool: Optional[str] = None
    coin: Optional[str] = None
    
    class Config:
        populate_by_name = True
    
    @property
    def power_in_kw(self) -> float:
        """Get power usage in kilowatts."""
        if self.power_usage is not None:
            return self.power_usage / 1000.0
        return 0.0
    
    @property
    def is_mining(self) -> bool:
        """Check if miner is actively mining."""
        return self.status == MinerStatus.MINING.value
    
    @property
    def is_available(self) -> bool:
        """Check if miner is available for dispatch (not in error/offline state)."""
        if self.status is None:
            return False
        return self.status not in [
            MinerStatus.OFFLINE.value,
            MinerStatus.ERROR.value,
            MinerStatus.UPDATING.value
        ]


class MinerListResponse(BaseModel):
    """Response from AwesomeMiner miners list endpoint."""
    miners: List[MinerInfo] = Field(default_factory=list)


class GroupInfo(BaseModel):
    """Mining group information from AwesomeMiner."""
    id: int
    name: str
    miner_count: Optional[int] = Field(None, alias="minerCount")
    
    class Config:
        populate_by_name = True


class GroupListResponse(BaseModel):
    """Response from AwesomeMiner groups list endpoint."""
    groups: List[GroupInfo] = Field(default_factory=list)
