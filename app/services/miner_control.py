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

# ── Sanity limits ─────────────────────────────────────────────────
MAX_POWER_WATTS = 3500.0
MAX_HASHRATE_GHS = 120000.0

# Fallback when a miner's model can't be identified
DEFAULT_MINER_POWER_KW = 3.25

# AnthillOS (Vnish 1.2.7+) token cache: {ip: token_str}
_anthillos_tokens: Dict[str, str] = {}
# IPs confirmed as AnthillOS (detected during polling/identification)
_anthillos_ips: set = set()


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
            # Non-200 but server responded — return status code so callers
            # can distinguish "auth failed" from "connection refused".
            return False, resp.status_code
    except Exception as e:
        logger.debug("vnish_request_failed", ip=ip, endpoint=endpoint, error=str(e))
        return False, None


# ── AnthillOS REST API (S19+ with Vnish 1.2.7+) ─────────────────

async def _anthillos_get_token(ip: str, timeout: float = 5.0) -> Optional[str]:
    """Acquire a Bearer token from AnthillOS via POST /api/v1/unlock."""
    cached = _anthillos_tokens.get(ip)
    if cached:
        return cached

    settings = get_settings()
    url = f"http://{ip}:{settings.vnish_port}/api/v1/unlock"
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(url, json={"pw": "admin"})
            if resp.status_code == 200:
                data = resp.json()
                token = data.get("token")
                if token:
                    _anthillos_tokens[ip] = token
                    return token
    except Exception as e:
        logger.debug("anthillos_unlock_failed", ip=ip, error=str(e))
    return None


async def _anthillos_request(
    ip: str,
    endpoint: str,
    method: str = "GET",
    timeout: float = 5.0,
) -> Tuple[bool, Any]:
    """Make an authenticated request to an AnthillOS REST API endpoint."""
    token = await _anthillos_get_token(ip, timeout=timeout)
    if not token:
        return False, None

    settings = get_settings()
    url = f"http://{ip}:{settings.vnish_port}{endpoint}"
    headers = {"Authorization": f"Bearer {token}"}
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            if method == "GET":
                resp = await client.get(url, headers=headers)
            else:
                resp = await client.post(url, headers=headers)
            if resp.status_code == 200:
                try:
                    return True, resp.json()
                except Exception:
                    return True, resp.text
            # Token expired — clear cache and retry once
            if resp.status_code in (401, 403):
                _anthillos_tokens.pop(ip, None)
                token = await _anthillos_get_token(ip, timeout=timeout)
                if not token:
                    return False, None
                headers = {"Authorization": f"Bearer {token}"}
                if method == "GET":
                    resp = await client.get(url, headers=headers)
                else:
                    resp = await client.post(url, headers=headers)
                if resp.status_code == 200:
                    try:
                        return True, resp.json()
                    except Exception:
                        return True, resp.text
            return False, None
    except Exception as e:
        logger.debug("anthillos_request_failed", ip=ip, endpoint=endpoint, error=str(e))
        return False, None


async def _anthillos_poll(ip: str, timeout: float = 3.0) -> Optional[dict]:
    """Poll AnthillOS status + summary (no auth needed for reads)."""
    settings = get_settings()
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.get(f"http://{ip}:{settings.vnish_port}/api/v1/status")
            if resp.status_code != 200:
                return None
            status = resp.json()

            resp2 = await client.get(f"http://{ip}:{settings.vnish_port}/api/v1/summary")
            summary = resp2.json() if resp2.status_code == 200 else {}

            return {"status": status, "summary": summary}
    except Exception:
        return None


