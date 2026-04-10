"""
Maestro — top-level orchestrator that translates EMS commands into section targets.

Architecture (process-isolated):
  Main Process (uvicorn + FastAPI):
    Maestro
      ├── SectionProcess(section-1) → separate OS process
      ├── SectionProcess(section-2) → separate OS process
      ├── SectionProcess(section-3) → separate OS process
      ├── SectionProcess(section-4) → separate OS process
      └── SectionProcess(section-5) → separate OS process

  EMS → POST /api/activate {110 kW}
  Maestro → Section A: 50 kW (full), Section B: 50 kW (full), Section C: 10 kW (partial)

The Maestro:
- Owns all SectionProcesses (never touches individual miners)
- Splits the fleet into ~50 kW sections based on IP ranges
- Translates activate/deactivate into per-section targets
- Provides aggregated status for the EMS API
- Uses power meter for ground-truth measurement
- Records snapshots for historical charts

Command funnel:  Maestro → SectionProcess → SectionManager → Miner
"""
import asyncio
import math
from datetime import datetime
from typing import Optional

import structlog

from app.config import get_settings
from app.services.section_process import SectionProcess
from app.services.power_meter import PowerMeterService, get_power_meter_service
from app.services.miner_control import discover_miners
from app.models.ems import RunningStatus
from app.models.state import FleetState

logger = structlog.get_logger()

# How many miners per section (~50 kW at 1.4 kW each)
MINERS_PER_SECTION = 36
DEFAULT_PER_MINER_KW = 1.4


