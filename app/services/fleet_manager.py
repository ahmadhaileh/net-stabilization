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
        
        # Service mode
        self._use_direct_mode = self.settings.miner_discovery_enabled
        
        # Services
        if self._use_direct_mode:
            self.discovery = discovery_service or get_discovery_service()
            self.discovery.network_cidr = self.settings.miner_network_cidr
            self.am_client = None
            logger.info("Fleet manager using DIRECT miner communication mode")
        else:
            self.am_client = am_client or get_awesome_miner_client()
            self.discovery = None
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
        
        # Target power for activation (set by EMS)
        self._target_power_kw: Optional[float] = None
        
        # Manual override flag
        self._manual_override = False
        self._override_power_kw: Optional[float] = None
    
    @property
    def status(self) -> FleetStatus:
        """Get current fleet status."""
        return self._status
    
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
        
        # Start periodic discovery if in direct mode
        if self._use_direct_mode and self.settings.auto_discovery_on_startup:
            # Run initial discovery
            logger.info("Running initial miner discovery...")
            await self.run_discovery()
            
            # Start periodic discovery
            if self._discovery_task is None or self._discovery_task.done():
                self._discovery_task = asyncio.create_task(self._discovery_loop())
    
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
            
            if self._use_direct_mode:
                return await self._activate_fleet_direct(target_power_kw)
            else:
                return await self._activate_fleet_awesomeminer(target_power_kw)
                
        except Exception as e:
            logger.error("Fleet activation failed", error=str(e))
            return False, f"Activation failed: {str(e)}"
    
    async def _activate_fleet_direct(self, target_power_kw: float) -> tuple[bool, str]:
        """
        Activate miners using direct communication with proportional power control.
        
        Strategy:
        1. Calculate how many miners to turn on to meet target power
        2. Turn on/off miners as needed (coarse control)
        3. Frequency scaling for fine control is applied separately after miners stabilize
        
        For S9 miners: each is ~1.4 kW at full power.
        - If target is 1.0 kW and we have 2 miners, turn on 1 miner
        - If target is 2.0 kW, turn on 2 miners
        - Fine-grained control via frequency is optional enhancement
        """
        all_miners = [m for m in self.discovery.miners if m.is_online]
        
        if not all_miners:
            return False, "No online miners available"
        
        # Sort miners by rated power (smallest first for better granularity)
        sorted_miners = sorted(all_miners, key=lambda m: m.rated_power_kw)
        
        # Get currently mining miners
        currently_mining = {m.id for m in all_miners if m.is_mining}
        
        logger.info(
            "Proportional power activation",
            target_kw=target_power_kw,
            total_miners=len(all_miners),
            currently_mining=len(currently_mining)
        )
        
        # Calculate which miners should be on/off to hit target
        # Strategy: Add miners until we reach or exceed target, but don't overshoot
        # by more than 50% of the last miner's power
        cumulative_power = 0.0
        miners_to_activate = []
        miners_to_deactivate = []
        
        for miner in sorted_miners:
            # Check if adding this miner would overshoot significantly
            power_after_adding = cumulative_power + miner.rated_power_kw
            
            if cumulative_power >= target_power_kw:
                # Already at or above target - turn off remaining miners
                if miner.id in currently_mining:
                    miners_to_deactivate.append(miner)
            elif power_after_adding <= target_power_kw * 1.2:
                # Adding this miner keeps us within 20% of target - activate it
                miners_to_activate.append(miner)
                cumulative_power = power_after_adding
            elif cumulative_power == 0:
                # No miners yet - must activate at least one if any power requested
                miners_to_activate.append(miner)
                cumulative_power = power_after_adding
            else:
                # Adding this miner would overshoot too much
                # Check if we're closer to target with or without it
                undershoot = target_power_kw - cumulative_power
                overshoot = power_after_adding - target_power_kw
                
                if overshoot < undershoot:
                    # Overshooting is closer to target - activate
                    miners_to_activate.append(miner)
                    cumulative_power = power_after_adding
                else:
                    # Stay under target - don't activate, turn off if running
                    if miner.id in currently_mining:
                        miners_to_deactivate.append(miner)
        
        # Apply changes
        success_count = 0
        total_changes = 0
        
        # Turn ON miners that should be active but aren't
        for miner in miners_to_activate:
            if miner.id not in currently_mining:
                total_changes += 1
                ok, msg = await self.discovery.set_miner_active(miner.id)
                if ok:
                    success_count += 1
                    logger.info("Miner activated", miner_id=miner.id, ip=miner.ip)
                else:
                    logger.warning("Failed to activate miner", miner_id=miner.id, error=msg)
        
        # Turn OFF miners that should be idle
        for miner in miners_to_deactivate:
            total_changes += 1
            ok, msg = await self.discovery.set_miner_idle(miner.id)
            if ok:
                success_count += 1
                logger.info("Miner set to idle", miner_id=miner.id, ip=miner.ip)
            else:
                logger.warning("Failed to idle miner", miner_id=miner.id, error=msg)
        
        if total_changes == 0:
            # No changes needed - miners already in correct state
            self._status.state = FleetState.RUNNING
            return True, f"Fleet already at target power (~{cumulative_power:.1f} kW)"
        
        if success_count == 0:
            return False, "Failed to apply any power changes"
        
        self._status.state = FleetState.RUNNING
        estimated_power = sum(m.rated_power_kw for m in miners_to_activate)
        return True, f"Activated {len(miners_to_activate)} miners for ~{estimated_power:.1f} kW (target: {target_power_kw:.1f} kW)"
    
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
        # Get all mining miners
        mining_miners = [
            m for m in self.discovery.miners
            if m.is_mining
        ]
        
        if not mining_miners:
            self._status.state = FleetState.STANDBY
            return True, "Fleet is already in standby/idle mode"
        
        # Put each miner into idle mode
        success_count = 0
        for miner in mining_miners:
            ok, msg = await self.discovery.set_miner_idle(miner.id)
            if ok:
                success_count += 1
                logger.info("Miner set to idle", miner_id=miner.id, ip=miner.ip)
            else:
                logger.warning("Failed to idle miner", miner_id=miner.id, error=msg)
        
        self._status.state = FleetState.STANDBY
        return True, f"Put {success_count}/{len(mining_miners)} miners into idle mode"
    
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
            
            self._log_command(
                "dashboard",
                "override",
                {"enabled": enabled, "power_kw": target_power_kw}
            )
            
            if enabled:
                logger.warning(
                    "Manual override enabled",
                    target_power=target_power_kw
                )
                if target_power_kw is not None and target_power_kw > 0:
                    return await self._activate_fleet(target_power_kw)
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
