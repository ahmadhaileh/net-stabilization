"""
Miner Control — single clean module for sleep/wake and status polling.

Based on Awesome Miner reverse engineering:
- Sleep: HTTP POST to /cgi-bin/do_sleep_mode.cgi with mode=1 (digest auth)
- Wake:  HTTP POST to /cgi-bin/do_sleep_mode.cgi with mode=0 (digest auth)
- Fire-and-forget: no post-wake polling, normal status cycle detects state.
- Web server stays up during sleep (only ASIC chips stop).

Vnish 3.9.0 S9 specifics:
- Digest auth: root/root
- Status: GET /cgi-bin/get_miner_status.cgi
- Config: GET /cgi-bin/get_miner_conf.cgi
- CGMiner TCP API on port 4028 for hashrate/power stats
"""
import asyncio
import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import Optional, Dict, Any, List, Tuple

import httpx
import structlog

from app.config import get_settings

logger = structlog.get_logger()

# ── Antminer S9 limits ───────────────────────────────────────────
MAX_POWER_WATTS = 1500.0
MAX_HASHRATE_GHS = 15000.0


class MinerState(str, Enum):
    """Observable miner states."""
    UNKNOWN = "unknown"
    MINING = "mining"        # hashing, consuming power
    SLEEPING = "sleeping"    # sleep mode, web server up, chips off
    OFFLINE = "offline"      # unreachable


@dataclass
class Miner:
    """Representation of a single physical miner."""
    ip: str
    state: MinerState = MinerState.UNKNOWN

    # Live stats (updated by poll)
    hashrate_ghs: float = 0.0
    power_watts: float = 0.0
    temperature_c: float = 0.0
    fan_speed_pct: float = 0.0
    uptime_seconds: int = 0
    pool_url: str = ""

    # Identity (filled on first successful poll)
    model: str = ""
    firmware: str = ""
    mac_address: str = ""

    # Tracking
    last_seen: Optional[datetime] = None
    last_command_time: Optional[datetime] = None
    last_command: Optional[str] = None
    consecutive_failures: int = 0

    # Wake tracking
    consecutive_wake_failures: int = 0
    _wake_backoff_until: Optional[datetime] = None

    @property
    def power_kw(self) -> float:
        return self.power_watts / 1000.0

    @property
    def is_transitioning(self) -> bool:
        """True if a command was sent recently (grace period)."""
        if not self.last_command_time:
            return False
        elapsed = (datetime.utcnow() - self.last_command_time).total_seconds()
        # Wake takes ~60s boot, sleep is near-instant
        grace = 120 if self.last_command == "wake" else 30
        return elapsed < grace

    @property
    def is_wake_backed_off(self) -> bool:
        if self._wake_backoff_until is None:
            return False
        return datetime.utcnow() < self._wake_backoff_until

    def record_wake_failure(self):
        self.consecutive_wake_failures += 1
        backoff = min(30 * (2 ** (self.consecutive_wake_failures - 1)), 300)
        self._wake_backoff_until = datetime.utcnow() + timedelta(seconds=backoff)

    def clear_wake_failures(self):
        self.consecutive_wake_failures = 0
        self._wake_backoff_until = None


# ── Vnish HTTP API ────────────────────────────────────────────────

async def _vnish_request(
    ip: str,
    endpoint: str,
    method: str = "GET",
    data: Optional[dict] = None,
    timeout: float = 5.0,
) -> Tuple[bool, Any]:
    """
    Make an HTTP request to a Vnish miner with digest auth.

    Returns (success, response_data_or_None).
    """
    settings = get_settings()
    url = f"http://{ip}:{settings.vnish_port}{endpoint}"
    auth = httpx.DigestAuth(settings.vnish_username, settings.vnish_password)

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            if method == "GET":
                resp = await client.get(url, auth=auth)
            else:
                resp = await client.post(
                    url,
                    data=data,
                    auth=auth,
                    headers={"Content-Type": "application/x-www-form-urlencoded"},
                )
            if resp.status_code == 200:
                try:
                    return True, resp.json()
                except Exception:
                    return True, resp.text
            return False, None
    except Exception as e:
        logger.debug("vnish_request_failed", ip=ip, endpoint=endpoint, error=str(e))
        return False, None