async def sleep_miner(ip: str) -> bool:
    """
    Put a miner to sleep by stopping mining.

    Strategy:
    1. Try AnthillOS REST API (S19+): POST /api/v1/mining/stop
    2. Try CGI stop_bmminer.cgi (stock Bitmain + old Vnish)
    3. Try CGI do_sleep_mode.cgi (Vnish fallback)
    """
    # AnthillOS REST API (S19+ with Vnish 1.2.7+) — only for known AnthillOS miners
    if ip in _anthillos_ips:
        ok, _ = await _anthillos_request(ip, "/api/v1/mining/stop", method="POST", timeout=5.0)
        if ok:
            logger.info("miner_sleep_sent", ip=ip, method="anthillos_stop")
            return True

    # CGI: stop_bmminer.cgi — works on stock Bitmain + old Vnish
    ok, resp = await _vnish_request(ip, "/cgi-bin/stop_bmminer.cgi", method="GET")
    if ok and isinstance(resp, dict):
        logger.info("miner_sleep_sent", ip=ip, method="stop_bmminer")
        return True

    # CGI fallback: do_sleep_mode.cgi (Vnish firmware)
    ok, _ = await _vnish_request(
        ip,
        "/cgi-bin/do_sleep_mode.cgi",
        method="POST",
        data={"mode": "1"},
    )
    if ok:
        logger.info("miner_sleep_sent", ip=ip, method="do_sleep_mode")
    else:
        logger.warning("miner_sleep_failed", ip=ip)
    return ok


