"""
Miner model and Vnish API — the only interface to miner hardware.

A Miner is an IP + state.  VnishAPI sends sleep / wake commands.
Everything else (hashrate, power, temperature) comes from polling.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import Optional

import httpx
import structlog

logger = structlog.get_logger()

# ---------------------------------------------------------------------------
# Miner model
# ---------------------------------------------------------------------------

class MinerState(str, Enum):
    MINING = "mining"
    IDLE = "idle"          # sleeping / stopped
    WAKING = "waking"      # wake sent, waiting for hashrate
    OFFLINE = "offline"    # unreachable


@dataclass
class Miner:
    ip: str
    state: MinerState = MinerState.OFFLINE
    hashrate_ghs: float = 0.0
    power_watts: float = 0.0
    temperature_c: float = 0.0
    fan_speed_pct: float = 0.0
    uptime_seconds: int = 0
    model: str = ""
    mac_address: str = ""
    firmware_version: str = ""
    last_seen: Optional[datetime] = None
    # Wake tracking
    wake_sent_at: Optional[datetime] = None
    _poke_count: int = 0

    @property
    def miner_id(self) -> str:
        return self.ip.replace(".", "_")

    @property
    def is_mining(self) -> bool:
        return self.state == MinerState.MINING

    @property
    def is_idle(self) -> bool:
        return self.state == MinerState.IDLE

    @property
    def is_waking(self) -> bool:
        return self.state == MinerState.WAKING

    @property
    def waking_for_seconds(self) -> float:
        if self.wake_sent_at is None:
            return 0.0
        return (datetime.utcnow() - self.wake_sent_at).total_seconds()

    def mark_waking(self):
        self.state = MinerState.WAKING
        self.wake_sent_at = datetime.utcnow()
        self._poke_count = 0

    def mark_mining(self, ghs: float, power_w: float, temp: float = 0.0,
                    fan: float = 0.0, uptime: int = 0):
        self.state = MinerState.MINING
        self.hashrate_ghs = ghs
        self.power_watts = power_w
        self.temperature_c = temp
        self.fan_speed_pct = fan
        self.uptime_seconds = uptime
        self.last_seen = datetime.utcnow()
        self.wake_sent_at = None
        self._poke_count = 0

    def mark_idle(self):
        self.state = MinerState.IDLE
        self.hashrate_ghs = 0.0
        self.power_watts = 0.0
        self.wake_sent_at = None
        self._poke_count = 0
        self.last_seen = datetime.utcnow()

    def mark_offline(self):
        self.state = MinerState.OFFLINE
        self.hashrate_ghs = 0.0
        self.power_watts = 0.0


# ---------------------------------------------------------------------------
# Vnish API — thin wrapper around the Vnish HTTP endpoints
# ---------------------------------------------------------------------------

WATTS_PER_MINER = 1400  # Antminer S9 average


class VnishAPI:
    """Stateless helper that talks to a miner's Vnish web interface."""

    def __init__(self, username: str = "root", password: str = "root",
                 timeout: float = 5.0):
        self._auth = httpx.DigestAuth(username, password)
        self._timeout = timeout

    # -- sleep (idle) -------------------------------------------------------

    async def sleep(self, ip: str, client: httpx.AsyncClient) -> bool:
        """Put miner to sleep (mode=1)."""
        url = f"http://{ip}/cgi-bin/do_sleep_mode.cgi"
        try:
            r = await client.post(url, auth=self._auth, data={"mode": "1"},
                                  timeout=self._timeout)
            ok = r.status_code == 200 and "ok" in r.text.lower()
            if ok:
                logger.debug("sleep OK", ip=ip)
            else:
                logger.warning("sleep unexpected response", ip=ip,
                               status=r.status_code, body=r.text[:80])
            return ok
        except Exception as e:
            logger.warning("sleep failed", ip=ip, err=str(e))
            return False

    # -- wake ---------------------------------------------------------------

    async def wake(self, ip: str, client: httpx.AsyncClient) -> bool:
        """Send wake command (mode=0)."""
        url = f"http://{ip}/cgi-bin/do_sleep_mode.cgi"
        try:
            r = await client.post(url, auth=self._auth, data={"mode": "0"},
                                  timeout=self._timeout)
            ok = r.status_code == 200 and "ok" in r.text.lower()
            if ok:
                logger.debug("wake OK", ip=ip)
            return ok
        except Exception as e:
            logger.warning("wake failed", ip=ip, err=str(e))
            return False

    # -- poke (stimulate firmware after wake) --------------------------------

    async def poke(self, ip: str, client: httpx.AsyncClient) -> bool:
        """HTTP GET to stimulate CGMiner restart after wake."""
        url = f"http://{ip}/cgi-bin/get_miner_status.cgi"
        try:
            r = await client.get(url, auth=self._auth, timeout=self._timeout)
            return r.status_code == 200
        except Exception:
            return False

    # -- poll status --------------------------------------------------------

    async def poll(self, ip: str, client: httpx.AsyncClient) -> Optional[dict]:
        """Read miner status via Vnish web API.

        Returns dict with keys: ghsav, elapsed, temp, fan, power_w
        or None on failure.
        """
        url = f"http://{ip}/cgi-bin/get_miner_status.cgi"
        try:
            r = await client.get(url, auth=self._auth, timeout=self._timeout)
            if r.status_code != 200:
                return None
            d = r.json()
            # Extract from Vnish response structure
            ghs = float(d.get("ghsav") or d.get("GHSav") or 0)
            elapsed = int(d.get("elapsed") or d.get("Elapsed") or 0)
            # Temperature: may be nested or top-level
            temp = 0.0
            fan = 0.0
            if "devs" in d and isinstance(d["devs"], list):
                temps = []
                fans = []
                for dev in d["devs"]:
                    t = dev.get("temp") or dev.get("temp2") or 0
                    if t and str(t).strip():
                        try:
                            temps.append(float(t))
                        except (ValueError, TypeError):
                            pass
                    for fk in ("fan1", "fan2", "fan3", "fan4"):
                        fv = dev.get(fk)
                        if fv and str(fv).strip():
                            try:
                                fans.append(float(fv))
                            except (ValueError, TypeError):
                                pass
                if temps:
                    temp = max(temps)
                if fans:
                    max_rpm = max(fans)
                    # S9 fan max ~6000 RPM
                    fan = min(100.0, (max_rpm / 6000) * 100)

            # Estimate power from hashrate (S9: ~1400W at 13.5 TH/s)
            if ghs > 100:
                power_w = (ghs / 13500) * WATTS_PER_MINER
            else:
                power_w = 0.0

            return {
                "ghsav": ghs,
                "elapsed": elapsed,
                "temp": temp,
                "fan": fan,
                "power_w": power_w,
            }
        except Exception:
            return None

    # -- discovery (system info) --------------------------------------------

    async def get_system_info(self, ip: str, client: httpx.AsyncClient) -> Optional[dict]:
        """Read /cgi-bin/get_system_info.cgi for model/firmware/mac."""
        url = f"http://{ip}/cgi-bin/get_system_info.cgi"
        try:
            r = await client.get(url, auth=self._auth, timeout=self._timeout)
            if r.status_code != 200:
                return None
            return r.json()
        except Exception:
            return None
