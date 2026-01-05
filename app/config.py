"""
Application configuration using Pydantic Settings.
"""
from functools import lru_cache
from typing import Optional

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""
    
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )
    
    # AwesomeMiner Configuration (legacy - can be disabled)
    awesome_miner_host: str = "localhost"
    awesome_miner_port: int = 17790
    awesome_miner_api_key: Optional[str] = None
    awesome_miner_enabled: bool = False  # Disabled by default, use direct mode
    
    # Direct Miner Discovery Configuration
    miner_discovery_enabled: bool = True  # Enable auto-discovery
    miner_network_cidr: str = "192.168.1.0/24"  # Network to scan
    miner_api_port: int = 4028  # CGMiner API port
    miner_scan_timeout: float = 1.0  # Timeout for discovery probes
    miner_api_timeout: float = 5.0  # Timeout for API calls
    auto_discovery_on_startup: bool = True  # Run discovery on startup
    discovery_interval_minutes: int = 30  # Periodic re-discovery interval
    
    # Server Configuration
    host_port: int = 8080
    log_level: str = "INFO"
    
    # Polling Configuration
    poll_interval_seconds: int = 5
    snapshot_interval_seconds: int = 60  # Save snapshots every 60s (not every poll)
    snapshot_retention_hours: int = 24  # Keep detailed snapshots for 24 hours
    
    # Database
    database_url: str = "sqlite+aiosqlite:///./data/netstab.db"
    
    # Fleet Configuration
    rated_power_kw: Optional[float] = None  # Auto-calculated if not set
    min_power_threshold_kw: float = 0.1  # Lower threshold for activation
    power_ramp_rate_kw_per_sec: float = 50.0
    idle_all_on_startup: bool = False  # Don't idle miners on startup - let them keep their current state
    
    # Power Control Mode:
    # - "frequency": Use frequency scaling for fine-grained power control (requires working API)
    # - "on_off": Simple on/off control per miner (more reliable, coarser granularity)
    power_control_mode: str = "on_off"  # Default to more reliable on/off mode
    
    @property
    def awesome_miner_base_url(self) -> str:
        """Construct the AwesomeMiner API base URL."""
        return f"http://{self.awesome_miner_host}:{self.awesome_miner_port}"


@lru_cache
def get_settings() -> Settings:
    """Get cached settings instance."""
    return Settings()