async def wake_miner(ip: str) -> bool:
    """
    Wake a miner from sleep.

    Strategy:
    1. Try AnthillOS REST API (S19+): POST /api/v1/mining/start
    2. Try CGI reboot_cgminer.cgi (old Vnish — fast, ~30s)
    3. Try CGI reboot.cgi (stock Bitmain — full reboot, ~90s)
    """
    # AnthillOS REST API (S19+ with Vnish 1.2.7+) — only for known AnthillOS miners
    if ip in _anthillos_ips:
        ok, _ = await _anthillos_request(ip, "/api/v1/mining/start", method="POST", timeout=8.0)
        if ok:
            logger.info("miner_wake_sent", ip=ip, method="anthillos_start")
            return True

    # CGI: Vnish cgminer-only restart (faster, ~30s)
    ok, _ = await _vnish_request(
        ip, "/cgi-bin/reboot_cgminer.cgi", method="GET", timeout=8.0
    )
    if ok:
        logger.info("miner_wake_sent", ip=ip, method="reboot_cgminer")
        return True

    # CGI: Full system reboot (stock Bitmain firmware)
    ok, _ = await _vnish_request(
        ip,
        "/cgi-bin/reboot.cgi",
        method="POST",
        data={"reboot": "1"},
        timeout=10.0,
    )
    if ok:
        logger.info("miner_wake_sent", ip=ip, method="reboot")
        return True

    logger.warning("miner_wake_failed", ip=ip)
    return False


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
    1. Try Vnish HTTP status (web server stays up in all states)
    2. If that fails, try CGMiner TCP summary (fast when mining)
    3. If both fail, mark offline

    Returns the new state.
    """
    # Try Vnish HTTP first — web server stays up in sleep and mining
    ok, status_data = await _vnish_request(
        miner.ip, "/cgi-bin/get_miner_status.cgi", timeout=3.0
    )
    # Any HTTP response (including 401) means web server is alive.
    # status_data is None only when connection failed entirely.
    vnish_reachable = ok or status_data is not None
    if ok and status_data:
        if isinstance(status_data, dict):
            _parse_vnish_status(miner, status_data)
            miner.consecutive_failures = 0
            miner.last_seen = datetime.utcnow()
            if miner.hashrate_ghs > 0:
                miner.state = MinerState.MINING
            else:
                miner.state = MinerState.SLEEPING
            return miner.state
        # Non-dict response — check if HTML (AnthillOS SPA) vs broken JSON (S9)
        if isinstance(status_data, str) and "<html" in status_data.lower():
            _anthillos_ips.add(miner.ip)
            anthill = await _anthillos_poll(miner.ip, timeout=3.0)
            if anthill:
                _parse_anthillos_status(miner, anthill)
                miner.consecutive_failures = 0
                miner.last_seen = datetime.utcnow()
                return miner.state

    # Try CGMiner TCP — reliable even when Vnish JSON is broken
    summary = await cgminer_command(miner.ip, "summary", timeout=3.0)
    if summary and "SUMMARY" in summary:
        _parse_cgminer_summary(miner, summary)
        miner.consecutive_failures = 0
        miner.last_seen = datetime.utcnow()
        if miner.hashrate_ghs > 0:
            miner.state = MinerState.MINING
        else:
            miner.state = MinerState.SLEEPING
        return miner.state

    # Both failed — but if Vnish web server responded, miner is alive (sleeping)
    if vnish_reachable:
        miner.consecutive_failures = 0
        miner.last_seen = datetime.utcnow()
        miner.hashrate_ghs = 0
        miner.power_watts = 0
        miner.state = MinerState.SLEEPING
        return miner.state

    # Truly unreachable
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
    """Extract stats from Vnish get_miner_status.cgi response.

    Response format:
    {
      "summary": {"ghsav": 4428.24, "ghs5s": "4633.464", ...},
      "pools": [...],
      "devs": [
        {"index":"6", "temp":"71", "temp2":"82", "rate":"4627.47",
         "fan5":"5520", "fan6":"4200", "chain_consumption":"502", ...},
        ...
      ]
    }
    """
    if not isinstance(data, dict):
        return
    try:
        summary = data.get("summary", {})
        if not isinstance(summary, dict):
            summary = {}

        # Hashrate — nested in summary, lowercase keys
        raw_ghs = float(summary.get("ghsav", summary.get("ghs5s", 0)) or 0)
        miner.hashrate_ghs = min(raw_ghs, MAX_HASHRATE_GHS)

        # Power — sum chain_consumption across devs (watts per chain)
        devs = data.get("devs", [])
        if isinstance(devs, list):
            total_watts = 0
            for dev in devs:
                if not isinstance(dev, dict):
                    continue
                try:
                    consumption = dev.get("chain_consumption", 0)
                    if consumption and str(consumption) not in ("null", ""):
                        total_watts += float(consumption)
                except (ValueError, TypeError):
                    pass
            if total_watts > 0:
                miner.power_watts = min(total_watts, MAX_POWER_WATTS)

            # Temperature — max of temp2 (chip temp) across devs
            temps = []
            for dev in devs:
                if not isinstance(dev, dict):
                    continue
                for key in ("temp2", "temp"):
                    try:
                        t = float(dev.get(key, 0) or 0)
                        if t > 0:
                            temps.append(t)
                            break
                    except (ValueError, TypeError):
                        pass
            if temps:
                miner.temperature_c = max(temps)

            # Fans — fanN keys in first dev that has them
            fan_speeds = []
            for dev in devs:
                if not isinstance(dev, dict):
                    continue
                for key in ("fan1", "fan2", "fan3", "fan4", "fan5", "fan6", "fan7", "fan8"):
                    try:
                        speed = float(dev.get(key, 0) or 0)
                        if speed > 0:
                            fan_speeds.append(speed)
                    except (ValueError, TypeError):
                        pass
                if fan_speeds:
                    break  # Only first dev with fans
            if fan_speeds:
                miner.fan_speed_pct = sum(fan_speeds) / len(fan_speeds)

    except (ValueError, TypeError):
        pass


def _parse_anthillos_status(miner: Miner, data: dict):
    """Extract stats from AnthillOS /api/v1/status + /api/v1/summary."""
    status = data.get("status", {})
    summary = data.get("summary", {})

    # State from /api/v1/status
    miner_state = status.get("miner_state", "")
    if miner_state == "mining":
        miner.state = MinerState.MINING
    elif miner_state in ("stopped", "sleeping"):
        miner.state = MinerState.SLEEPING
    elif miner_state == "initializing":
        miner.state = MinerState.MINING  # Treat as mining (waking up)
    else:
        miner.state = MinerState.SLEEPING

    # Stats from /api/v1/summary → nested under "miner" key
    miner_data = summary.get("miner", summary)
    if isinstance(miner_data, dict):
        # Hashrate (GH/s)
        raw_ghs = float(miner_data.get("instant_hashrate", 0) or 0)
        miner.hashrate_ghs = min(raw_ghs, MAX_HASHRATE_GHS)

        # Power (watts)
        raw_watts = float(miner_data.get("power_consumption", 0) or 0)
        if raw_watts > 0:
            miner.power_watts = min(raw_watts, MAX_POWER_WATTS)

        # Temperature — chip_temp.max
        chip_temp = miner_data.get("chip_temp", {})
        if isinstance(chip_temp, dict):
            t = float(chip_temp.get("max", 0) or 0)
            if t > 0:
                miner.temperature_c = t


# ── Discovery ─────────────────────────────────────────────────────

async def discover_miners(network_cidr: str, timeout: float = 1.0) -> List[str]:
    """
    Scan a network for miners by probing Vnish web server (port 80).

    Port 80 stays up in all states: mining, sleeping, and crashed CGMiner.
    Port 4028 (CGMiner) is only up when actively mining.
    Returns list of IPs that responded.
    """
    import ipaddress

    settings = get_settings()
    network = ipaddress.ip_network(network_cidr, strict=False)
    found: List[str] = []

    # Exclude known non-miner devices (gateway, server, switches)
    exclude_set = set(
        ip.strip() for ip in settings.discovery_exclude_ips.split(",") if ip.strip()
    )

    async def _probe(ip_str: str):
        if ip_str in exclude_set:
            return
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(ip_str, settings.vnish_port),
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
    Tries CGI endpoint first (S9), then REST API (S19+).
    Returns True if successful.
    """
    # Try CGI endpoint (S9 Vnish / stock Bitmain)
    ok, info = await _vnish_request(miner.ip, "/cgi-bin/get_system_info.cgi", timeout=3.0)
    if ok and isinstance(info, dict):
        miner.model = info.get("minertype", "")
        miner.firmware = info.get("file_system_version", "")
        miner.mac_address = info.get("macaddr", "")
        return True

    # Try REST API (S19+ Vnish)
    ok, info = await _vnish_request(miner.ip, "/api/v1/info", timeout=3.0)
    if ok and isinstance(info, dict):
        miner.model = info.get("model", "") or info.get("minertype", "")
        miner.firmware = info.get("firmware", "") or info.get("fw_version", "")
        miner.mac_address = info.get("mac", "") or info.get("macaddr", "")
        _anthillos_ips.add(miner.ip)
        return True

    return False


