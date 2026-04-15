"""
Section Process — runs a SectionManager in an isolated OS process.

Each section gets its own:
- Python process (no GIL contention)
- asyncio event loop (no scheduling interference)
- HTTP client pool (no connection sharing)
- Memory space (complete isolation)

Architecture:
  Maestro (main process)
    ├── SectionProcess(section-1)  →  subprocess with own event loop
    ├── SectionProcess(section-2)  →  subprocess with own event loop
    ├── SectionProcess(section-3)  →  subprocess with own event loop
    ├── SectionProcess(section-4)  →  subprocess with own event loop
    └── SectionProcess(section-5)  →  subprocess with own event loop

Communication via multiprocessing.Queue:
  cmd_queue:    Maestro → Section  (set_target, deactivate, control_miner, stop)
  status_queue: Section → Maestro  (periodic status with per-miner data)
"""
import asyncio
import logging
import multiprocessing as mp
import sys
from datetime import datetime
from queue import Empty
from typing import Any, Dict, List, Optional

import structlog


class SectionProcess:
    """
    Proxy that runs a SectionManager in a separate OS process.

    All public methods are non-blocking and safe to call from
    the main process's asyncio event loop.
    """

    def __init__(
        self,
        section_id: str,
        miner_ips: List[str],
        per_miner_kw: float = 1.5,
    ):
        self.section_id = section_id
        self._miner_ips = miner_ips
        self._per_miner_kw = per_miner_kw

        self._cmd_queue: mp.Queue = mp.Queue()
        self._status_queue: mp.Queue = mp.Queue()
        self._process: Optional[mp.Process] = None

        # Cached status (updated by draining status_queue)
        self._cached_status: Dict[str, Any] = {
            "section_id": section_id,
            "total_miners": len(miner_ips),
            "online_miners": 0,
            "mining_miners": 0,
            "sleeping_miners": 0,
            "pending_wakes": 0,
            "rated_power_kw": round(len(miner_ips) * per_miner_kw, 1),
            "active_power_kw": 0.0,
            "expected_power_kw": 0.0,
            "target_power_kw": None,
            "miners": [],
            "process_alive": False,
        }

    # ── Lifecycle ─────────────────────────────────────────────────

    def start(self):
        """Start the section as an independent OS process."""
        self._process = mp.Process(
            target=_section_worker,
            args=(
                self.section_id,
                self._miner_ips,
                self._per_miner_kw,
                self._cmd_queue,
                self._status_queue,
            ),
            name=f"section-{self.section_id}",
            daemon=True,
        )
        self._process.start()
        self._cached_status["process_alive"] = True

    def stop(self):
        """Send stop command and wait for subprocess to exit."""
        try:
            self._cmd_queue.put_nowait({"cmd": "stop"})
        except Exception:
            pass
        if self._process and self._process.is_alive():
            self._process.join(timeout=10)
            if self._process.is_alive():
                self._process.terminate()
        self._cached_status["process_alive"] = False

    # ── Commands (non-blocking, fire-and-forget) ──────────────────

    def set_target(self, power_kw: float):
        """Set power target for this section."""
        self._cmd_queue.put_nowait({"cmd": "set_target", "power_kw": power_kw})

    def deactivate(self):
        """Sleep all miners in this section."""
        self._cmd_queue.put_nowait({"cmd": "deactivate"})

    def do_initial_sleep(self):
        """Put all miners to sleep on startup."""
        self._cmd_queue.put_nowait({"cmd": "initial_sleep"})

    def control_miner(self, ip: str, action: str):
        """Manual control of a specific miner (start/stop)."""
        self._cmd_queue.put_nowait(
            {"cmd": "control_miner", "ip": ip, "action": action}
        )

    # ── Status (non-blocking read) ────────────────────────────────

    def get_status(self) -> Dict[str, Any]:
        """
        Return latest section status including per-miner details.

        Non-blocking: drains all pending updates and returns the most recent.
        """
        self._drain_status()
        return dict(self._cached_status)

    def _drain_status(self):
        """Drain all pending status messages, keep the latest."""
        latest = None
        try:
            while True:
                latest = self._status_queue.get_nowait()
        except Empty:
            pass
        if latest is not None:
            latest["process_alive"] = (
                self._process.is_alive() if self._process else False
            )
            self._cached_status = latest

    # ── Properties ────────────────────────────────────────────────

    @property
    def rated_power_kw(self) -> float:
        return len(self._miner_ips) * self._per_miner_kw

    @property
    def target_power_kw(self) -> Optional[float]:
        return self._cached_status.get("target_power_kw")

    @property
    def is_alive(self) -> bool:
        return self._process.is_alive() if self._process else False


