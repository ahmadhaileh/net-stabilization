"""
Maestro — orchestrates SectionManagers and distributes power targets.

The Maestro:
  1. Discovers all miners on the network
  2. Splits them into sections of ~35 miners (~50 kW each)
  3. On activate(kW): distributes target across managers
     - Fill managers to 50 kW each, last gets remainder
     - Reserve managers compensate when primaries underdeliver
  4. Runs a supervision loop that redistributes power from
     underperforming sections to reserves
  5. Reads the power meter for ground-truth fleet power
"""
from __future__ import annotations

import asyncio
import ipaddress
import math
from datetime import datetime
from typing import Dict, List, Optional, Tuple

import httpx
import structlog

from app.v2.miner import Miner, MinerState, VnishAPI, WATTS_PER_MINER
from app.v2.section_manager import SectionManager
from app.v2.power_meter import PowerMeterService

logger = structlog.get_logger()

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
SECTION_SIZE = 35              # miners per section (~50 kW)
SUPERVISION_INTERVAL = 15.0   # seconds between maestro supervision ticks
RESERVE_FRACTION = 0.10       # 10% extra capacity for compensation


class Maestro:
    """Top-level orchestrator for the mining fleet."""

    def __init__(self, network_cidr: str = "192.168.95.0/24",
                 vnish_username: str = "root", vnish_password: str = "root",
                 power_meter: Optional[PowerMeterService] = None,
                 idle_on_startup: bool = True):
        self._network = ipaddress.IPv4Network(network_cidr, strict=False)
        self._vnish = VnishAPI(username=vnish_username, password=vnish_password)
        self._power_meter = power_meter
        self._idle_on_startup = idle_on_startup

        self.managers: List[SectionManager] = []
        self._all_miners: Dict[str, Miner] = {}  # ip -> Miner

        # Fleet state
        self._target_kw: float = 0.0
        self._state: str = "standby"  # standby | activating | running | deactivating
        self._supervision_task: Optional[asyncio.Task] = None
        self._running = False

        # Meter readings
        self._measured_power_kw: Optional[float] = None
        self._plant_power_kw: Optional[float] = None
        self._voltage: Optional[float] = None

    # -- properties ---------------------------------------------------------

    @property
    def state(self) -> str:
        return self._state

    @property
    def target_kw(self) -> float:
        return self._target_kw

    @property
    def total_miners(self) -> int:
        return len(self._all_miners)

    @property
    def mining_count(self) -> int:
        return sum(m.mining_count for m in self.managers)

    @property
    def waking_count(self) -> int:
        return sum(m.waking_count for m in self.managers)

    @property
    def idle_count(self) -> int:
        return sum(m.idle_count for m in self.managers)

    @property
    def online_count(self) -> int:
        return sum(m.online_count for m in self.managers)

    @property
    def rated_kw(self) -> float:
        return sum(m.rated_kw for m in self.managers)

    @property
    def estimated_power_kw(self) -> float:
        """Sum of per-manager estimated power."""
        return sum(m.estimated_power_kw for m in self.managers)

    @property
    def active_power_kw(self) -> float:
        """Best available power reading: meter > estimate."""
        if self._measured_power_kw is not None:
            return self._measured_power_kw
        return self.estimated_power_kw

    @property
    def measured_power_kw(self) -> Optional[float]:
        return self._measured_power_kw

    @property
    def plant_power_kw(self) -> Optional[float]:
        return self._plant_power_kw

    @property
    def voltage(self) -> Optional[float]:
        return self._voltage

    @property
    def all_miners(self) -> List[Miner]:
        return list(self._all_miners.values())

    # -- lifecycle ----------------------------------------------------------

    async def start(self):
        """Discover miners, create sections, start everything."""
        logger.info("Maestro starting", network=str(self._network))

        # 1. Discover all miner IPs
        ips = await self._scan_network()
        logger.info("Network scan complete", hosts_found=len(ips))

        # 2. Create Miner objects
        self._all_miners = {ip: Miner(ip=ip) for ip in ips}

        # 3. Split into sections
        self._create_sections()

        # 4. Discover miner states (parallel per section)
        await asyncio.gather(*[m.discover() for m in self.managers])

        # 5. Idle all if configured
        if self._idle_on_startup:
            mining = [m for m in self._all_miners.values() if m.is_mining]
            if mining:
                logger.info("Idling miners on startup", count=len(mining))
                await asyncio.gather(
                    *[mgr.idle_all_miners() for mgr in self.managers])

        # 6. Start section loops
        for mgr in self.managers:
            await mgr.start()

        # 7. Start supervision
        self._running = True
        self._supervision_task = asyncio.create_task(
            self._supervision_loop(), name="maestro-supervision"
        )
        self._state = "standby"
        logger.info("Maestro ready",
                     sections=len(self.managers),
                     total_miners=self.total_miners,
                     rated_kw=round(self.rated_kw, 1))

    async def stop(self):
        self._running = False
        if self._supervision_task:
            self._supervision_task.cancel()
            await asyncio.gather(self._supervision_task, return_exceptions=True)
        for mgr in self.managers:
            await mgr.stop()
        logger.info("Maestro stopped")

    # -- network scan -------------------------------------------------------

    async def _scan_network(self) -> List[str]:
        """Scan the subnet for miners (check port 80 for Vnish web UI)."""
        hosts = [str(ip) for ip in self._network.hosts()]
        found: List[str] = []

        async def _check(ip: str):
            try:
                reader, writer = await asyncio.wait_for(
                    asyncio.open_connection(ip, 80), timeout=1.0
                )
                writer.close()
                await writer.wait_closed()
                found.append(ip)
            except Exception:
                pass

        # Scan in batches of 50
        for i in range(0, len(hosts), 50):
            batch = hosts[i:i + 50]
            await asyncio.gather(*[_check(ip) for ip in batch])

        found.sort(key=lambda ip: int(ip.split(".")[-1]))
        return found

    # -- section creation ---------------------------------------------------

    def _create_sections(self):
        """Split miners into sections of SECTION_SIZE."""
        ips = sorted(self._all_miners.keys(),
                     key=lambda ip: int(ip.split(".")[-1]))
        self.managers = []
        for i in range(0, len(ips), SECTION_SIZE):
            chunk = ips[i:i + SECTION_SIZE]
            name = chr(ord("A") + len(self.managers))  # A, B, C, ...
            miners = [self._all_miners[ip] for ip in chunk]
            mgr = SectionManager(name=name, miners=miners, vnish=self._vnish)
            self.managers.append(mgr)
            logger.info("Section created", section=name,
                         miners=len(chunk),
                         ip_range=f"{chunk[0]}–{chunk[-1]}")

    # -- activate / deactivate ----------------------------------------------

    async def activate(self, target_kw: float) -> Tuple[bool, str]:
        """Activate fleet at target power."""
        if target_kw <= 0:
            return False, "Power must be positive"
        if target_kw > self.rated_kw * 1.1:
            return False, f"Exceeds rated capacity ({self.rated_kw:.0f} kW)"

        self._target_kw = target_kw
        self._state = "activating"
        self._distribute_power(target_kw)

        sections_info = ", ".join(
            f"{m.name}={m.target_kw:.0f}kW" for m in self.managers
        )
        msg = (f"Target: {target_kw:.1f}kW across {len(self.managers)} sections "
               f"[{sections_info}]")
        logger.info("Fleet activating", target_kw=target_kw, sections=sections_info)
        return True, msg

    async def deactivate(self) -> Tuple[bool, str]:
        """Deactivate fleet — idle all sections."""
        self._target_kw = 0.0
        self._state = "deactivating"

        # Set all targets to 0 (regulation loops will sleep miners)
        for mgr in self.managers:
            mgr.target_kw = 0.0

        # Also immediately send sleep commands
        results = await asyncio.gather(
            *[mgr.idle_all_miners() for mgr in self.managers],
            return_exceptions=True
        )

        total = sum(mgr.total_count for mgr in self.managers)
        self._state = "standby"
        return True, f"Sent idle to {total} miners across {len(self.managers)} sections"

    def _distribute_power(self, total_kw: float):
        """Distribute target power across section managers.

        Strategy:
        - Fill sections to their rated capacity in order
        - Last section gets the remainder
        - Add reserve capacity (10%) distributed to sections that have room
        """
        remaining = total_kw
        reserve = total_kw * RESERVE_FRACTION

        for mgr in self.managers:
            if remaining <= 0:
                mgr.target_kw = 0.0
                continue
            alloc = min(remaining, mgr.rated_kw)
            mgr.target_kw = alloc
            remaining -= alloc

        # Distribute reserve: add a bit to sections that aren't maxed out
        if reserve > 0:
            for mgr in self.managers:
                headroom = mgr.rated_kw - mgr.target_kw
                if headroom > 0 and reserve > 0:
                    extra = min(headroom, reserve)
                    mgr.target_kw += extra
                    reserve -= extra

        logger.info("Power distributed",
                     allocations={m.name: round(m.target_kw, 1)
                                  for m in self.managers})

    # -- supervision loop ---------------------------------------------------

    async def _supervision_loop(self):
        """Periodic supervision: read meter, compensate underperformers,
        update fleet state."""
        while self._running:
            try:
                await self._supervise()
            except asyncio.CancelledError:
                return
            except Exception as e:
                logger.error("Supervision error", err=str(e))
            await asyncio.sleep(SUPERVISION_INTERVAL)

    async def _supervise(self):
        # 1. Read power meter
        await self._read_meter()

        # 2. Update fleet state
        if self._target_kw == 0:
            if self.mining_count == 0 and self.waking_count == 0:
                self._state = "standby"
            else:
                self._state = "deactivating"
        else:
            tolerance = self._target_kw * 0.05  # 5%
            if abs(self.active_power_kw - self._target_kw) <= tolerance:
                self._state = "running"
            elif self.active_power_kw < self._target_kw:
                self._state = "activating"
            else:
                self._state = "running"

        # 3. Compensate: find underperforming sections, shift to reserves
        if self._target_kw > 0:
            await self._compensate()

    async def _compensate(self):
        """If a section underdelivers, shift its deficit to sections
        with spare capacity."""
        total_deficit = 0.0
        for mgr in self.managers:
            if mgr.target_kw <= 0:
                continue
            delivered = mgr.estimated_power_kw
            pending = mgr.waking_count * WATTS_PER_MINER / 1000.0
            expected = delivered + pending
            if expected < mgr.target_kw * 0.8:  # delivering < 80%
                deficit = mgr.target_kw - expected
                total_deficit += deficit

        if total_deficit < WATTS_PER_MINER / 1000.0:
            return  # less than 1 miner deficit, skip

        # Find sections with spare capacity
        for mgr in self.managers:
            if total_deficit <= 0:
                break
            headroom = mgr.rated_kw - mgr.target_kw
            if headroom >= WATTS_PER_MINER / 1000.0:
                add = min(headroom, total_deficit)
                mgr.target_kw += add
                total_deficit -= add
                logger.info("Compensation: boosted section",
                            section=mgr.name,
                            added_kw=round(add, 1),
                            new_target=round(mgr.target_kw, 1))

    async def _read_meter(self):
        """Read physical power meter."""
        if not self._power_meter:
            self._measured_power_kw = None
            return
        reading = await self._power_meter.get_power()
        if reading:
            self._measured_power_kw = reading.miners_total_power_kw
            self._plant_power_kw = reading.plant_total_power_kw
            self._voltage = reading.voltage

    # -- status for API -----------------------------------------------------

    def status_dict(self) -> dict:
        """Full status for dashboard/API."""
        return {
            "state": self._state,
            "target_kw": round(self._target_kw, 2),
            "active_power_kw": round(self.active_power_kw, 2),
            "estimated_power_kw": round(self.estimated_power_kw, 2),
            "measured_power_kw": (round(self._measured_power_kw, 2)
                                  if self._measured_power_kw is not None else None),
            "plant_power_kw": (round(self._plant_power_kw, 2)
                               if self._plant_power_kw is not None else None),
            "voltage": (round(self._voltage, 1)
                        if self._voltage is not None else None),
            "rated_kw": round(self.rated_kw, 2),
            "total_miners": self.total_miners,
            "online_miners": self.online_count,
            "mining_miners": self.mining_count,
            "waking_miners": self.waking_count,
            "idle_miners": self.idle_count,
            "sections": [m.summary() for m in self.managers],
        }


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------
_maestro: Optional[Maestro] = None


def get_maestro() -> Maestro:
    global _maestro
    if _maestro is None:
        raise RuntimeError("Maestro not initialised — call create_maestro() first")
    return _maestro


def create_maestro(**kwargs) -> Maestro:
    global _maestro
    _maestro = Maestro(**kwargs)
    return _maestro