def estimate_miner_power_kw(model: str) -> float:
    """
    Estimate rated power (kW) from a miner model string.

    This is the single source of truth for model→power mapping.
    Used by discovery, Maestro, and section managers to compute
    dynamic per-miner power instead of hardcoded fleet-wide constants.
    """
    m = model.lower()

    # S-series (SHA-256)
    if "s9" in m:
        return 1.4
    if "s17" in m:
        return 2.8 if "pro" in m else 2.4
    if "s19" in m:
        if "xp" in m:
            return 3.25
        if "pro" in m:
            return 3.25
        return 3.25   # S19 95TH rated 3250 W
    if "s21" in m:
        return 3.5

    # T-series
    if "t9" in m:
        return 1.45
    if "t17" in m:
        return 2.2
    if "t19" in m:
        return 3.15

    # Whatsminers
    if "whatsminer" in m:
        return 3.4

    # Default for unknown
    return DEFAULT_MINER_POWER_KW


async def discover_miners_with_power(
    network_cidr: str, timeout: float = 1.0,
) -> Dict[str, float]:
    """
    Discover miners and probe each for model to determine rated power.

    Returns dict of {ip: rated_power_kw}.
    This replaces the old discover_miners() for callers that need power info.
    """
    ips = await discover_miners(network_cidr, timeout)

    # Probe all miners concurrently for model identification
    async def _probe_power(ip: str) -> Tuple[str, float]:
        miner = Miner(ip=ip)
        if await identify_miner(miner):
            kw = estimate_miner_power_kw(miner.model)
            logger.info("miner_identified", ip=ip, model=miner.model, rated_kw=kw)
            return ip, kw
        logger.info("miner_unidentified", ip=ip, rated_kw=DEFAULT_MINER_POWER_KW)
        return ip, DEFAULT_MINER_POWER_KW

    results = await asyncio.gather(*[_probe_power(ip) for ip in ips])
    return dict(results)