# ══════════════════════════════════════════════════════════════════
# Subprocess entry point (runs in a completely separate OS process)
# ══════════════════════════════════════════════════════════════════


def _section_worker(
    section_id: str,
    miner_ips: List[str],
    per_miner_kw: float,
    cmd_queue: mp.Queue,
    status_queue: mp.Queue,
):
    """
    Subprocess entry point.

    This function runs in its own process with its own Python interpreter,
    event loop, and memory space. No shared state with the parent.
    """
    _configure_subprocess_logging(section_id)
    logger = structlog.get_logger()

    try:
        logger.info(
            "section_process_starting",
            section=section_id,
            pid=mp.current_process().pid,
            miners=len(miner_ips),
        )
        asyncio.run(
            _section_main(section_id, miner_ips, per_miner_kw, cmd_queue, status_queue)
        )
    except KeyboardInterrupt:
        pass
    except Exception as e:
        logger.error("section_process_fatal", section=section_id, error=str(e))


def _configure_subprocess_logging(section_id: str):
    """Configure structured logging for the subprocess."""
    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=logging.INFO,
    )
    structlog.configure(
        processors=[
            structlog.stdlib.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.stdlib.BoundLogger,
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=False,  # Each process gets fresh loggers
    )


async def _section_main(
    section_id: str,
    miner_ips: List[str],
    per_miner_kw: float,
    cmd_queue: mp.Queue,
    status_queue: mp.Queue,
):
    """
    Main coroutine for the section subprocess.

    Creates a SectionManager and runs:
    - Poll loop (status updates from miners)
    - Regulate loop (wake/sleep to match target)
    - Command loop (receive commands from Maestro)
    - Status loop (push status back to Maestro)
    """
    from app.services.section_manager import SectionManager
    from app.services.miner_control import wake_miner, sleep_miner
    from app.config import get_settings

    logger = structlog.get_logger()
    settings = get_settings()

    # Create the SectionManager — entirely local to this process
    section = SectionManager(
        section_id=section_id,
        miner_ips=miner_ips,
        per_miner_kw=per_miner_kw,
    )

    # Start poll + regulate loops (they use THIS process's event loop)
    await section.start(
        poll_interval=settings.poll_interval_seconds,
        regulate_interval=10.0,
    )

    running = True

    async def command_loop():
        """Listen for commands from the Maestro."""
        nonlocal running
        while running:
            try:
                while True:
                    cmd = cmd_queue.get_nowait()
                    cmd_type = cmd.get("cmd")

                    if cmd_type == "set_target":
                        await section.set_target(cmd["power_kw"])
                    elif cmd_type == "deactivate":
                        await section.deactivate()
                    elif cmd_type == "initial_sleep":
                        await section.do_initial_sleep()
                    elif cmd_type == "control_miner":
                        ip = cmd["ip"]
                        action = cmd["action"]
                        if action == "start":
                            await wake_miner(ip)
                        elif action == "stop":
                            await sleep_miner(ip)
                    elif cmd_type == "stop":
                        running = False
                        return
            except Empty:
                pass
            await asyncio.sleep(0.1)

    async def status_loop():
        """Periodically push status back to the Maestro."""
        while running:
            try:
                status = section.get_full_status()
                # Non-blocking put — if queue is somehow full, skip this update
                try:
                    status_queue.put_nowait(status)
                except Exception:
                    pass
            except Exception as e:
                logger.warning(
                    "status_broadcast_error", section=section_id, error=str(e)
                )
            await asyncio.sleep(2.0)

    # Run both loops until stop is received
    try:
        await asyncio.gather(command_loop(), status_loop())
    except asyncio.CancelledError:
        pass
    finally:
        await section.stop()
        logger.info("section_process_stopped", section=section_id)
