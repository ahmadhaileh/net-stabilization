"""
SectionManager — manages a small group of miners (~35, ~50 kW).

Each manager runs its own:
  - poll loop   (check miner status every few seconds)
  - poke loop   (stimulate waking miners with HTTP requests)
  - regulation  (wake/sleep miners to reach target power)

Because each section is small, we avoid the fleet-wide activation
stagnation that occurs when trying to wake 170+ miners at once.
"""
from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Dict, List, Optional, Tuple

import httpx
import structlog

from app.v2.miner import Miner, MinerState, VnishAPI, WATTS_PER_MINER

logger = structlog.get_logger()

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
POLL_INTERVAL = 5.0          # seconds between status polls
POKE_INTERVAL = 3.0          # seconds between pokes for waking miners
WAKE_TIMEOUT = 300.0         # give up on a miner after 5 min
REGULATION_INTERVAL = 10.0   # seconds between regulation ticks


class SectionManager:
    """Manages a named section of miners with its own regulation loop."""

    def __init__(self, name: str, miners: List[Miner], vnish: VnishAPI):
        self.name = name
        self.miners: Dict[str, Miner] = {m.ip: m for m in miners}
        self._vnish = vnish

        # Target power set by maestro (0 = idle this section)
        self._target_kw: float = 0.0

        # HTTP client shared across all operations in this section
        self._client: Optional[httpx.AsyncClient] = None
        self._tasks: List[asyncio.Task] = []
        self._running = False

        # Metrics
        self._last_regulation: Optional[datetime] = None

    # -- properties ---------------------------------------------------------

    @property
    def target_kw(self) -> float:
        return self._target_kw

    @target_kw.setter
    def target_kw(self, value: float):
        self._target_kw = max(0.0, value)

    @property
    def current_power_kw(self) -> float:
        return sum(m.power_watts for m in self.miners.values() if m.is_mining) / 1000.0

    @property
    def mining_count(self) -> int:
        return sum(1 for m in self.miners.values() if m.is_mining)

    @property
    def waking_count(self) -> int:
        return sum(1 for m in self.miners.values() if m.is_waking)

    @property
    def idle_count(self) -> int:
        return sum(1 for m in self.miners.values() if m.is_idle)

    @property
    def online_count(self) -> int:
        return sum(1 for m in self.miners.values()
                   if m.state != MinerState.OFFLINE)

    @property
    def total_count(self) -> int:
        return len(self.miners)

    @property
    def rated_kw(self) -> float:
        """Max power if every miner in this section is mining."""
        return len(self.miners) * WATTS_PER_MINER / 1000.0

    @property
    def estimated_power_kw(self) -> float:
        """Power based on mining + waking counts (for maestro planning)."""
        return self.mining_count * WATTS_PER_MINER / 1000.0

    def summary(self) -> dict:
        return {
            "name": self.name,
            "target_kw": round(self._target_kw, 2),
            "current_kw": round(self.current_power_kw, 2),
            "estimated_kw": round(self.estimated_power_kw, 2),
            "mining": self.mining_count,
            "waking": self.waking_count,
            "idle": self.idle_count,
            "offline": sum(1 for m in self.miners.values()
                          if m.state == MinerState.OFFLINE),
            "total": self.total_count,
        }

    # -- lifecycle ----------------------------------------------------------

    async def start(self):
        if self._running:
            return
        self._running = True
        self._client = httpx.AsyncClient(
            timeout=8.0,
            limits=httpx.Limits(max_connections=40, max_keepalive_connections=20),
        )
        self._tasks = [
            asyncio.create_task(self._poll_loop(), name=f"{self.name}-poll"),
            asyncio.create_task(self._poke_loop(), name=f"{self.name}-poke"),
            asyncio.create_task(self._regulation_loop(), name=f"{self.name}-reg"),
        ]
        logger.info("Section started", section=self.name,
                     miners=self.total_count)

    async def stop(self):
        self._running = False
        for t in self._tasks:
            t.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()
        if self._client:
            await self._client.aclose()
            self._client = None
        logger.info("Section stopped", section=self.name)

    # -- poll loop ----------------------------------------------------------

    async def _poll_loop(self):
        """Poll every miner for current status."""
        while self._running:
            try:
                await self._poll_all()
            except asyncio.CancelledError:
                return
            except Exception as e:
                logger.error("Poll error", section=self.name, err=str(e))
            await asyncio.sleep(POLL_INTERVAL)

    async def _poll_all(self):
        """Concurrent poll of all miners in this section."""
        if not self._client:
            return
        tasks = [self._poll_one(m) for m in self.miners.values()]
        await asyncio.gather(*tasks, return_exceptions=True)

    async def _poll_one(self, miner: Miner):
        """Poll a single miner and update its state."""
        status = await self._vnish.poll(miner.ip, self._client)
        if status is None:
            # Can't reach Vnish web → check if it's a waking miner (pokes will handle)
            if not miner.is_waking:
                # Try to determine if web server is even up
                info = await self._vnish.get_system_info(miner.ip, self._client)
                if info is not None:
                    # Web up but CGMiner not running → idle
                    miner.mark_idle()
                elif miner.state != MinerState.OFFLINE:
                    miner.mark_offline()
            return

        ghs = status["ghsav"]
        if ghs > 100:  # mining (S9 does ~13500 GH/s)
            miner.mark_mining(
                ghs=ghs,
                power_w=status["power_w"],
                temp=status["temp"],
                fan=status["fan"],
                uptime=status["elapsed"],
            )
        else:
            # Web responds but no hashrate → idle or still waking
            if miner.is_waking:
                pass  # poke loop handles this
            else:
                miner.mark_idle()

    # -- poke loop ----------------------------------------------------------

    async def _poke_loop(self):
        """Send HTTP pokes to all waking miners to stimulate CGMiner restart."""
        while self._running:
            try:
                waking = [m for m in self.miners.values() if m.is_waking]
                if waking:
                    expired = []
                    poke_targets = []
                    for m in waking:
                        if m.waking_for_seconds > WAKE_TIMEOUT:
                            expired.append(m)
                        else:
                            poke_targets.append(m)

                    for m in expired:
                        logger.warning("Wake timeout", section=self.name,
                                       ip=m.ip, after_s=round(m.waking_for_seconds))
                        m.mark_idle()  # give up, return to idle pool

                    if poke_targets:
                        tasks = [self._vnish.poke(m.ip, self._client)
                                 for m in poke_targets]
                        await asyncio.gather(*tasks, return_exceptions=True)
                        for m in poke_targets:
                            m._poke_count += 1
            except asyncio.CancelledError:
                return
            except Exception as e:
                logger.error("Poke error", section=self.name, err=str(e))
            await asyncio.sleep(POKE_INTERVAL)

    # -- regulation loop ----------------------------------------------------

    async def _regulation_loop(self):
        """Adjust running miner count to match target power."""
        while self._running:
            try:
                await self._regulate()
            except asyncio.CancelledError:
                return
            except Exception as e:
                logger.error("Regulation error", section=self.name, err=str(e))
            await asyncio.sleep(REGULATION_INTERVAL)

    async def _regulate(self):
        """One regulation tick: wake or sleep miners to approach target."""
        target = self._target_kw
        per_miner_kw = WATTS_PER_MINER / 1000.0

        # How many miners should be running?
        miners_needed = round(target / per_miner_kw) if target > 0 else 0
        currently_running = self.mining_count
        currently_waking = self.waking_count
        effective = currently_running + currently_waking

        if miners_needed == 0 and target == 0:
            # Idle everything
            if currently_running > 0 or currently_waking > 0:
                await self._idle_all()
            return

        tolerance = max(1, round(miners_needed * 0.05))  # 5% or at least 1

        if effective < miners_needed - tolerance:
            # Need more miners
            deficit = miners_needed - effective
            await self._wake_n(deficit)
        elif currently_running > miners_needed + tolerance and currently_waking == 0:
            # Too many miners — only trim if nothing is waking
            surplus = currently_running - miners_needed
            await self._sleep_n(surplus)

        self._last_regulation = datetime.utcnow()

    # -- wake / sleep helpers -----------------------------------------------

    async def _wake_n(self, n: int):
        """Wake up to n idle miners."""
        idle = [m for m in self.miners.values() if m.is_idle]
        to_wake = idle[:n]
        if not to_wake:
            return

        tasks = [self._wake_one(m) for m in to_wake]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        ok = sum(1 for r in results if r is True)
        logger.info("Woke miners", section=self.name, requested=n,
                     sent=len(to_wake), ok=ok)

    async def _wake_one(self, miner: Miner) -> bool:
        success = await self._vnish.wake(miner.ip, self._client)
        if success:
            miner.mark_waking()
        return success

    async def _sleep_n(self, n: int):
        """Put up to n mining miners to sleep."""
        mining = [m for m in self.miners.values() if m.is_mining]
        to_sleep = mining[:n]
        if not to_sleep:
            return

        tasks = [self._sleep_one(m) for m in to_sleep]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        ok = sum(1 for r in results if r is True)
        logger.info("Slept miners", section=self.name, requested=n,
                     sent=len(to_sleep), ok=ok)

    async def _sleep_one(self, miner: Miner) -> bool:
        success = await self._vnish.sleep(miner.ip, self._client)
        if success:
            miner.mark_idle()
        return success

    async def _idle_all(self):
        """Sleep every running/waking miner in this section."""
        active = [m for m in self.miners.values()
                  if m.is_mining or m.is_waking]
        if not active:
            return
        tasks = [self._sleep_one(m) for m in active]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        ok = sum(1 for r in results if r is True)
        logger.info("Idled section", section=self.name,
                     sent=len(active), ok=ok)

    # -- bulk operations called by maestro ----------------------------------

    async def idle_all_miners(self):
        """Public: immediately idle this entire section."""
        self._target_kw = 0.0
        await self._idle_all()

    async def discover(self):
        """Initial discovery: poll each miner once to determine state."""
        if not self._client:
            self._client = httpx.AsyncClient(
                timeout=8.0,
                limits=httpx.Limits(max_connections=40,
                                    max_keepalive_connections=20),
            )
        tasks = [self._discover_one(m) for m in self.miners.values()]
        await asyncio.gather(*tasks, return_exceptions=True)
        logger.info("Discovery complete", section=self.name,
                     total=self.total_count,
                     mining=self.mining_count,
                     idle=self.idle_count,
                     offline=sum(1 for m in self.miners.values()
                                 if m.state == MinerState.OFFLINE))

    async def _discover_one(self, miner: Miner):
        """Check miner state + collect model/firmware info."""
        # First try Vnish status (gets hashrate if mining)
        status = await self._vnish.poll(miner.ip, self._client)
        if status and status["ghsav"] > 100:
            miner.mark_mining(
                ghs=status["ghsav"], power_w=status["power_w"],
                temp=status["temp"], fan=status["fan"],
                uptime=status["elapsed"],
            )
        elif status is not None:
            miner.mark_idle()
        else:
            # poll failed — check if web server at least responds
            info = await self._vnish.get_system_info(miner.ip, self._client)
            if info:
                miner.mark_idle()
                miner.model = info.get("minertype", "")
                miner.mac_address = info.get("macaddr", "")
            else:
                miner.mark_offline()

        # Get model info if we don't have it yet
        if not miner.model and miner.state != MinerState.OFFLINE:
            info = await self._vnish.get_system_info(miner.ip, self._client)
            if info:
                miner.model = info.get("minertype", "")
                miner.mac_address = info.get("macaddr", "")
                ver = info.get("bmminer_version", "")
                if ver:
                    miner.firmware_version = ver
