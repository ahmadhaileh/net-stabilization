"""
Section Manager — manages a ~50 kW section of miners.

Each section owns a fixed set of miners. It can:
- Sleep / wake individual miners to hit a power target
- Poll its miners for status
- Report its current and rated power

Design:
- Fire-and-forget sleep/wake (per AM findings)
- Normal polling detects state changes
- Simple on/off control (no frequency scaling)
- Staggered wake with configurable delay between miners
- Per-miner ~1.4 kW (S9 on Vnish 3.9.0)
"""
import asyncio
from datetime import datetime, timedelta
from typing import Dict, List, Optional

import structlog

from app.services.miner_control import (
    Miner,
    MinerState,
    sleep_miner,
    wake_miner,
    poll_miner,
    discover_miners,
    identify_miner,
)

logger = structlog.get_logger()

# Conservative per-miner power for calculations.
# Real S9 draws ~1.35-1.45 kW; using 1.4 as default.
DEFAULT_PER_MINER_KW = 1.4


class SectionManager:
    """
    Manages a section of miners (~36 miners, ~50 kW).

    Responsibilities:
    - Owns a set of Miner objects
    - Polls them periodically
    - Accepts a power target and wakes/sleeps miners to match
    - Reports current state to the Maestro
    """

    def __init__(
        self,
        section_id: str,
        miner_ips: List[str],
        per_miner_kw: float = DEFAULT_PER_MINER_KW,
        wake_delay_seconds: float = 1.0,
    ):
        self.section_id = section_id
        self.per_miner_kw = per_miner_kw
        self.wake_delay_seconds = wake_delay_seconds

        # Create Miner objects for each IP
        self.miners: Dict[str, Miner] = {
            ip: Miner(ip=ip) for ip in miner_ips
        }

        # Current target (None = standby / sleep all)
        self._target_power_kw: Optional[float] = None

        # Pending wakes — IPs we sent wake to but haven't seen mining yet
        self._pending_wakes: Dict[str, datetime] = {}
        self._wake_grace_seconds = 180  # 3 min for boot + detection

        # Background tasks
        self._poll_task: Optional[asyncio.Task] = None
        self._regulate_task: Optional[asyncio.Task] = None

        # Concurrency limiter for miner commands
        self._cmd_semaphore = asyncio.Semaphore(20)

        logger.info(
            "section_created",
            section=section_id,
            miners=len(miner_ips),
            rated_kw=round(len(miner_ips) * per_miner_kw, 1),
        )

    # ── Properties ────────────────────────────────────────────────

    @property
    def rated_power_kw(self) -> float:
        """Maximum power this section can deliver."""
        return len(self.miners) * self.per_miner_kw

    @property
    def mining_miners(self) -> List[Miner]:
        return [m for m in self.miners.values() if m.state == MinerState.MINING]

    @property
    def sleeping_miners(self) -> List[Miner]:
        return [m for m in self.miners.values() if m.state == MinerState.SLEEPING]

    @property
    def available_miners(self) -> List[Miner]:
        """Miners that could be woken (sleeping + not backed off + not already pending)."""
        return [
            m for m in self.miners.values()
            if m.state == MinerState.SLEEPING
            and not m.is_wake_backed_off
            and m.ip not in self._pending_wakes
        ]

    @property
    def active_power_kw(self) -> float:
        """Current power from mining miners (estimated)."""
        return len(self.mining_miners) * self.per_miner_kw

    @property
    def expected_power_kw(self) -> float:
        """Power including pending wakes (for regulation decisions)."""
        active = len(self.mining_miners)
        pending = self._count_valid_pending()
        return (active + pending) * self.per_miner_kw

    @property
    def target_power_kw(self) -> Optional[float]:
        return self._target_power_kw

    @property
    def total_miners(self) -> int:
        return len(self.miners)

    @property
    def online_miners(self) -> int:
        return sum(1 for m in self.miners.values() if m.state != MinerState.OFFLINE)

    # ── Lifecycle ─────────────────────────────────────────────────

    async def start(self, poll_interval: float = 5.0, regulate_interval: float = 15.0):
        """Start background polling and regulation loops."""
        self._poll_task = asyncio.create_task(
            self._poll_loop(poll_interval), name=f"poll-{self.section_id}"
        )
        self._regulate_task = asyncio.create_task(
            self._regulate_loop(regulate_interval), name=f"regulate-{self.section_id}"
        )
        logger.info("section_started", section=self.section_id)

    async def stop(self):
        """Stop background tasks."""
        for task in (self._poll_task, self._regulate_task):
            if task and not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
        self._poll_task = None
        self._regulate_task = None
        logger.info("section_stopped", section=self.section_id)

    # ── Commands from Maestro ─────────────────────────────────────

    async def set_target(self, power_kw: float):
        """
        Set the power target for this section.

        The regulation loop will wake/sleep miners to converge.
        Setting 0 means sleep everything.
        """
        clamped = max(0.0, min(power_kw, self.rated_power_kw))
        old = self._target_power_kw
        self._target_power_kw = clamped
        logger.info(
            "section_target_set",
            section=self.section_id,
            target_kw=round(clamped, 1),
            old_kw=round(old, 1) if old is not None else None,
        )
        # Trigger immediate regulation instead of waiting for next cycle
        asyncio.create_task(self._regulate_once())

    async def deactivate(self):
        """Sleep all miners in this section."""
        self._target_power_kw = 0.0
        await self._sleep_all()

    async def do_initial_sleep(self):
        """Put all miners to sleep on startup (standby mode)."""
        self._target_power_kw = 0.0
        await self._sleep_all()

    # ── Polling ───────────────────────────────────────────────────

    async def _poll_loop(self, interval: float):
        """Periodically poll all miners for status."""
        while True:
            try:
                await self.poll_all()
            except Exception as e:
                logger.error("poll_error", section=self.section_id, error=str(e))
            await asyncio.sleep(interval)

    async def poll_all(self):
        """Poll all miners concurrently."""
        async def _poll_one(miner: Miner):
            old_state = miner.state
            new_state = await poll_miner(miner)

            # Detect wake success: was pending, now mining
            if miner.ip in self._pending_wakes and new_state == MinerState.MINING:
                del self._pending_wakes[miner.ip]
                miner.clear_wake_failures()
                logger.info("miner_wake_confirmed", ip=miner.ip, section=self.section_id)

            # Detect wake failure: pending too long
            if miner.ip in self._pending_wakes:
                wake_time = self._pending_wakes[miner.ip]
                if (datetime.utcnow() - wake_time).total_seconds() > self._wake_grace_seconds:
                    del self._pending_wakes[miner.ip]
                    miner.record_wake_failure()
                    logger.warning(
                        "miner_wake_timeout",
                        ip=miner.ip,
                        section=self.section_id,
                        failures=miner.consecutive_wake_failures,
                    )

        tasks = [_poll_one(m) for m in self.miners.values()]
        await asyncio.gather(*tasks, return_exceptions=True)

    # ── Regulation ────────────────────────────────────────────────

    async def _regulate_loop(self, interval: float):
        """Periodically adjust fleet to match target."""
        while True:
            try:
                await self._regulate_once()
            except Exception as e:
                logger.error("regulate_error", section=self.section_id, error=str(e))
            await asyncio.sleep(interval)

    async def _regulate_once(self):
        """One regulation cycle: wake or sleep miners to match target."""
        target = self._target_power_kw
        if target is None:
            return  # No target set, do nothing

        miners_needed = max(0, round(target / self.per_miner_kw))
        mining_count = len(self.mining_miners)
        pending_count = self._count_valid_pending()
        expected_count = mining_count + pending_count

        if target == 0:
            # Sleep everything
            if mining_count > 0 or pending_count > 0:
                await self._sleep_all()
            return

        # Tolerance: ±1 miner
        if abs(expected_count - miners_needed) <= 1:
            return  # Close enough

        if expected_count > miners_needed + 1:
            # Too many — trim down
            excess = expected_count - miners_needed
            await self._trim_miners(excess)
        elif expected_count < miners_needed - 1:
            # Too few — wake more
            deficit = miners_needed - expected_count
            await self._wake_miners(deficit)

    async def _wake_miners(self, count: int):
        """Wake `count` sleeping miners with staggered delay."""
        candidates = self.available_miners
        to_wake = candidates[:count]

        if not to_wake:
            logger.debug("no_miners_to_wake", section=self.section_id, needed=count)
            return

        logger.info(
            "waking_miners",
            section=self.section_id,
            count=len(to_wake),
            ips=[m.ip for m in to_wake],
        )

        for i, miner in enumerate(to_wake):
            if i > 0 and self.wake_delay_seconds > 0:
                await asyncio.sleep(self.wake_delay_seconds)

            async with self._cmd_semaphore:
                ok = await wake_miner(miner.ip)
                if ok:
                    miner.last_command_time = datetime.utcnow()
                    miner.last_command = "wake"
                    self._pending_wakes[miner.ip] = datetime.utcnow()

    async def _trim_miners(self, count: int):
        """Sleep `count` mining miners. Cancel pending wakes first (cheaper)."""
        trimmed = 0

        # First: cancel pending wakes (free — miner hasn't booted yet or just started)
        pending_ips = list(self._pending_wakes.keys())
        for ip in pending_ips:
            if trimmed >= count:
                break
            async with self._cmd_semaphore:
                ok = await sleep_miner(ip)
                if ok:
                    self._pending_wakes.pop(ip, None)
                    miner = self.miners.get(ip)
                    if miner:
                        miner.last_command_time = datetime.utcnow()
                        miner.last_command = "sleep"
                    trimmed += 1

        if trimmed >= count:
            return

        # Then: sleep actual mining miners (sorted by IP, trim from end)
        mining = sorted(self.mining_miners, key=lambda m: m.ip, reverse=True)
        for miner in mining:
            if trimmed >= count:
                break
            if miner.is_transitioning:
                continue
            async with self._cmd_semaphore:
                ok = await sleep_miner(miner.ip)
                if ok:
                    miner.last_command_time = datetime.utcnow()
                    miner.last_command = "sleep"
                    trimmed += 1

        logger.info("trimmed_miners", section=self.section_id, trimmed=trimmed, requested=count)

    async def _sleep_all(self):
        """Sleep all non-sleeping miners."""
        self._pending_wakes.clear()
        to_sleep = [
            m for m in self.miners.values()
            if m.state != MinerState.SLEEPING and m.state != MinerState.OFFLINE
        ]
        if not to_sleep:
            return

        logger.info("sleeping_all", section=self.section_id, count=len(to_sleep))

        async def _do_sleep(miner: Miner):
            async with self._cmd_semaphore:
                ok = await sleep_miner(miner.ip)
                if ok:
                    miner.last_command_time = datetime.utcnow()
                    miner.last_command = "sleep"

        await asyncio.gather(*[_do_sleep(m) for m in to_sleep], return_exceptions=True)

    # ── Helpers ───────────────────────────────────────────────────

    def _count_valid_pending(self) -> int:
        """Count pending wakes that haven't expired."""
        now = datetime.utcnow()
        expired = [
            ip for ip, t in self._pending_wakes.items()
            if (now - t).total_seconds() > self._wake_grace_seconds
        ]
        for ip in expired:
            del self._pending_wakes[ip]
        return len(self._pending_wakes)

    def get_status(self) -> dict:
        """Return section status for the Maestro / API."""
        return {
            "section_id": self.section_id,
            "total_miners": self.total_miners,
            "online_miners": self.online_miners,
            "mining_miners": len(self.mining_miners),
            "sleeping_miners": len(self.sleeping_miners),
            "pending_wakes": len(self._pending_wakes),
            "rated_power_kw": round(self.rated_power_kw, 1),
            "active_power_kw": round(self.active_power_kw, 1),
            "expected_power_kw": round(self.expected_power_kw, 1),
            "target_power_kw": round(self._target_power_kw, 1) if self._target_power_kw is not None else None,
        }