async def sleep_miner(ip: str) -> bool:
    """
    Put a miner to sleep.

    Sends POST /cgi-bin/do_sleep_mode.cgi mode=1.
    Fire-and-forget — returns True if the HTTP request succeeded.
    The miner stops hashing but keeps its web server running.
    """
    ok, _ = await _vnish_request(
        ip,
        "/cgi-bin/do_sleep_mode.cgi",
        method="POST",
        data={"mode": "1"},
    )
    if ok:
        logger.info("miner_sleep_sent", ip=ip)
    else:
        logger.warning("miner_sleep_failed", ip=ip)
    return ok


async def wake_miner(ip: str) -> bool:
    """
    Wake a miner from sleep.

    Sends POST /cgi-bin/do_sleep_mode.cgi mode=0.
    Fire-and-forget — miner boots CGMiner and starts hashing in ~60s.
    Normal polling will detect it coming online.
    """
    ok, _ = await _vnish_request(
        ip,
        "/cgi-bin/do_sleep_mode.cgi",
        method="POST",
        data={"mode": "0"},
    )
    if ok:
        logger.info("miner_wake_sent", ip=ip)
    else:
        logger.warning("miner_wake_failed", ip=ip)
    return ok


# ── CGMiner TCP API (port 4028) ──────────────────────────────────

async def cgminer_command(ip: str, command: str, parameter: str = "", timeout: float = 5.0) -> Optional[dict]:
    """Send a CGMiner TCP API command and return parsed JSON response."""
    settings = get_settings()
    cmd = {"command": command}
    if parameter:
        cmd["parameter"] = parameter

    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(ip, settings.miner_api_port),
            timeout=timeout,
        )
        try:
            writer.write(json.dumps(cmd).encode())
            await writer.drain()

            data = b""
            while True:
                try:
                    chunk = await asyncio.wait_for(reader.read(4096), timeout=timeout)
                    if not chunk:
                        break
                    data += chunk
                    # Try parse — CGMiner closes after full response
                    cleaned = data.replace(b"\x00", b"").decode()
                    cleaned = cleaned.replace("}{", "},{")
                    return json.loads(cleaned)
                except (json.JSONDecodeError, UnicodeDecodeError):
                    continue
                except asyncio.TimeoutError:
                    break

            if data:
                cleaned = data.replace(b"\x00", b"").decode()
                cleaned = cleaned.replace("}{", "},{")
                return json.loads(cleaned)
        finally:
            writer.close()
            await writer.wait_closed()
    except Exception:
        return None


# ── Status Polling ────────────────────────────────────────────────

async def poll_miner(miner: Miner) -> MinerState:
    """
    Poll a single miner and update its state in-place.

    Strategy:
    1. Try CGMiner TCP summary (fast, reliable when mining)
    2. If that fails, try Vnish HTTP status (web server stays up in sleep)
    3. If both fail, mark offline

    Returns the new state.
    """
    # Try CGMiner first — fast path for mining miners
    summary = await cgminer_command(miner.ip, "summary", timeout=3.0)
    if summary and "SUMMARY" in summary:
        _parse_cgminer_summary(miner, summary)
        if miner.hashrate_ghs > 0:
            miner.state = MinerState.MINING
            miner.consecutive_failures = 0
            miner.last_seen = datetime.utcnow()
            return MinerState.MINING

    # Try Vnish HTTP — sleeping miners have web server up
    ok, status_data = await _vnish_request(
        miner.ip, "/cgi-bin/get_miner_status.cgi", timeout=3.0
    )
    if ok and status_data:
        _parse_vnish_status(miner, status_data)
        miner.consecutive_failures = 0
        miner.last_seen = datetime.utcnow()
        # If hashrate > 0, it's mining; otherwise it's sleeping/idle
        if miner.hashrate_ghs > 0:
            miner.state = MinerState.MINING
        else:
            miner.state = MinerState.SLEEPING
        return miner.state

    # CGMiner responded but with 0 hashrate and vnish down — could be booting
    if summary and "SUMMARY" in summary:
        miner.state = MinerState.SLEEPING  # CGMiner up but not hashing
        miner.consecutive_failures = 0
        miner.last_seen = datetime.utcnow()
        return MinerState.SLEEPING

    # Both failed
    miner.consecutive_failures += 1
    if miner.consecutive_failures >= 3:
        miner.state = MinerState.OFFLINE
    return miner.state