class Maestro:
    """
    Top-level orchestrator for the mining fleet.

    Translates EMS activate/deactivate commands into per-section power targets.
    Each section runs as an independent OS process — the Maestro never touches
    individual miners, only sends commands to section processes.
    """

    def __init__(self, per_miner_kw: float = DEFAULT_PER_MINER_KW):
        self.settings = get_settings()
        self.per_miner_kw = per_miner_kw
        self.sections: list[SectionProcess] = []
        self.power_meter: PowerMeterService = get_power_meter_service()

        # State
        self._state: FleetState = FleetState.UNKNOWN
        self._target_power_kw: Optional[float] = None
        self._last_ems_command: Optional[datetime] = None

        # Lock for state changes (asyncio lock protects against concurrent EMS calls)
        self._lock = asyncio.Lock()

        # Background tasks
        self._meter_task: Optional[asyncio.Task] = None
        self._snapshot_task: Optional[asyncio.Task] = None
        self._last_meter_kw: Optional[float] = None
        self._last_plant_kw: Optional[float] = None
        self._last_voltage: Optional[float] = None

    # ── Lifecycle ─────────────────────────────────────────────────

    async def initialize(self):
        """Discover miners and create section processes (not started yet)."""
        logger.info("maestro_initializing", network=self.settings.miner_network_cidr)

        # Discover all miners on the network (one-time, in main process)
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
            section = SectionProcess(
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
        """Start all section processes and background tasks."""
        # Start each section as an independent OS process
        for section in self.sections:
            section.start()
            logger.info(
                "section_process_launched",
                section=section.section_id,
                miners=len(section._miner_ips),
            )

        # Give processes a moment to initialize
        await asyncio.sleep(1.0)

        # Initial idle — sleep everything on startup
        if self.settings.idle_all_on_startup:
            logger.info("maestro_initial_sleep")
            for section in self.sections:
                section.do_initial_sleep()

        # Start power meter polling (stays in main process)
        self._meter_task = asyncio.create_task(
            self._meter_loop(), name="maestro-meter"
        )

        # Start snapshot recording (stays in main process)
        self._snapshot_task = asyncio.create_task(
            self._snapshot_loop(), name="maestro-snapshots"
        )

        logger.info("maestro_started", sections=len(self.sections))

    async def stop(self):
        """Stop all section processes and background tasks."""
        for section in self.sections:
            section.stop()

        for task in (self._meter_task, self._snapshot_task):
            if task and not task.done():
                task.cancel()
                try:
                    await task
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

            # Distribute to sections (fill to capacity in order)
            remaining = clamped
            for section in self.sections:
                if remaining <= 0:
                    section.set_target(0)
                else:
                    section_target = min(remaining, section.rated_power_kw)
                    section.set_target(section_target)
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
            section.deactivate()

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
        return sum(s.get_status().get("active_power_kw", 0) for s in self.sections)

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
        section_statuses = [s.get_status() for s in self.sections]

        total = sum(ss.get("total_miners", 0) for ss in section_statuses)
        online = sum(ss.get("online_miners", 0) for ss in section_statuses)
        mining = sum(ss.get("mining_miners", 0) for ss in section_statuses)
        sleeping = sum(ss.get("sleeping_miners", 0) for ss in section_statuses)

        return {
            "state": self._state.value,
            "is_available_for_dispatch": self.is_available,
            "running_status": self.running_status.value,
            "rated_power_kw": round(self.rated_power_kw, 1),
            "active_power_kw": round(self.active_power_kw, 1),
            "measured_power_kw": round(self._last_meter_kw, 1) if self._last_meter_kw is not None else None,
            "plant_power_kw": round(self._last_plant_kw, 1) if self._last_plant_kw is not None else None,
            "voltage": round(self._last_voltage, 1) if self._last_voltage is not None else None,
            "target_power_kw": round(self._target_power_kw, 1) if self._target_power_kw is not None else None,
            "total_miners": total,
            "online_miners": online,
            "mining_miners": mining,
            "sleeping_miners": sleeping,
            "sections": section_statuses,
            "last_ems_command": self._last_ems_command.isoformat() if self._last_ems_command else None,
        }

    def find_section_for_miner(self, ip: str) -> Optional[SectionProcess]:
        """Find which section owns a given miner IP."""
        for section in self.sections:
            status = section.get_status()
            for miner in status.get("miners", []):
                if miner.get("ip") == ip:
                    return section
        return None

    # ── Power Meter ───────────────────────────────────────────────

    async def _meter_loop(self):
        """Poll the power meter every few seconds."""
        while True:
            try:
                reading = await self.power_meter.get_power()
                if reading:
                    self._last_meter_kw = reading.miners_total_power_kw
                    self._last_plant_kw = reading.plant_total_power_kw
                    self._last_voltage = reading.voltage

                    # Power loss detection: voltage=0 → emergency deactivate
                    if reading.voltage == 0 and self._state == FleetState.RUNNING:
                        logger.error("power_loss_detected", voltage=0)
                        async with self._lock:
                            await self._do_deactivate()
            except Exception as e:
                logger.warning("meter_error", error=str(e))
            await asyncio.sleep(5)

    # ── Snapshot Recording ────────────────────────────────────────

    async def _snapshot_loop(self):
        """Record fleet snapshots periodically for historical charts."""
        interval = self.settings.snapshot_interval_seconds
        while True:
            try:
                await asyncio.sleep(interval)
                self._record_snapshot()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.warning("snapshot_error", error=str(e))

    def _record_snapshot(self):
        """Save a fleet snapshot to the database."""
        try:
            from app.database import get_db_service
            db = get_db_service()

            status = self.get_status()

            # Aggregate miner stats from sections
            total_hashrate_ghs = 0
            total_power_watts = 0
            temps = []
            for ss in status.get("sections", []):
                for m in ss.get("miners", []):
                    total_hashrate_ghs += m.get("hashrate_ghs", 0)
                    total_power_watts += m.get("power_watts", 0)
                    t = m.get("temperature_c", 0)
                    if t > 0:
                        temps.append(t)

            db.save_fleet_snapshot(
                total_hashrate_ghs=round(total_hashrate_ghs, 1),
                total_power_watts=round(total_power_watts, 1),
                avg_temperature=round(sum(temps) / len(temps), 1) if temps else None,
                miners_online=status.get("online_miners", 0),
                miners_mining=status.get("mining_miners", 0),
                miners_total=status.get("total_miners", 0),
                fleet_state=status.get("state"),
                measured_power_kw=status.get("measured_power_kw"),
                plant_power_kw=status.get("plant_power_kw"),
                voltage=status.get("voltage"),
                target_power_kw=status.get("target_power_kw"),
            )

            # Also save per-miner snapshots
            miner_snaps = []
            for ss in status.get("sections", []):
                for m in ss.get("miners", []):
                    if m.get("is_online"):
                        miner_snaps.append({
                            "miner_ip": m["ip"],
                            "hashrate_ghs": m.get("hashrate_ghs", 0),
                            "power_watts": m.get("power_watts", 0),
                            "temperature": m.get("temperature_c", 0),
                            "fan_speed": int(m.get("fan_speed_pct", 0)),
                            "is_mining": m.get("is_mining", False),
                            "uptime_seconds": m.get("uptime_seconds", 0),
                        })
            if miner_snaps:
                db.save_miner_snapshots_batch(miner_snaps)

        except Exception as e:
            logger.warning("record_snapshot_failed", error=str(e))


# ── Singleton ─────────────────────────────────────────────────────

_maestro: Optional[Maestro] = None


def get_maestro() -> Maestro:
    """Get the singleton Maestro instance."""
    global _maestro
    if _maestro is None:
        _maestro = Maestro()
    return _maestro
