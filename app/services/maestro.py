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
from typing import Optional, List

import structlog

from app.config import get_settings
from app.services.section_process import SectionProcess
from app.services.power_meter import PowerMeterService, get_power_meter_service
from app.services.miner_control import discover_miners_with_power
from app.models.ems import RunningStatus
from app.models.state import FleetState

logger = structlog.get_logger()

# How many miners per section
MINERS_PER_SECTION = 25


class Maestro:
    """
    Top-level orchestrator for the mining fleet.

    Translates EMS activate/deactivate commands into per-section power targets.
    Each section runs as an independent OS process — the Maestro never touches
    individual miners, only sends commands to section processes.
    """

    def __init__(self):
        self.settings = get_settings()
        self.per_miner_kw: float = 0.0  # Computed dynamically from discovery
        self.sections: list[SectionProcess] = []
        self.power_meter: PowerMeterService = get_power_meter_service()

        # State
        self._state: FleetState = FleetState.UNKNOWN
        self._target_power_kw: Optional[float] = None
        self._last_ems_command: Optional[datetime] = None

        # Command history for dashboard
        self._command_history: List[dict] = []

        # Lock for state changes (asyncio lock protects against concurrent EMS calls)
        self._lock = asyncio.Lock()

        # Background tasks
        self._meter_task: Optional[asyncio.Task] = None
        self._snapshot_task: Optional[asyncio.Task] = None
        self._last_meter_kw: Optional[float] = None
        self._last_plant_kw: Optional[float] = None
        self._last_voltage: Optional[float] = None

        # Power loss debounce: require multiple consecutive voltage=0 readings
        # to avoid false deactivation from transient meter glitches.
        self._consecutive_voltage_zero: int = 0
        self._voltage_zero_threshold: int = 3  # 3 × 5s = 15s sustained

        # Background tasks (continued)
        self._rediscovery_task: Optional[asyncio.Task] = None
        self._known_ips: set[str] = set()  # IPs already assigned to sections

        # Closed-loop meter feedback — corrects for per_miner_kw inaccuracy.
        # When the meter shows less power than the target, we increase section
        # targets proportionally so they wake more miners.
        self._feedback_effective_target: Optional[float] = None  # Current corrected target
        self._feedback_correction_factor: float = 1.2  # Learned ratio — safety net for boot failures (~20%)
        self._last_feedback_time: Optional[datetime] = None
        self._activation_start_time: Optional[datetime] = None  # When fleet first went RUNNING
        self._feedback_warmup_seconds: int = 120  # Wait for miners to actually boot (~90s boot + buffer)
        self._feedback_cooldown_seconds: int = 20  # Min time between adjustments

    # ── Lifecycle ─────────────────────────────────────────────────

    async def initialize(self):
        """Discover miners and create section processes (not started yet)."""
        logger.info("maestro_initializing", network=self.settings.miner_network_cidr)

        # Discover all miners on the network and probe their model/power
        miner_power = await discover_miners_with_power(
            self.settings.miner_network_cidr,
            timeout=self.settings.miner_scan_timeout,
        )

        if not miner_power:
            logger.warning("no_miners_found")
            self._state = FleetState.FAULT
            return

        # Compute fleet-average per_miner_kw from actual discovered values
        self.per_miner_kw = sum(miner_power.values()) / len(miner_power)

        # Track all managed IPs
        self._known_ips = set(miner_power.keys())

        ips = sorted(miner_power.keys())

        logger.info(
            "miners_discovered_with_power",
            total=len(ips),
            avg_per_miner_kw=round(self.per_miner_kw, 3),
            models_kw={ip: round(kw, 2) for ip, kw in miner_power.items()},
        )

        # Split into sections of MINERS_PER_SECTION
        self.sections = []
        for i in range(0, len(ips), MINERS_PER_SECTION):
            chunk = ips[i : i + MINERS_PER_SECTION]
            # Build per-miner power map for this section
            section_power_map = {ip: miner_power[ip] for ip in chunk}
            section_id = f"section-{len(self.sections) + 1}"
            section = SectionProcess(
                section_id=section_id,
                miner_power_map=section_power_map,
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

        # Start periodic re-discovery (picks up new miners added to the network)
        self._rediscovery_task = asyncio.create_task(
            self._rediscovery_loop(), name="maestro-rediscovery"
        )

        logger.info("maestro_started", sections=len(self.sections))

    async def stop(self):
        """Stop all section processes and background tasks."""
        for section in self.sections:
            section.stop()

        for task in (self._meter_task, self._snapshot_task, self._rediscovery_task):
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

            # Check for sustained power loss (debounced)
            if self._consecutive_voltage_zero >= self._voltage_zero_threshold:
                return False, "Power loss detected (sustained voltage=0), cannot activate"

            clamped = min(target_power_kw, rated)
            old_target = self._target_power_kw
            was_running = self._state == FleetState.RUNNING
            self._target_power_kw = clamped
            self._last_ems_command = datetime.utcnow()

            if clamped < 1.0:
                # Effectively zero — deactivate
                return await self._do_deactivate()

            if was_running:
                # ── Retarget while running ──
                # Key rule: NEVER reduce a section below its current target
                # when the new EMS target is >= old target.  This prevents
                # the "dip" where miners get needlessly put to sleep.
                self._distribute_retarget(clamped, old_target)
            else:
                # Fresh activation from standby
                # Preserve learned correction_factor — it carries over from
                # previous runs so we dispatch the right count immediately.
                self._feedback_effective_target = None
                self._last_feedback_time = None
                self._activation_start_time = datetime.utcnow()
                self._distribute_fresh(clamped)

            self._state = FleetState.RUNNING

            logger.info(
                "maestro_activate",
                target_kw=round(clamped, 1),
                sections_active=sum(
                    1 for s in self.sections if (s.target_power_kw or 0) > 0
                ),
            )
            return True, f"Activating at {clamped:.1f} kW"

    # ── Target Distribution ───────────────────────────────────────

    def _distribute_fresh(self, target_kw: float):
        """Distribute target EVENLY across all sections for a fresh activation.

        Even distribution ensures all sections start waking miners simultaneously,
        maximizing parallelism and reducing time to target.
        """
        # Apply learned correction factor (compensates boot failures + per_miner_kw gap)
        effective = min(target_kw * self._feedback_correction_factor, self.rated_power_kw)
        logger.info(
            "fresh_activation_distribute",
            raw_target_kw=round(target_kw, 1),
            correction_factor=round(self._feedback_correction_factor, 3),
            effective_kw=round(effective, 1),
        )
        self._feedback_effective_target = effective

        # Spread evenly across all sections, capping at each section's rated power
        n = len(self.sections)
        per_section = effective / n
        for section in self.sections:
            section_target = min(per_section, section.rated_power_kw)
            section.set_target(section_target)

    def _distribute_retarget(self, new_target_kw: float, old_target_kw: float):
        """
        Redistribute power when retargeting while already running.

        Key invariant: when raising the target, NEVER reduce any section's
        current target.  This prevents miners from being put to sleep and
        then immediately re-woken (the "dip").

        When lowering the target, scale down proportionally so no single
        section bears all the reduction.
        """
        # Get current section targets (what sections are actually working toward)
        current_targets = []
        for s in self.sections:
            st = s.get_status()
            current_targets.append(st.get("target_power_kw") or 0.0)

        current_total = sum(current_targets)

        # Apply learned correction factor to the new target
        if self._feedback_correction_factor > 1.0:
            effective = min(
                new_target_kw * self._feedback_correction_factor,
                self.rated_power_kw,
            )
        else:
            effective = new_target_kw

        self._feedback_effective_target = effective
        # Short cooldown before feedback loop refines further
        self._last_feedback_time = datetime.utcnow()

        logger.info(
            "maestro_retarget",
            old_target_kw=round(old_target_kw or 0, 1),
            new_target_kw=round(new_target_kw, 1),
            correction_factor=round(self._feedback_correction_factor, 3),
            effective_kw=round(effective, 1),
            current_section_total_kw=round(current_total, 1),
            current_section_targets=[round(t, 1) for t in current_targets],
        )

        if effective >= current_total:
            # ── Raising or maintaining: keep existing targets, distribute extra ──
            extra = effective - current_total
            for i, section in enumerate(self.sections):
                headroom = section.rated_power_kw - current_targets[i]
                add = min(extra, headroom)
                if add > 0.5:
                    new_st = current_targets[i] + add
                    section.set_target(new_st)
                    extra -= add
                # If add <= 0.5, don't bother sending a command — no change
        else:
            # ── Lowering: scale down proportionally ──
            if current_total > 0:
                scale = effective / current_total
            else:
                scale = 0.0
            for i, section in enumerate(self.sections):
                new_st = current_targets[i] * scale
                if new_st < 1.0:
                    new_st = 0.0
                section.set_target(new_st)

    async def deactivate(self) -> tuple[bool, str]:
        """Deactivate the fleet — sleep all sections."""
        async with self._lock:
            self._last_ems_command = datetime.utcnow()
            return await self._do_deactivate()

    async def _do_deactivate(self) -> tuple[bool, str]:
        """Internal deactivation (must hold lock)."""
        self._target_power_kw = 0.0
        self._feedback_effective_target = None
        self._last_feedback_time = None
        # Preserve _feedback_correction_factor — learned ratio carries over
        # so future activations dispatch the right number of miners immediately.
        self._activation_start_time = None
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
    def estimated_power_kw(self) -> float:
        """Sum of per-miner power estimates from section processes."""
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
            "estimated_power_kw": round(self.estimated_power_kw, 1),
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

    # ── Command History ───────────────────────────────────────────

    def log_command(self, command: str, source: str, parameters: dict = None, success: bool = True, message: str = ""):
        """Record a command in the history ring buffer."""
        entry = {
            "timestamp": datetime.utcnow().isoformat(),
            "command": command,
            "source": source,
            "parameters": parameters or {},
            "success": success,
            "message": message,
        }
        self._command_history.append(entry)
        if len(self._command_history) > 500:
            self._command_history = self._command_history[-500:]

    def get_command_history(self, limit: int = 20) -> List[dict]:
        """Return most recent commands, newest first."""
        return list(reversed(self._command_history[-limit:]))

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
                    # Debounced: require multiple consecutive readings to avoid
                    # false positives from transient meter communication errors.
                    if reading.voltage == 0:
                        self._consecutive_voltage_zero += 1
                        if (
                            self._consecutive_voltage_zero >= self._voltage_zero_threshold
                            and self._state == FleetState.RUNNING
                        ):
                            logger.error(
                                "power_loss_detected",
                                voltage=0,
                                consecutive=self._consecutive_voltage_zero,
                            )
                            async with self._lock:
                                await self._do_deactivate()
                    else:
                        self._consecutive_voltage_zero = 0

                        # Closed-loop feedback: adjust section targets when meter
                        # shows we're not hitting the EMS target.
                        if self._state == FleetState.RUNNING and self._target_power_kw:
                            self._apply_meter_feedback()
            except Exception as e:
                logger.warning("meter_error", error=str(e))
            await asyncio.sleep(5)

    def _apply_meter_feedback(self):
        """
        Closed-loop meter feedback: compare measured power to EMS target
        and redistribute section targets to compensate.

        CRITICAL RULE: When meter < target (undershooting), we ONLY increase
        section targets — never decrease.  Decreasing while undershooting
        causes the wake/sleep oscillation that destroys convergence.

        Only scale DOWN sections when meter > target (truly overshooting).
        """
        target = self._target_power_kw
        raw_meter = self._last_meter_kw
        if not target or target <= 0 or raw_meter is None:
            return

        # Subtract idle baseline: the meter reads ALL miner power including
        # sleeping miners' PSU standby draw.  The EMS target is *mining* power
        # only, so we must remove the idle floor before comparing.
        # Idle draw is ~1.1% of rated power (measured across S9/S19 PSUs).
        total_sleeping = sum(
            s.get_status().get("sleeping_miners", 0) for s in self.sections
        )
        idle_per_miner_kw = self.per_miner_kw * 0.011  # ~1.1% of rated: 25W for 2.25kW, 15W for 1.4kW
        idle_baseline_kw = total_sleeping * idle_per_miner_kw
        meter = max(0.0, raw_meter - idle_baseline_kw)

        # Warmup: wait for miners to actually boot (90s+ boot time)
        if self._activation_start_time:
            elapsed = (datetime.utcnow() - self._activation_start_time).total_seconds()
            if elapsed < self._feedback_warmup_seconds:
                return

        # Don't adjust until meaningful number of miners are online
        total_mining = sum(
            s.get_status().get("mining_miners", 0) for s in self.sections
        )
        if total_mining < 10:
            return

        # Cooldown: don't adjust more often than every N seconds
        if self._last_feedback_time:
            since_last = (datetime.utcnow() - self._last_feedback_time).total_seconds()
            if since_last < self._feedback_cooldown_seconds:
                return

        tolerance_kw = target * 0.05  # 5% tolerance band (matches test criteria)
        error = target - meter  # positive = undershooting, negative = overshooting

        if abs(error) <= tolerance_kw:
            return  # Within tolerance — nothing to do

        # Update correction factor with conservative smoothing.
        # alpha=0.3 prevents wild swings — each iteration moves only 30%
        # toward the ideal correction, retaining 70% of previous estimate.
        if meter > 5.0:
            instant_correction = target / meter
            instant_correction = max(0.85, min(instant_correction, 1.2))
            alpha = 0.3
            self._feedback_correction_factor = (
                alpha * instant_correction
                + (1 - alpha) * self._feedback_correction_factor
            )

        effective = target * self._feedback_correction_factor
        effective = min(effective, self.rated_power_kw)

        # Don't redistribute if effective target barely changed
        current_effective = self._feedback_effective_target or target
        if abs(effective - current_effective) < 1.0:
            return

        self._feedback_effective_target = effective
        self._last_feedback_time = datetime.utcnow()

        logger.info(
            "meter_feedback_adjust",
            target_kw=round(target, 1),
            raw_meter_kw=round(raw_meter, 1),
            meter_minus_idle_kw=round(meter, 1),
            idle_baseline_kw=round(idle_baseline_kw, 1),
            error_kw=round(error, 1),
            correction_factor=round(self._feedback_correction_factor, 3),
            effective_target_kw=round(effective, 1),
            mining_count=total_mining,
        )

        # Get current section targets
        current_targets = []
        for s in self.sections:
            st = s.get_status()
            current_targets.append(st.get("target_power_kw") or 0.0)
        current_total = sum(current_targets)

        if error > 0:
            # ── UNDERSHOOTING (meter < target): only INCREASE, never reduce ──
            if effective > current_total:
                extra = effective - current_total
                n = len(self.sections)
                per_section_add = extra / n
                for i, section in enumerate(self.sections):
                    headroom = section.rated_power_kw - current_targets[i]
                    add = min(per_section_add, headroom)
                    if add > 0.5:
                        section.set_target(current_targets[i] + add)
            # If effective <= current_total but meter < target, do NOTHING.
            # The miners just need more time to come online.
        else:
            # ── OVERSHOOTING (meter > target): scale down proportionally ──
            if current_total > 0:
                scale = effective / current_total
                scale = max(scale, 0.5)  # Never cut more than 50% at once
            else:
                scale = 0.0
            for i, section in enumerate(self.sections):
                new_st = current_targets[i] * scale
                if new_st < 1.0:
                    new_st = 0.0
                section.set_target(new_st)

    # ── Periodic Re-Discovery ────────────────────────────────────

    async def _rediscovery_loop(self):
        """Periodically scan the network for new miners and add them to sections."""
        interval = 60  # Re-scan every 60 seconds
        while True:
            try:
                await asyncio.sleep(interval)
                await self._rediscover_miners()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.warning("rediscovery_error", error=str(e))

    async def _rediscover_miners(self):
        """Discover new miners and create sections for them."""
        miner_power = await discover_miners_with_power(
            self.settings.miner_network_cidr,
            timeout=self.settings.miner_scan_timeout,
        )
        if not miner_power:
            return

        # Find miners not yet assigned to any section
        new_miners = {
            ip: kw for ip, kw in miner_power.items()
            if ip not in self._known_ips
        }

        if not new_miners:
            return

        logger.info(
            "new_miners_discovered",
            count=len(new_miners),
            ips=sorted(new_miners.keys()),
        )

        # Create new sections for the new miners
        new_ips = sorted(new_miners.keys())
        new_sections: list[SectionProcess] = []

        for i in range(0, len(new_ips), MINERS_PER_SECTION):
            chunk = new_ips[i : i + MINERS_PER_SECTION]
            section_power_map = {ip: new_miners[ip] for ip in chunk}
            section_id = f"section-{len(self.sections) + len(new_sections) + 1}"
            section = SectionProcess(
                section_id=section_id,
                miner_power_map=section_power_map,
            )
            new_sections.append(section)

        # Start the new section processes
        for section in new_sections:
            section.start()
            logger.info(
                "section_process_launched",
                section=section.section_id,
                miners=len(section._miner_ips),
            )

        # Give them a moment to initialize, then sleep all new miners
        await asyncio.sleep(1.0)
        for section in new_sections:
            section.do_initial_sleep()

        # Register them
        async with self._lock:
            self.sections.extend(new_sections)
            self._known_ips.update(new_miners.keys())

            # Recompute fleet-average per_miner_kw from all known miners
            all_power = {ip: miner_power[ip] for ip in self._known_ips if ip in miner_power}
            if all_power:
                self.per_miner_kw = sum(all_power.values()) / len(all_power)

        logger.info(
            "sections_expanded",
            new_sections=len(new_sections),
            new_miners=len(new_miners),
            total_sections=len(self.sections),
            total_miners=sum(len(s._miner_ips) for s in self.sections),
            rated_kw=round(self.rated_power_kw, 1),
            avg_per_miner_kw=round(self.per_miner_kw, 3),
        )

        # If we're currently running (active EMS target), distribute power
        # to the new sections too
        if self._state == FleetState.RUNNING and self._target_power_kw:
            self._distribute_retarget(self._target_power_kw, self._target_power_kw)

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
