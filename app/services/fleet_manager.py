"""
Fleet Manager - Core business logic for managing the mining fleet.

This module handles:
- Aggregating miner states into fleet status
- Power distribution calculations
- Activation/deactivation logic (idle mode)
- State transitions
- Direct miner communication OR AwesomeMiner integration
"""
import asyncio
from datetime import datetime
from typing import Optional, List, Dict, Any

import structlog

from app.config import Settings, get_settings
from app.models.ems import RunningStatus
from app.models.state import (
    FleetState,
    FleetStatus,
    MinerState,
    CommandLog,
    SystemConfig
)
from app.models.miner import MinerInfo
from app.services.awesome_miner import AwesomeMinerClient, get_awesome_miner_client
from app.services.miner_discovery import (
    MinerDiscoveryService,
    get_discovery_service,
    DiscoveredMiner
)
from app.services.vnish_power import VnishPowerService, get_vnish_power_service

logger = structlog.get_logger()


class FleetManager:
    """
    Manages the mining fleet state and operations.
    
    This is the core service that:
    - Polls miners for status updates (direct or via AwesomeMiner)
    - Calculates aggregated fleet metrics
    - Handles activation/deactivation commands (idle mode)
    - Distributes power targets across miners
    
    Supports two modes:
    - Direct mode: Communicates directly with miners via CGMiner API
    - AwesomeMiner mode: Uses AwesomeMiner REST API (legacy)
    """
    
    def __init__(
        self,
        settings: Optional[Settings] = None,
        am_client: Optional[AwesomeMinerClient] = None,
        discovery_service: Optional[MinerDiscoveryService] = None
    ):
        self.settings = settings or get_settings()
        
        # Database service for persistence
        from app.database import get_db_service
        self.db = get_db_service()
        
        # Service mode
        self._use_direct_mode = self.settings.miner_discovery_enabled
        
        # Services
        if self._use_direct_mode:
            self.discovery = discovery_service or get_discovery_service()
            self.discovery.network_cidr = self.settings.miner_network_cidr
            self.am_client = None
            self.vnish_power = get_vnish_power_service()
            logger.info("Fleet manager using DIRECT miner communication mode")
        else:
            self.am_client = am_client or get_awesome_miner_client()
            self.discovery = None
            self.vnish_power = None
            logger.info("Fleet manager using AwesomeMiner mode")
        
        # Current fleet status
        self._status = FleetStatus()
        
        # Runtime configuration
        self._config = SystemConfig()
        
        # Command history
        self._command_log: List[CommandLog] = []
        
        # Lock for state modifications
        self._lock = asyncio.Lock()
        
        # Background task handles
        self._poll_task: Optional[asyncio.Task] = None
        self._discovery_task: Optional[asyncio.Task] = None
        self._regulation_task: Optional[asyncio.Task] = None
        
        # Target power for activation (set by EMS)
        self._target_power_kw: Optional[float] = None
        
        # Power regulation settings
        self._regulation_interval_seconds: int = 30  # Check power every 30 seconds
        self._regulation_tolerance_percent: float = 10.0  # Tolerate 10% deviation
        
        # Manual override flag - load from DB
        self._manual_override = self.db.get_setting("manual_override", False)
        self._override_power_kw = self.db.get_setting("override_power_kw", None)
        
        # Power control mode - load from DB (defaults to on_off)
        # "frequency" = fine-grained frequency scaling
        # "on_off" = simple on/off per miner
        self._power_control_mode: str = self.db.get_setting("power_control_mode", "on_off")
        
        # Snapshot throttling
        self._last_fleet_snapshot_time: Optional[datetime] = None
        self._snapshot_interval = self.settings.snapshot_interval_seconds
        self._cleanup_task: Optional[asyncio.Task] = None
    
    @property
    def status(self) -> FleetStatus:
        """Get current fleet status."""
        return self._status
    
    @property
    def power_control_mode(self) -> str:
        """Get current power control mode."""
        return self._power_control_mode
    
    @power_control_mode.setter
    def power_control_mode(self, mode: str):
        """Set power control mode ('frequency' or 'on_off')."""
        if mode not in ("frequency", "on_off"):
            raise ValueError(f"Invalid power control mode: {mode}")
        self._power_control_mode = mode
        self.db.set_setting("power_control_mode", mode)
        logger.info("Power control mode changed", mode=mode)
    
    @property
    def config(self) -> SystemConfig:
        """Get current system configuration."""
        return self._config
    
    # =========================================================================
    # Status Polling
    # =========================================================================
    
    async def start_polling(self):
        """Start background status polling and optional discovery."""
        if self._poll_task is None or self._poll_task.done():
            self._poll_task = asyncio.create_task(self._polling_loop())
            logger.info("Started fleet status polling")
        
        # Start cleanup task
        if self._cleanup_task is None or self._cleanup_task.done():
            self._cleanup_task = asyncio.create_task(self._cleanup_loop())
            logger.info("Started database cleanup task")
        
        # Start power regulation loop
        if self._regulation_task is None or self._regulation_task.done():
            self._regulation_task = asyncio.create_task(self._regulation_loop())
            logger.info("Started power regulation loop")
        
        # Start periodic discovery if in direct mode
        if self._use_direct_mode and self.settings.auto_discovery_on_startup:
            # Run initial discovery
            logger.info("Running initial miner discovery...")
            await self.run_discovery()
            
            # Idle all miners on startup if configured
            if self.settings.idle_all_on_startup:
                logger.info("Idling all miners on startup (default standby mode)...")
                await self.idle_all_miners()
            
            # Start periodic discovery
            if self._discovery_task is None or self._discovery_task.done():
                self._discovery_task = asyncio.create_task(self._discovery_loop())
    
    async def idle_all_miners(self) -> tuple[bool, str]:
        """
        Put ALL miners into idle mode.
        
        This is used on startup to ensure fleet starts in standby mode.
        EMS signals will then activate the appropriate number of miners.
        
        Returns:
            Tuple of (success, message)
        """
        if not self._use_direct_mode:
            return False, "Idle all only supported in direct mode"
        
        all_miners = list(self.discovery.miners)
        if not all_miners:
            return True, "No miners to idle"
        
        success_count = 0
        fail_count = 0
        
        # Send idle command to ALL miners regardless of is_mining status
        # because the status might be stale or inaccurate
        for miner in all_miners:
            ok, msg = await self.discovery.set_miner_idle(miner.id)
            if ok:
                success_count += 1
                logger.info("Miner idled on startup", miner_id=miner.id, ip=miner.ip)
            else:
                fail_count += 1
                logger.warning("Failed to idle miner on startup", miner_id=miner.id, error=msg)
        
        self._status.state = FleetState.STANDBY
        self._target_power_kw = None
        
        return True, f"Sent idle command to {success_count}/{len(all_miners)} miners (failed: {fail_count})"
    
    def _save_fleet_snapshot_throttled(
        self,
        total_power_kw: float,
        online_count: int,
        mining_count: int,
        miner_count: int,
        fleet_state: FleetState
    ):
        """Save fleet snapshot with time-based throttling."""
        now = datetime.utcnow()
        
        # Check if enough time has passed
        if self._last_fleet_snapshot_time is not None:
            elapsed = (now - self._last_fleet_snapshot_time).total_seconds()
            if elapsed < self._snapshot_interval:
                return  # Skip this snapshot
        
        try:
            self.db.save_fleet_snapshot(
                total_hashrate_ghs=0,  # Would need to aggregate from miners
                total_power_watts=total_power_kw * 1000,
                miners_online=online_count,
                miners_mining=mining_count,
                miners_total=miner_count,
                fleet_state=fleet_state.value
            )
            self._last_fleet_snapshot_time = now
        except Exception as e:
            logger.debug("Failed to save fleet snapshot", error=str(e))
    
    async def stop_polling(self):
        """Stop background status polling and discovery."""
        if self._poll_task and not self._poll_task.done():
            self._poll_task.cancel()
            try:
                await self._poll_task
            except asyncio.CancelledError:
                pass
            logger.info("Stopped fleet status polling")
        
        if self._discovery_task and not self._discovery_task.done():
            self._discovery_task.cancel()
            try:
                await self._discovery_task
            except asyncio.CancelledError:
                pass
            logger.info("Stopped periodic discovery")
        
        if self._cleanup_task and not self._cleanup_task.done():
            self._cleanup_task.cancel()
            try:
                await self._cleanup_task
            except asyncio.CancelledError:
                pass
            logger.info("Stopped cleanup task")
        
        if self._regulation_task and not self._regulation_task.done():
            self._regulation_task.cancel()
            try:
                await self._regulation_task
            except asyncio.CancelledError:
                pass
            logger.info("Stopped power regulation loop")
    
    async def _polling_loop(self):
        """Background loop that polls miner status."""
        while True:
            try:
                await self.update_status()
            except Exception as e:
                logger.error("Error in polling loop", error=str(e))
            
            await asyncio.sleep(self.settings.poll_interval_seconds)
    
    async def _discovery_loop(self):
        """Background loop for periodic miner discovery."""
        interval = self.settings.discovery_interval_minutes * 60
        while True:
            await asyncio.sleep(interval)
            try:
                logger.info("Running periodic miner discovery...")
                await self.run_discovery()
            except Exception as e:
                logger.error("Error in discovery loop", error=str(e))
    
    async def _cleanup_loop(self):
        """Background loop for database cleanup (runs every hour)."""
        while True:
            await asyncio.sleep(3600)  # Run every hour
            try:
                deleted = self.db.cleanup_old_snapshots(
                    retention_hours=self.settings.snapshot_retention_hours
                )
                if any(deleted.values()):
                    logger.info(
                        "Database cleanup completed",
                        miner_snapshots=deleted.get("miner_snapshots", 0),
                        fleet_snapshots=deleted.get("fleet_snapshots", 0),
                        command_history=deleted.get("command_history", 0)
                    )
            except Exception as e:
                logger.error("Error in cleanup loop", error=str(e))
    
    async def _regulation_loop(self):
        """
        Background loop for continuous power regulation.
        
        Runs periodically to compare actual power vs target power and
        adjusts miner allocations if deviation exceeds tolerance threshold.
        """
        while True:
            await asyncio.sleep(self._regulation_interval_seconds)
            
            try:
                # Skip if no active target power
                if self._target_power_kw is None or self._target_power_kw <= 0:
                    continue
                
                # Skip if in manual override mode
                if self._manual_override:
                    continue
                
                # Skip if not in running state
                if self._status.state != FleetState.RUNNING:
                    continue
                
                # Get current actual power
                actual_power_kw = self._status.active_power_kw
                target_power_kw = self._target_power_kw
                
                # Calculate deviation percentage
                if target_power_kw > 0:
                    deviation_percent = abs(actual_power_kw - target_power_kw) / target_power_kw * 100
                else:
                    deviation_percent = 0
                
                logger.debug(
                    "Power regulation check",
                    target_kw=target_power_kw,
                    actual_kw=actual_power_kw,
                    deviation_percent=round(deviation_percent, 1)
                )
                
                # Check if adjustment is needed
                if deviation_percent > self._regulation_tolerance_percent:
                    logger.info(
                        "Power deviation detected, re-adjusting",
                        target_kw=target_power_kw,
                        actual_kw=actual_power_kw,
                        deviation_percent=round(deviation_percent, 1),
                        tolerance_percent=self._regulation_tolerance_percent
                    )
                    
                    # Re-activate with same target to adjust miner allocations
                    # Use internal method to avoid locking issues
                    if self._use_direct_mode:
                        await self._activate_fleet_direct(target_power_kw)
                    else:
                        await self._activate_fleet_awesomeminer(target_power_kw)
                        
            except Exception as e:
                logger.error("Error in regulation loop", error=str(e))
    
    async def run_discovery(self) -> int:
        """
        Run miner discovery on the network.
        
        Returns:
            Number of miners discovered
        """
        if not self._use_direct_mode:
            return 0
        
        miners = await self.discovery.discover_miners()
        logger.info(f"Discovery found {len(miners)} miners")
        return len(miners)
    
    async def update_status(self) -> FleetStatus:
        """
        Update fleet status from miners.
        
        Returns:
            Updated FleetStatus
        """
        async with self._lock:
            try:
                if self._use_direct_mode:
                    return await self._update_status_direct()
                else:
                    return await self._update_status_awesomeminer()
                    
            except Exception as e:
                logger.error("Failed to update fleet status", error=str(e))
                self._status.errors.append(f"Status update failed: {str(e)}")
                self._status.state = FleetState.UNKNOWN
                raise
    
    async def _update_status_direct(self) -> FleetStatus:
        """Update status using direct miner communication."""
        # Update all miners
        miners = await self.discovery.update_all_miners()
        
        # Build miner states
        miner_states = []
        total_power_kw = 0.0
        total_rated_kw = 0.0
        online_count = 0
        mining_count = 0
        
        # Idle power consumption for control board (18W per miner when online but not mining)
        IDLE_POWER_KW = 0.018
        
        for miner in miners:
            state = MinerState(
                miner_id=miner.id,
                name=f"{miner.model} ({miner.ip})",
                is_online=miner.is_online,
                is_mining=miner.is_mining,
                power_kw=miner.power_kw,
                rated_power_kw=miner.rated_power_kw,
                last_update=miner.last_seen
            )
            miner_states.append(state)
            
            # Always count rated power (even for idle/offline miners)
            # This ensures we know total fleet capacity
            total_rated_kw += state.rated_power_kw
            
            if state.is_online:
                online_count += 1
                if state.is_mining:
                    mining_count += 1
                    total_power_kw += state.power_kw
                else:
                    # Idle but online miners still consume ~18W for control board
                    total_power_kw += IDLE_POWER_KW
        
        return self._finalize_status(
            miner_states, total_power_kw, total_rated_kw,
            online_count, mining_count
        )
    
    async def _update_status_awesomeminer(self) -> FleetStatus:
        """Update status using AwesomeMiner API (legacy)."""
        # Get miners from AwesomeMiner
        miners = await self.am_client.get_miners()
        
        # Update individual miner states
        miner_states = []
        total_power_kw = 0.0
        total_rated_kw = 0.0
        online_count = 0
        mining_count = 0
        
        for miner in miners:
            state = MinerState(
                miner_id=str(miner.id),
                name=miner.name,
                is_online=miner.is_available,
                is_mining=miner.is_mining,
                power_kw=miner.power_in_kw,
                rated_power_kw=self._estimate_miner_rated_power(miner),
                last_update=datetime.utcnow()
            )
            miner_states.append(state)
            
            if state.is_online:
                online_count += 1
                total_rated_kw += state.rated_power_kw
            
            if state.is_mining:
                mining_count += 1
                total_power_kw += state.power_kw
        
        return self._finalize_status(
            miner_states, total_power_kw, total_rated_kw,
            online_count, mining_count
        )
    
    def _finalize_status(
        self,
        miner_states: List[MinerState],
        total_power_kw: float,
        total_rated_kw: float,
        online_count: int,
        mining_count: int
    ) -> FleetStatus:
        """Common status finalization logic."""
        # Determine fleet state and running status
        fleet_state, running_status = self._calculate_fleet_state(
            mining_count, online_count, total_power_kw
        )
        
        # Use override rated power if configured
        if self._config.rated_power_kw_override is not None:
            rated_power = self._config.rated_power_kw_override
        elif self.settings.rated_power_kw is not None:
            rated_power = self.settings.rated_power_kw
        else:
            rated_power = total_rated_kw
        
        # Check availability for dispatch
        is_available = (
            online_count > 0 and
            fleet_state not in [FleetState.FAULT, FleetState.UNKNOWN]
        )
        
        # Update status
        self._status = FleetStatus(
            state=fleet_state,
            is_available_for_dispatch=is_available,
            running_status=running_status,
            rated_power_kw=rated_power,
            active_power_kw=round(total_power_kw, 2),
            target_power_kw=self._target_power_kw,
            total_miners=len(miner_states),
            online_miners=online_count,
            mining_miners=mining_count,
            miners=miner_states,
            manual_override_active=self._manual_override,
            override_target_power_kw=self._override_power_kw,
            last_update=datetime.utcnow()
        )
        
        # Save fleet snapshot to database (for historical charts) - throttled
        self._save_fleet_snapshot_throttled(
            total_power_kw=total_power_kw,
            online_count=online_count,
            mining_count=mining_count,
            miner_count=len(miner_states),
            fleet_state=fleet_state
        )
        
        logger.debug(
            "Fleet status updated",
            state=fleet_state.value,
            active_power=total_power_kw,
            miners_online=online_count,
            miners_mining=mining_count,
            mode="direct" if self._use_direct_mode else "awesomeminer"
        )
        
        return self._status
    
    def _calculate_fleet_state(
        self,
        mining_count: int,
        online_count: int,
        total_power_kw: float
    ) -> tuple[FleetState, RunningStatus]:
        """Calculate fleet state based on miner states."""
        if online_count == 0:
            return FleetState.FAULT, RunningStatus.STANDBY
        
        if total_power_kw > self.settings.min_power_threshold_kw:
            return FleetState.RUNNING, RunningStatus.RUNNING
        else:
            return FleetState.STANDBY, RunningStatus.STANDBY
    
    def _estimate_miner_rated_power(self, miner: MinerInfo) -> float:
        """
        Estimate rated power for a miner.
        
        This is a placeholder - in production, this should be configured
        per-miner based on known hardware specs.
        """
        # If power usage is reported and miner is mining, use it as estimate
        if miner.power_usage and miner.is_mining:
            return miner.power_in_kw * 1.1  # Add 10% headroom
        
        # Default estimate - should be configured properly
        return 3.0  # 3 kW default (typical for a single GPU/ASIC)
    
    # =========================================================================
    # Activation / Deactivation
    # =========================================================================
    
    async def activate(self, target_power_kw: float) -> tuple[bool, str]:
        """
        Activate the fleet at the specified power level.
        
        Args:
            target_power_kw: Target power consumption in kW
            
        Returns:
            Tuple of (success, message)
        """
        async with self._lock:
            logger.info("Activation requested", target_power_kw=target_power_kw)
            
            # Validate request
            if target_power_kw < 0:
                return False, "Requested power cannot be negative."
            
            if target_power_kw > self._status.rated_power_kw:
                return False, "Requested power exceeds rated limits."
            
            if not self._status.is_available_for_dispatch:
                return False, "Fleet is not available for dispatch."
            
            # Store target
            self._target_power_kw = target_power_kw
            self._status.last_ems_command = datetime.utcnow()
            
            # Log command
            self._log_command("ems", "activate", {"power_kw": target_power_kw})
            
            # If target is 0 or very small, treat as deactivation
            if target_power_kw < self.settings.min_power_threshold_kw:
                return await self._deactivate_fleet()
            
            # Calculate power distribution and activate miners
            success, message = await self._activate_fleet(target_power_kw)
            
            return success, message
    
    async def deactivate(self) -> tuple[bool, str]:
        """
        Deactivate the fleet (stop all mining).
        
        Returns:
            Tuple of (success, message)
        """
        async with self._lock:
            logger.info("Deactivation requested")
            
            # Clear target
            self._target_power_kw = None
            self._status.last_ems_command = datetime.utcnow()
            
            # Log command
            self._log_command("ems", "deactivate", {})
            
            return await self._deactivate_fleet()
    
    async def _activate_fleet(self, target_power_kw: float) -> tuple[bool, str]:
        """Internal method to activate miners to reach target power."""
        try:
            self._status.state = FleetState.ACTIVATING
            
            logger.info(
                "Fleet activation started",
                target_kw=target_power_kw,
                use_direct_mode=self._use_direct_mode
            )
            
            if self._use_direct_mode:
                return await self._activate_fleet_direct(target_power_kw)
            else:
                return await self._activate_fleet_awesomeminer(target_power_kw)
                
        except Exception as e:
            logger.error("Fleet activation failed", error=str(e))
            return False, f"Activation failed: {str(e)}"
    
    async def _activate_fleet_direct(self, target_power_kw: float) -> tuple[bool, str]:
        """
        Activate miners using direct communication.
        
        Supports two modes based on settings.power_control_mode:
        
        "frequency" mode (requires working frequency API):
        1. Calculate how many miners run at FULL power (default frequency)
        2. One "swing" miner runs at variable frequency for the remainder
        3. Remaining miners stay IDLE
        
        "on_off" mode (simple, reliable):
        1. Calculate how many miners to turn fully ON to reach target
        2. Turn ON the required number of miners
        3. Keep remaining miners OFF
        4. Any shortfall/overshoot is accepted (coarser control)
        """
        all_miners = [m for m in self.discovery.miners if m.is_online]
        
        if not all_miners:
            return False, "No online miners available"
        
        
        # Check power control mode
        logger.info(
            "Activating fleet in direct mode",
            power_control_mode=self._power_control_mode,
            target_kw=target_power_kw,
            miners_online=len(all_miners)
        )
        
        if self._power_control_mode == "on_off":
            logger.info("Using ON/OFF power control mode")
            return await self._activate_fleet_on_off_mode(target_power_kw, all_miners)
        else:
            logger.info("Using FREQUENCY power control mode")
            return await self._activate_fleet_frequency_mode(target_power_kw, all_miners)
    
    async def _activate_fleet_on_off_mode(
        self, 
        target_power_kw: float, 
        all_miners: List[DiscoveredMiner]
    ) -> tuple[bool, str]:
        """
        Simple on/off power control - no frequency scaling.
        
        Turns on just enough miners to reach the target power.
        Accepts that actual power will be in discrete steps.
        """
        # Define full power per miner (watts)
        full_power_watts = int(all_miners[0].rated_power_watts) if all_miners else 1460
        full_power_kw = full_power_watts / 1000.0
        
        # Calculate how many miners to turn on
        target_power_watts = target_power_kw * 1000
        miners_needed = int(target_power_watts / full_power_watts)
        
        # Add one more if there's significant remainder (> 30% of a miner)
        remainder = target_power_watts - (miners_needed * full_power_watts)
        if remainder > (full_power_watts * 0.3):
            miners_needed += 1
        
        # Clamp to available miners
        miners_needed = min(miners_needed, len(all_miners))
        
        logger.info(
            "On/Off power control",
            target_kw=target_power_kw,
            miners_needed=miners_needed,
            full_power_kw=full_power_kw,
            total_available=len(all_miners)
        )
        
        # Sort miners by IP for consistent ordering
        sorted_miners = sorted(all_miners, key=lambda m: m.ip)
        
        success_count = 0
        results = []
        
        for i, miner in enumerate(sorted_miners):
            if i < miners_needed:
                # Turn this miner ON
                if not miner.is_mining:
                    ok, msg = await self.discovery.set_miner_active(miner.id)
                    if ok:
                        success_count += 1
                        results.append(f"{miner.ip}: ON")
                        logger.info("Miner activated", ip=miner.ip)
                    else:
                        logger.warning("Failed to activate miner", ip=miner.ip, error=msg)
                else:
                    results.append(f"{miner.ip}: ON (already)")
            else:
                # Turn this miner OFF
                if miner.is_mining:
                    ok, msg = await self.discovery.set_miner_idle(miner.id)
                    if ok:
                        success_count += 1
                        results.append(f"{miner.ip}: OFF")
                        logger.info("Miner idled", ip=miner.ip)
                    else:
                        logger.warning("Failed to idle miner", ip=miner.ip, error=msg)
                else:
                    results.append(f"{miner.ip}: OFF (already)")
        
        self._status.state = FleetState.RUNNING
        
        actual_power_kw = miners_needed * full_power_kw
        return True, f"Target: {target_power_kw:.2f}kW, Actual: {actual_power_kw:.2f}kW ({miners_needed} miners ON)"
    
    async def _activate_fleet_frequency_mode(
        self,
        target_power_kw: float,
        all_miners: List[DiscoveredMiner]
    ) -> tuple[bool, str]:
        """
        Frequency scaling power control (Full + Swing miner strategy).
        
        For S9 miners: each is ~1.46 kW at full power (650MHz).
        - If target is 2.0 kW: 1 full (1.46kW) + 1 swing at ~540W (~350MHz)
        - If target is 3.0 kW: 2 full (2.92kW) - slightly over
        - If target is 3.5 kW: 2 full (2.92kW) + 1 swing at ~580W (~375MHz)
        """
        # Get current state
        currently_mining = {m.id: m for m in all_miners if m.is_mining}
        
        # Define full power per miner (watts) - use first miner's rated power or default
        full_power_watts = int(all_miners[0].rated_power_watts) if all_miners else 1460
        
        # Calculate power allocation using VnishPowerService
        miner_info_list = [
            {'ip': m.ip, 'id': m.id, 'is_mining': m.is_mining, 'is_online': m.is_online}
            for m in all_miners
        ]
        
        allocation = self.vnish_power.get_power_allocation(
            target_power_kw,
            miner_info_list,
            full_power_watts
        )
        
        for a in allocation:
            m = next((m for m in all_miners if m.ip == a['ip']), None)
        
        logger.info(
            "Fractional power activation",
            target_kw=target_power_kw,
            total_miners=len(all_miners),
            allocation_count=len(allocation)
        )
        
        # Apply allocation
        success_count = 0
        total_changes = 0
        results = []
        
        for alloc in allocation:
            ip = alloc['ip']
            action = alloc['action']
            frequency = alloc['frequency']
            voltage = alloc['voltage']
            
            # Find the miner object
            miner = next((m for m in all_miners if m.ip == ip), None)
            if not miner:
                continue
            
            if action == 'idle':
                # Turn off this miner
                if miner.is_mining:
                    total_changes += 1
                    ok, msg = await self.discovery.set_miner_idle(miner.id)
                    if ok:
                        success_count += 1
                        results.append(f"{ip}: idle")
                        logger.info("Miner set to idle", miner_id=miner.id, ip=ip)
                    else:
                        logger.warning("Failed to idle miner", miner_id=miner.id, error=msg)
                        
            elif action == 'full':
                # Full power miner
                if not miner.is_mining:
                    # Need to start mining first, then set frequency
                    total_changes += 1
                    ok, msg = await self.discovery.set_miner_active(miner.id)
                    if ok:
                        # Wait for miner to stabilize before frequency change
                        await asyncio.sleep(5)
                        ok2, msg2 = await self.vnish_power.set_miner_frequency(ip, frequency, voltage)
                        if ok2:
                            success_count += 1
                            results.append(f"{ip}: full ({frequency}MHz)")
                            logger.info("Miner activated at full power", miner_id=miner.id, ip=ip, freq=frequency)
                        else:
                            logger.warning("Failed to set frequency after wake", ip=ip, error=msg2)
                            results.append(f"{ip}: full (woke but freq failed)")
                    else:
                        logger.warning("Failed to activate miner", miner_id=miner.id, error=msg)
                else:
                    # Already mining - check if frequency needs adjustment
                    current_freq = miner.current_frequency  # None if unknown
                    if current_freq is None:
                        # Unknown frequency - always set to be safe
                        logger.info("Unknown miner frequency, setting to target", ip=ip, target_freq=frequency)
                        total_changes += 1
                        ok, msg = await self.vnish_power.set_miner_frequency(ip, frequency, voltage)
                        if ok:
                            success_count += 1
                            results.append(f"{ip}: full ({frequency}MHz)")
                        else:
                            logger.warning("Failed to set frequency", ip=ip, error=msg)
                    elif abs(current_freq - frequency) > 50:  # Only change if diff > 50MHz
                        total_changes += 1
                        logger.info("Adjusting miner frequency", ip=ip, current_freq=current_freq, target_freq=frequency)
                        ok, msg = await self.vnish_power.set_miner_frequency(ip, frequency, voltage)
                        if ok:
                            success_count += 1
                            results.append(f"{ip}: full ({frequency}MHz)")
                        else:
                            logger.warning("Failed to set frequency", ip=ip, error=msg)
                    else:
                        results.append(f"{ip}: full (no change, at {current_freq}MHz)")
                        
            elif action == 'swing':
                # Swing miner - partial power via frequency scaling
                total_changes += 1
                
                if not miner.is_mining:
                    # First start the miner, then set frequency
                    ok, msg = await self.discovery.set_miner_active(miner.id)
                    if ok:
                        # Wait for miner to stabilize before frequency change
                        await asyncio.sleep(5)
                        ok2, msg2 = await self.vnish_power.set_miner_frequency(ip, frequency, voltage)
                        if ok2:
                            success_count += 1
                            results.append(f"{ip}: swing ({frequency}MHz ~{alloc['estimated_power']}W)")
                            logger.info(
                                "Swing miner activated",
                                miner_id=miner.id,
                                ip=ip,
                                freq=frequency,
                                voltage=voltage,
                                est_power=alloc['estimated_power']
                            )
                        else:
                            logger.warning("Failed to set swing frequency", ip=ip, error=msg2)
                    else:
                        logger.warning("Failed to activate swing miner", miner_id=miner.id, error=msg)
                else:
                    # Already mining - just adjust frequency
                    ok, msg = await self.vnish_power.set_miner_frequency(ip, frequency, voltage)
                    if ok:
                        success_count += 1
                        results.append(f"{ip}: swing ({frequency}MHz ~{alloc['estimated_power']}W)")
                        logger.info(
                            "Swing miner frequency adjusted",
                            ip=ip,
                            freq=frequency,
                            voltage=voltage,
                            est_power=alloc['estimated_power']
                        )
                    else:
                        logger.warning("Failed to set swing frequency", ip=ip, error=msg)
        
        if total_changes == 0:
            self._status.state = FleetState.RUNNING
            estimated_power = sum(a['estimated_power'] for a in allocation)
            return True, f"Fleet already at target (~{estimated_power/1000:.2f} kW)"
        
        if success_count == 0:
            return False, "Failed to apply any power changes"
        
        self._status.state = FleetState.RUNNING
        
        # Calculate estimated power
        estimated_power = sum(a['estimated_power'] for a in allocation)
        full_count = sum(1 for a in allocation if a['action'] == 'full')
        swing_count = sum(1 for a in allocation if a['action'] == 'swing')
        idle_count = sum(1 for a in allocation if a['action'] == 'idle')
        
        summary = f"Full: {full_count}, Swing: {swing_count}, Idle: {idle_count}"
        return True, f"Target: {target_power_kw:.2f}kW, Est: {estimated_power/1000:.2f}kW ({summary})"
    
    def _calculate_proportional_power(
        self,
        miners: List[DiscoveredMiner],
        target_power_kw: float
    ) -> Dict[str, Dict[str, Any]]:
        """
        Calculate power allocation for each miner to hit target power.
        
        Strategy:
        1. First, determine how many miners need to be on (full power miners)
        2. Use one "swing" miner with frequency scaling for fine adjustment
        3. Keep remaining miners off
        
        Returns:
            Dict mapping miner_id to {"action": str, "frequency": int}
        """
        allocation = {}
        
        if not miners:
            return allocation
        
        # Sort miners by rated power (smallest first for better granularity)
        sorted_miners = sorted(miners, key=lambda m: m.rated_power_kw)
        
        remaining_power = target_power_kw
        
        for i, miner in enumerate(sorted_miners):
            if remaining_power <= 0:
                # Target reached, turn off remaining miners
                if miner.is_mining:
                    allocation[miner.id] = {"action": "turn_off"}
                continue
            
            # Full power of this miner
            full_power = miner.rated_power_kw
            
            # Can we fit this miner fully?
            if remaining_power >= full_power:
                # Turn on at full power
                if not miner.is_mining:
                    allocation[miner.id] = {
                        "action": "turn_on",
                        "frequency": miner.default_frequency
                    }
                elif miner.current_frequency and miner.current_frequency != miner.default_frequency:
                    # Already mining but at reduced frequency - restore full power
                    allocation[miner.id] = {
                        "action": "scale_frequency",
                        "frequency": miner.default_frequency
                    }
                remaining_power -= full_power
                
            elif remaining_power > 0:
                # This is the "swing" miner - use frequency scaling
                # Calculate required frequency to achieve remaining power
                power_ratio = remaining_power / full_power
                
                # Frequency scales roughly linearly with power
                # Map power ratio to frequency range
                freq_range = miner.max_frequency - miner.min_frequency
                target_freq = int(miner.min_frequency + (power_ratio * freq_range))
                
                # Clamp to valid range
                target_freq = max(miner.min_frequency, min(miner.max_frequency, target_freq))
                
                if not miner.is_mining:
                    allocation[miner.id] = {
                        "action": "turn_on",
                        "frequency": target_freq
                    }
                else:
                    allocation[miner.id] = {
                        "action": "scale_frequency",
                        "frequency": target_freq
                    }
                
                logger.info(
                    "Swing miner frequency calculated",
                    miner_id=miner.id,
                    target_power=remaining_power,
                    power_ratio=power_ratio,
                    target_freq=target_freq
                )
                
                remaining_power = 0  # Swing miner handles the rest
                
            else:
                # No more power needed, turn off if running
                if miner.is_mining:
                    allocation[miner.id] = {"action": "turn_off"}
        
        return allocation
    
    async def _activate_fleet_awesomeminer(self, target_power_kw: float) -> tuple[bool, str]:
        """Activate miners using AwesomeMiner API (legacy)."""
        # Get available miners
        available_miners = [
            m for m in self._status.miners
            if m.is_online
        ]
        
        if not available_miners:
            return False, "No miners available for activation."
        
        # Calculate how many miners to start
        miners_to_start = self._calculate_miners_to_start(
            available_miners, target_power_kw
        )
        
        # Start miners
        miner_ids = [int(m.miner_id) for m in miners_to_start]
        if miner_ids:
            results = await self.am_client.start_all_miners(miner_ids)
            
            started = sum(1 for v in results.values() if v)
            if started == 0:
                return False, "Failed to start any miners."
            
            logger.info(
                "Miners started for activation",
                requested=len(miner_ids),
                started=started
            )
        
        self._status.state = FleetState.RUNNING
        return True, "Fleet activated successfully."
    
    async def _deactivate_fleet(self) -> tuple[bool, str]:
        """Internal method to put fleet into idle mode."""
        try:
            self._status.state = FleetState.DEACTIVATING
            
            if self._use_direct_mode:
                return await self._deactivate_fleet_direct()
            else:
                return await self._deactivate_fleet_awesomeminer()
                
        except Exception as e:
            logger.error("Fleet deactivation failed", error=str(e))
            return False, f"Deactivation failed: {str(e)}"
    
    async def _deactivate_fleet_direct(self) -> tuple[bool, str]:
        """Put miners into idle mode using direct communication."""
        # Get ALL online miners, not just those marked as mining
        # because the is_mining status might be stale
        online_miners = [
            m for m in self.discovery.miners
            if m.is_online
        ]
        
        if not online_miners:
            self._status.state = FleetState.STANDBY
            return True, "No online miners found"
        
        # Put each miner into idle mode (even if they report not mining)
        # This ensures we definitely stop all mining activity
        success_count = 0
        fail_count = 0
        for miner in online_miners:
            ok, msg = await self.discovery.set_miner_idle(miner.id)
            if ok:
                success_count += 1
                logger.info("Miner set to idle", miner_id=miner.id, ip=miner.ip)
            else:
                fail_count += 1
                logger.warning("Failed to idle miner", miner_id=miner.id, error=msg)
        
        self._status.state = FleetState.STANDBY
        return True, f"Sent idle command to {success_count}/{len(online_miners)} miners (failed: {fail_count})"
    
    async def _deactivate_fleet_awesomeminer(self) -> tuple[bool, str]:
        """Stop mining using AwesomeMiner API (legacy)."""
        # Get all mining miners
        mining_miners = [
            m for m in self._status.miners
            if m.is_mining
        ]
        
        if not mining_miners:
            self._status.state = FleetState.STANDBY
            return True, "Fleet is already in standby mode."
        
        # Stop all miners
        miner_ids = [int(m.miner_id) for m in mining_miners]
        results = await self.am_client.stop_all_miners(miner_ids)
        
        stopped = sum(1 for v in results.values() if v)
        
        logger.info(
            "Miners stopped for deactivation",
            requested=len(miner_ids),
            stopped=stopped
        )
        
        self._status.state = FleetState.STANDBY
        return True, "Fleet deactivation command accepted."
    
    def _select_miners_for_power(
        self,
        miners: List[DiscoveredMiner],
        target_power_kw: float
    ) -> List[DiscoveredMiner]:
        """Select miners to activate to reach target power."""
        # Sort by rated power (smallest first for granularity)
        sorted_miners = sorted(miners, key=lambda m: m.rated_power_kw)
        
        selected = []
        cumulative_power = 0.0
        
        for miner in sorted_miners:
            if cumulative_power >= target_power_kw:
                break
            selected.append(miner)
            cumulative_power += miner.rated_power_kw
        
        return selected
    
    def _calculate_miners_to_start(
        self,
        available_miners: List[MinerState],
        target_power_kw: float
    ) -> List[MinerState]:
        """
        Calculate which miners to start to reach target power.
        
        Uses the configured distribution strategy.
        """
        if self._config.power_distribution_strategy == "priority":
            return self._priority_distribution(available_miners, target_power_kw)
        else:
            return self._even_distribution(available_miners, target_power_kw)
    
    def _even_distribution(
        self,
        miners: List[MinerState],
        target_power_kw: float
    ) -> List[MinerState]:
        """Select miners to evenly distribute power load."""
        # Sort by rated power (smallest first for better granularity)
        sorted_miners = sorted(miners, key=lambda m: m.rated_power_kw)
        
        selected = []
        cumulative_power = 0.0
        
        for miner in sorted_miners:
            if cumulative_power >= target_power_kw:
                break
            selected.append(miner)
            cumulative_power += miner.rated_power_kw
        
        return selected
    
    def _priority_distribution(
        self,
        miners: List[MinerState],
        target_power_kw: float
    ) -> List[MinerState]:
        """Select miners based on configured priority."""
        # Create priority map
        priority_map = {
            mid: idx for idx, mid in enumerate(self._config.miner_priority)
        }
        
        # Sort by priority (configured miners first, then by ID)
        sorted_miners = sorted(
            miners,
            key=lambda m: (priority_map.get(m.miner_id, 999999), m.miner_id)
        )
        
        selected = []
        cumulative_power = 0.0
        
        for miner in sorted_miners:
            if cumulative_power >= target_power_kw:
                break
            selected.append(miner)
            cumulative_power += miner.rated_power_kw
        
        return selected
    
    # =========================================================================
    # Manual Override
    # =========================================================================
    
    async def set_manual_override(
        self,
        enabled: bool,
        target_power_kw: Optional[float] = None
    ) -> tuple[bool, str]:
        """
        Enable or disable manual override mode.
        
        When enabled, EMS commands are ignored and the specified power
        target is maintained.
        
        Args:
            enabled: Whether to enable override
            target_power_kw: Target power when enabled (None means stop all)
            
        Returns:
            Tuple of (success, message)
        """
        async with self._lock:
            self._manual_override = enabled
            self._override_power_kw = target_power_kw if enabled else None
            
            # Persist to database
            self.db.set_setting("manual_override", enabled)
            self.db.set_setting("override_power_kw", target_power_kw if enabled else "")
            
            self._log_command(
                "dashboard",
                "override",
                {"enabled": enabled, "power_kw": target_power_kw}
            )
            
            # Log command to database
            self.db.log_command(
                command_type="override",
                source="dashboard",
                target="fleet",
                parameters={"enabled": enabled, "power_kw": target_power_kw},
                success=True
            )
            
            if enabled:
                logger.warning(
                    "Manual override enabled",
                    target_power=target_power_kw
                )
                if target_power_kw is not None and target_power_kw > 0:
                    result = await self._activate_fleet(target_power_kw)
                    return result
                else:
                    return await self._deactivate_fleet()
            else:
                logger.info("Manual override disabled")
                return True, "Manual override disabled. Resuming EMS control."
    
    # =========================================================================
    # Configuration
    # =========================================================================
    
    def update_config(self, **kwargs) -> SystemConfig:
        """Update runtime configuration."""
        for key, value in kwargs.items():
            if hasattr(self._config, key):
                setattr(self._config, key, value)
        
        logger.info("Configuration updated", **kwargs)
        return self._config
    
    # =========================================================================
    # Command Logging
    # =========================================================================
    
    def _log_command(
        self,
        source: str,
        command: str,
        parameters: Dict,
        success: bool = True,
        message: Optional[str] = None
    ):
        """Log a command to history."""
        log_entry = CommandLog(
            source=source,
            command=command,
            parameters=parameters,
            success=success,
            message=message
        )
        self._command_log.append(log_entry)
        
        # Keep only last 1000 entries
        if len(self._command_log) > 1000:
            self._command_log = self._command_log[-1000:]
    
    def get_command_history(self, limit: int = 100) -> List[CommandLog]:
        """Get recent command history."""
        return self._command_log[-limit:]


# Singleton instance
_fleet_manager: Optional[FleetManager] = None


def get_fleet_manager() -> FleetManager:
    """Get the singleton FleetManager instance."""
    global _fleet_manager
    if _fleet_manager is None:
        _fleet_manager = FleetManager()
    return _fleet_manager