def _parse_cgminer_summary(miner: Miner, data: dict):
    """Extract stats from a CGMiner summary response."""
    try:
        s = data["SUMMARY"][0]
        raw_ghs = float(s.get("GHS 5s", s.get("GHS av", 0)))
        miner.hashrate_ghs = min(raw_ghs, MAX_HASHRATE_GHS)
        miner.uptime_seconds = int(s.get("Elapsed", 0))
    except (KeyError, IndexError, ValueError):
        pass


def _parse_vnish_status(miner: Miner, data: Any):
    """Extract stats from Vnish get_miner_status.cgi response."""
    if not isinstance(data, dict):
        return
    try:
        # Power
        power = float(data.get("Power", 0))
        miner.power_watts = min(power, MAX_POWER_WATTS)

        # Hashrate — Vnish reports in GH/s
        raw_ghs = float(data.get("GHSav", data.get("GHS5s", 0)))
        miner.hashrate_ghs = min(raw_ghs, MAX_HASHRATE_GHS)

        # Temperature — max across boards
        temps = []
        for chain in data.get("chain", []):
            t = chain.get("temp_chip", chain.get("temp_pcb", 0))
            if isinstance(t, (int, float)) and t > 0:
                temps.append(float(t))
        if temps:
            miner.temperature_c = max(temps)

        # Fans
        fans = data.get("fan", [])
        if fans and isinstance(fans, list):
            fan_speeds = [f for f in fans if isinstance(f, (int, float)) and f > 0]
            if fan_speeds:
                miner.fan_speed_pct = sum(fan_speeds) / len(fan_speeds)

        # Identity
        if not miner.firmware:
            miner.firmware = str(data.get("CompileTime", ""))
        if not miner.model:
            miner.model = str(data.get("Type", ""))
    except (ValueError, TypeError):
        pass


# ── Discovery ─────────────────────────────────────────────────────

async def discover_miners(network_cidr: str, timeout: float = 1.0) -> List[str]:
    """
    Scan a network for miners by probing CGMiner port 4028.

    Returns list of IPs that responded.
    """
    import ipaddress

    settings = get_settings()
    network = ipaddress.ip_network(network_cidr, strict=False)
    found: List[str] = []

    async def _probe(ip_str: str):
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(ip_str, settings.miner_api_port),
                timeout=timeout,
            )
            writer.close()
            await writer.wait_closed()
            found.append(ip_str)
        except Exception:
            pass

    # Probe all IPs concurrently
    tasks = [_probe(str(ip)) for ip in network.hosts()]
    await asyncio.gather(*tasks)

    logger.info("discovery_complete", network=network_cidr, found=len(found))
    return sorted(found)


async def identify_miner(miner: Miner) -> bool:
    """
    Fill in identity fields (model, firmware, mac) from Vnish system info.
    Returns True if successful.
    """
    ok, info = await _vnish_request(miner.ip, "/cgi-bin/get_system_info.cgi", timeout=3.0)
    if ok and isinstance(info, dict):
        miner.model = info.get("minertype", "")
        miner.firmware = info.get("file_system_version", "")
        miner.mac_address = info.get("macaddr", "")
        return True
    return False
