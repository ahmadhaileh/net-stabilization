"""
Maestro — top-level orchestrator that translates EMS commands into section targets.

Architecture:
  EMS → POST /api/activate {110 kW}
  Maestro → Section A: 50 kW (full), Section B: 50 kW (full), Section C: 10 kW (partial)

The Maestro:
- Owns all SectionManagers
- Splits the fleet into ~50 kW sections based on IP ranges
- Translates activate/deactivate into per-section targets
- Provides aggregated status for the EMS API
- Uses power meter for ground-truth measurement
- Handles reserve: overshoots slightly to account for wake failures

Section allocation strategy:
  1. Fill sections to capacity in order
  2. Last section gets the remainder
  3. Reserve sections can absorb shortfall from failed wakes
"""
import asyncio
import math
from datetime import datetime
from typing import Optional

import structlog

from app.config import get_settings
from app.services.section_manager import SectionManager, DEFAULT_PER_MINER_KW
from app.services.power_meter import PowerMeterService, get_power_meter_service
from app.services.miner_control import discover_miners, Miner, MinerState
from app.models.ems import RunningStatus
from app.models.state import FleetState

logger = structlog.get_logger()

# How many miners per section (~50 kW at 1.4 kW each)
MINERS_PER_SECTION = 36


class Maestro:
    """
    Top-level orchestrator for the mining fleet.

    Translates EMS activate/deactivate commands into per-section power targets.
    """

    def __init__(self, per_miner_kw: float = DEFAULT_PER_MINER_KW):
        self.settings = get_settings()
        self.per_miner_kw = per_miner_kw
        self.sections: list[SectionManager] = []
        self.power_meter: PowerMeterService = get_power_meter_service()

        # State
        self._state: FleetState = FleetState.UNKNOWN
        self._target_power_kw: Optional[float] = None
        self._last_ems_command: Optional[datetime] = None

        # Lock for state changes
        self._lock = asyncio.Lock()

        # Background tasks
        self._meter_task: Optional[asyncio.Task] = None
        self._last_meter_kw: Optional[float] = None
        self._last_voltage: Optional[float] = None

    # ── Lifecycle ─────────────────────────────────────────────────

    async def initialize(self):
        """Discover miners and create sections."""
        logger.info("maestro_initializing", network=self.settings.miner_network_cidr)

        # Discover all miners on the network
        ips = await discover_miners(
            self.settings.miner_network_cidr,
            timeout=self.settings.miner_scan_timeout,
        )

        if not ips:
            logger.warning("no_miners_found")
            self._state = FleetState.FAULT
            return

        # Split into sections of MINERS_PER_SECTION
        self.sections = []
        for i in range(0, len(ips), MINERS_PER_SECTION):
            chunk = ips[i : i + MINERS_PER_SECTION]
            section_id = f"section-{len(self.sections) + 1}"
            section = SectionManager(
                section_id=section_id,
                miner_ips=chunk,
                per_miner_kw=self.per_miner_kw,
            )
            self.sections.append(section)

        logger.info(
            "maestro_sections_created",
            total_miners=len(ips),
            sections=len(self.sections),
            rated_kw=round(self.rated_power_kw, 1),
        )

        self._state = FleetState.STANDBY

    async def start(self):
        """Start all section managers and the meter polling loop."""
        # Start sections (poll + regulate loops)
        for section in self.sections:
            await section.start(
                poll_interval=self.settings.poll_interval_seconds,
                regulate_interval=15.0,
            )

        # Start power meter polling
        self._meter_task = asyncio.create_task(
            self._meter_loop(), name="maestro-meter"
        )

        # Initial idle — sleep everything on startup
        if self.settings.idle_all_on_startup:
            logger.info("maestro_initial_sleep")
            for section in self.sections:
                await section.do_initial_sleep()

        logger.info("maestro_started")

    async def stop(self):
        """Stop all sections and background tasks."""
        for section in self.sections:
            await section.stop()
        if self._meter_task and not self._meter_task.done():
            self._meter_task.cancel()
            try:
                await self._meter_task
            except asyncio.CancelledError:
                pass
        logger.info("maestro_stopped")

    # ── EMS Commands ──────────────────────────────────────────────

    async def activate(self, target_power_kw: float) -> tuple[bool, str]:
        """
        Activate the fleet at the requested power level.

        Distributes power across sections:
        - Fill sections to capacity in order
        - Last section gets the remainder
        """
        async with self._lock:
            if not self.sections:
                return False, "No sections available"

            if target_power_kw < 0:
                return False, "Power cannot be negative"

            rated = self.rated_power_kw
            if target_power_kw > rated * 1.05:
                return False, f"Requested {target_power_kw:.1f} kW exceeds rated {rated:.1f} kW"

            # Check for power loss
            if self._last_voltage is not None and self._last_voltage == 0:
                return False, "Power loss detected (voltage=0), cannot activate"

            clamped = min(target_power_kw, rated)
            self._target_power_kw = clamped
            self._last_ems_command = datetime.utcnow()

            if clamped < 1.0:
                # Effectively zero — deactivate
                return await self._do_deactivate()

            # Distribute to sections
            remaining = clamped
            for section in self.sections:
                if remaining <= 0:
                    await section.set_target(0)
                else:
                    section_target = min(remaining, section.rated_power_kw)
                    await section.set_target(section_target)
                    remaining -= section_target

            self._state = FleetState.RUNNING

            logger.info(
                "maestro_activate",
                target_kw=round(clamped, 1),
                sections_active=sum(
                    1 for s in self.sections if (s.target_power_kw or 0) > 0
                ),
            )
            return True, f"Activating at {clamped:.1f} kW"

    async def deactivate(self) -> tuple[bool, str]:
        """Deactivate the fleet — sleep all sections."""
        async with self._lock:
            self._last_ems_command = datetime.utcnow()
            return await self._do_deactivate()

    async def _do_deactivate(self) -> tuple[bool, str]:
        """Internal deactivation (must hold lock)."""
        self._target_power_kw = 0.0
        self._state = FleetState.DEACTIVATING

        for section in self.sections:
            await section.deactivate()

        self._state = FleetState.STANDBY
        logger.info("maestro_deactivated")
        return True, "Fleet deactivated"

    # ── Status ────────────────────────────────────────────────────

    @property
    def rated_power_kw(self) -> float:
        return sum(s.rated_power_kw for s in self.sections)

    @property
    def active_power_kw(self) -> float:
        """Best estimate of current power: meter reading or sum of sections."""
        if self._last_meter_kw is not None:
            return self._last_meter_kw
        return sum(s.active_power_kw for s in self.sections)

    @property
    def is_available(self) -> bool:
        return self._state in (FleetState.STANDBY, FleetState.RUNNING)

    @property
    def running_status(self) -> RunningStatus:
        if self._state == FleetState.RUNNING:
            return RunningStatus.RUNNING
        return RunningStatus.STANDBY

    def get_status(self) -> dict:
        """Aggregated status for the EMS API and dashboard."""
        total = sum(s.total_miners for s in self.sections)
        online = sum(s.online_miners for s in self.sections)
        mining = sum(len(s.mining_miners) for s in self.sections)
        sleeping = sum(len(s.sleeping_miners) for s in self.sections)

        return {
            "state": self._state.value,
            "is_available_for_dispatch": self.is_available,
            "running_status": self.running_status.value,
            "rated_power_kw": round(self.rated_power_kw, 1),
            "active_power_kw": round(self.active_power_kw, 1),
            "measured_power_kw": round(self._last_meter_kw, 1) if self._last_meter_kw is not None else None,
            "voltage": round(self._last_voltage, 1) if self._last_voltage is not None else None,
            "target_power_kw": round(self._target_power_kw, 1) if self._target_power_kw is not None else None,
            "total_miners": total,
            "online_miners": online,
            "mining_miners": mining,
            "sleeping_miners": sleeping,
            "sections": [s.get_status() for s in self.sections],
            "last_ems_command": self._last_ems_command.isoformat() if self._last_ems_command else None,
        }

    # ── Power Meter ───────────────────────────────────────────────

    async def _meter_loop(self):
        """Poll the power meter every few seconds."""
        while True:
            try:
                reading = await self.power_meter.get_power()
                if reading:
                    self._last_meter_kw = reading.miners_total_power_kw
                    self._last_voltage = reading.voltage

                    # Power loss detection: voltage=0 → emergency deactivate
                    if reading.voltage == 0 and self._state == FleetState.RUNNING:
                        logger.error("power_loss_detected", voltage=0)
                        async with self._lock:
                            await self._do_deactivate()
            except Exception as e:
                logger.warning("meter_error", error=str(e))
            await asyncio.sleep(5)


# ── Singleton ─────────────────────────────────────────────────────

_maestro: Optional[Maestro] = None


def get_maestro() -> Maestro:
    """Get the singleton Maestro instance."""
    global _maestro
    if _maestro is None:
        _maestro = Maestro()
    return _maestro
