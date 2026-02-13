"""
Miner Discovery and Direct Control Service.

This module provides:
- Auto-discovery of ASIC miners on the local network
- Direct communication with miners via CGMiner/BTMiner API
- Vnish Web API support for firmware-specific control
- Idle mode support (stop cgminer, not full system reboot)
- No dependency on AwesomeMiner

Supported miners:
- Antminer (Bitmain) - CGMiner API + Vnish Web API
- Whatsminer (MicroBT) - BTMiner API  
- Avalon (Canaan) - CGMiner API
- Most other CGMiner-based ASICs

CGMiner TCP API (Port 4028):
----------------------------
Standard JSON-over-TCP protocol used by most ASIC miners:
  {"command":"summary"}    - Get mining summary stats
  {"command":"stats"}      - Get detailed statistics
  {"command":"pools"}      - Get pool configuration
  {"command":"devs"}       - Get device information
  {"command":"config"}     - Get CGMiner configuration
  {"command":"version"}    - Get firmware/software version

Vnish Firmware Support (Discovered from Awesome Miner reverse engineering):
---------------------------------------------------------------------------
Vnish 3.9.x (S9/T9/L3+) uses CGI-based API at port 80:

  Read-only (GET):
    /cgi-bin/get_miner_conf.cgi    - Config (pools, fan, frequencies)
    /cgi-bin/get_system_info.cgi   - System info (firmware, uptime, MAC)
    /cgi-bin/chip_hr.json          - Per-chip hashrate for all boards
    /cgi-bin/get_autofreq_log.cgi  - Auto-frequency tuning logs
    /cgi-bin/get_fs.cgi            - Filesystem/security check
  
  Control (POST):
    /cgi-bin/do_sleep_mode.cgi     - Sleep mode (mode=1 sleep, mode=0 wake)
    /cgi-bin/set_miner_conf.cgi    - Update pool config
    /cgi-bin/set_miner_conf_custom.cgi - Update advanced settings
    /cgi-bin/reboot.cgi            - Full system reboot

  V3 Config Parameters (embedded in config responses):
    _ant_voltage            - Global voltage
    _ant_asicboost          - AsicBoost enabled
    _ant_tempoff            - Temperature off threshold  
    _ant_target_temp        - Target temperature
    _ant_fan_customize_*    - Manual fan control
    _ant_autodownscale_*    - Auto-downscaling/preset switching
    
  Authentication: HTTP Digest Auth (default root:root)

Vnish 1.x+ (S17/T17/S19) uses REST API at /api/v1/:
  GET  /api/v1/summary    - Full miner status
  GET  /api/v1/info       - System info  
  GET  /api/v1/settings   - View config
  POST /api/v1/settings   - Save config (pools, cooling, overclock)
  POST /api/v1/mining/stop     - Stop mining
  POST /api/v1/mining/start    - Start mining
  POST /api/v1/mining/restart  - Restart mining
  POST /api/v1/system/reboot   - System reboot
  
  Authentication: Bearer token or API key header
  Docs: https://bitbucket.org/anthill-farm/miner-dash-api/
"""
import asyncio
import json
import re
import socket
import ipaddress
import httpx
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional, List, Dict, Any, Tuple

import structlog

from app.config import get_settings
logger = structlog.get_logger()


class MinerType(str, Enum):
    """Known miner types."""
    ANTMINER = "antminer"
    WHATSMINER = "whatsminer"
    AVALON = "avalon"
    CGMINER = "cgminer"  # Generic CGMiner-compatible
    UNKNOWN = "unknown"


class MinerPowerMode(str, Enum):
    """Power modes for miners."""
    NORMAL = "normal"
    LOW = "low"
    HIGH = "high"
    IDLE = "idle"


class FirmwareType(str, Enum):
    """Known firmware types."""
    STOCK = "stock"
    VNISH = "vnish"
    BRAIINS = "braiins"
    MARATHON = "marathon"
    UNKNOWN = "unknown"


@dataclass
class DiscoveredMiner:
    """Information about a discovered miner."""
    ip: str
    port: int = 4028
    miner_type: MinerType = MinerType.UNKNOWN
    model: str = ""
    hostname: str = ""
    mac_address: str = ""
    
    # Firmware info
    firmware_type: FirmwareType = FirmwareType.UNKNOWN
    firmware_version: str = ""
    
    # Runtime stats
    is_online: bool = False
    is_mining: bool = False
    hashrate_ghs: float = 0.0
    power_watts: float = 0.0
    temperature_c: float = 0.0
    fan_speed_pct: float = 0.0
    uptime_seconds: int = 0
    pool_url: str = ""
    power_mode: MinerPowerMode = MinerPowerMode.NORMAL
    
    # Config
    rated_power_watts: float = 3000.0  # Default, should be configured
    current_frequency: Optional[int] = None  # Current frequency in MHz for power scaling
    default_frequency: int = 550  # Default/full-power frequency for S9
    min_frequency: int = 300  # Minimum safe frequency
    max_frequency: int = 700  # Maximum safe frequency
    
    # Tracking
    last_seen: datetime = field(default_factory=datetime.utcnow)
    discovery_time: datetime = field(default_factory=datetime.utcnow)
    consecutive_failures: int = 0
    
    # State transition tracking - miners take 45-60s to wake from idle
    last_command_time: Optional[datetime] = None
    last_command_type: Optional[str] = None  # 'wake', 'sleep', 'restart', 'reboot', 'config'
    transition_grace_seconds: int = 60  # Grace period after commands
    
    @property
    def is_transitioning(self) -> bool:
        """Check if miner is in a state transition grace period."""
        if not self.last_command_time:
            return False
        elapsed = (datetime.utcnow() - self.last_command_time).total_seconds()
        return elapsed < self.transition_grace_seconds
    
    def mark_command_sent(self, command_type: str, grace_seconds: int = None):
        """Mark that a command was sent to this miner."""
        self.last_command_time = datetime.utcnow()
        self.last_command_type = command_type
        if grace_seconds:
            self.transition_grace_seconds = grace_seconds
    
    @property
    def id(self) -> str:
        """Unique identifier based on IP."""
        return self.ip.replace(".", "_")
    
    @property
    def power_kw(self) -> float:
        """Power in kilowatts."""
        return self.power_watts / 1000.0
    
    @property
    def rated_power_kw(self) -> float:
        """Rated power in kilowatts."""
        return self.rated_power_watts / 1000.0


class CGMinerAPI:
    """
    Async client for CGMiner/BTMiner API.
    
    The CGMiner API uses a simple JSON-over-TCP protocol on port 4028.
    Commands are sent as JSON objects, responses are JSON.
    
    Common commands:
    - {"command": "summary"} - Get miner summary
    - {"command": "stats"} - Get detailed stats
    - {"command": "pools"} - Get pool information
    - {"command": "devs"} - Get device info
    - {"command": "version"} - Get version info
    """
    
    def __init__(self, host: str, port: int = 4028, timeout: float = 5.0):
        self.host = host
        self.port = port
        self.timeout = timeout
    
    async def send_command(self, command: str, parameter: str = "") -> Dict[str, Any]:
        """
        Send a command to the miner and return the response.
        
        Args:
            command: The CGMiner API command
            parameter: Optional parameter for the command
            
        Returns:
            Parsed JSON response
        """
        try:
            # Build command
            cmd = {"command": command}
            if parameter:
                cmd["parameter"] = parameter
            
            # Connect and send
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(self.host, self.port),
                timeout=self.timeout
            )
            
            try:
                # Send command
                cmd_bytes = json.dumps(cmd).encode('utf-8')
                writer.write(cmd_bytes)
                await writer.drain()
                
                # Read response (CGMiner sends response then closes, or we read until timeout)
                response_data = b""
                while True:
                    try:
                        chunk = await asyncio.wait_for(
                            reader.read(4096),
                            timeout=self.timeout
                        )
                        if not chunk:
                            break
                        response_data += chunk
                        # Check if we have complete JSON
                        try:
                            # CGMiner sometimes adds null bytes
                            cleaned = response_data.replace(b'\x00', b'').decode('utf-8')
                            # Some firmware (vnish) sends invalid JSON with missing comma
                            # between STATS array elements: }{  needs to become },{
                            cleaned = cleaned.replace('}{', '},{')
                            return json.loads(cleaned)
                        except json.JSONDecodeError:
                            continue
                    except asyncio.TimeoutError:
                        break
                
                # Try to parse whatever we got
                cleaned = response_data.replace(b'\x00', b'').decode('utf-8')
                # Fix missing comma in malformed JSON from some firmware
                cleaned = cleaned.replace('}{', '},{')
                return json.loads(cleaned)
                
            finally:
                writer.close()
                await writer.wait_closed()
                
        except asyncio.TimeoutError:
            logger.debug("CGMiner API timeout", host=self.host, command=command)
            raise ConnectionError(f"Timeout connecting to {self.host}:{self.port}")
        except ConnectionRefusedError:
            logger.debug("CGMiner API connection refused", host=self.host)
            raise ConnectionError(f"Connection refused to {self.host}:{self.port}")
        except Exception as e:
            logger.debug("CGMiner API error", host=self.host, error=str(e))
            raise ConnectionError(f"Error communicating with {self.host}: {e}")
    
    async def get_summary(self) -> Dict[str, Any]:
        """Get miner summary."""
        return await self.send_command("summary")
    
    async def get_stats(self) -> Dict[str, Any]:
        """Get detailed miner stats."""
        return await self.send_command("stats")
    
    async def get_pools(self) -> Dict[str, Any]:
        """Get pool information."""
        return await self.send_command("pools")
    
    async def get_version(self) -> Dict[str, Any]:
        """Get miner version."""
        return await self.send_command("version")
    
    async def get_devs(self) -> Dict[str, Any]:
        """Get device information."""
        return await self.send_command("devs")


class VnishWebAPI:
    """
    Async client for Vnish firmware Web API (CGI-based for S9/T9/L3+).
    
    Vnish 3.9.x firmware on Antminer S9 provides these CGI endpoints:
    
    Read-only (GET):
    - /cgi-bin/get_miner_status.cgi - Mining stats (hashrate, temps, pools, chains)
    - /cgi-bin/get_miner_conf.cgi   - Current config (pools, fan PWM, frequencies)
    - /cgi-bin/get_system_info.cgi  - System info (firmware version, uptime, MAC)
    
    Control (GET triggers action):
    - /cgi-bin/stop_bmminer.cgi     - Stop cgminer (legacy idle mode)
    - /cgi-bin/reboot_cgminer.cgi   - Restart cgminer (legacy resume)
    - /cgi-bin/reboot.cgi           - Full system reboot
    
    Sleep Mode (POST - discovered from Awesome Miner):
    - /cgi-bin/do_sleep_mode.cgi    - Sleep control
      POST with mode=1 = Enter sleep/pause mode (instant stop)
      POST with mode=0 = Wake from sleep (restart mining)
    
    Config (POST with form data):
    - /cgi-bin/set_miner_conf.cgi        - Set pool config
    - /cgi-bin/set_miner_conf_custom.cgi - Set advanced config (fan, voltage, freq)
    
    Authentication: HTTP Digest Auth (default: root/root)
    
    Config keys for set_miner_conf_custom.cgi:
    - _ant_fan_rpm_off: "1" = immersion mode (disable fan alarm), "0" = normal
    - _ant_fan_customize_switch: "true" = manual PWM, "false" = auto
    - _ant_fan_customize_value: "0"-"100" = PWM duty cycle %
    - bitmain-fan-pwm: Same as fan_customize_value (older format)
    - bitmain-freq: Mining frequency (e.g., "550")
    - bitmain-voltage: Core voltage (e.g., "8.8")
    """
    
    def __init__(
        self,
        host: str,
        port: Optional[int] = None,
        username: Optional[str] = None,
        password: Optional[str] = None,
        timeout: float = 10.0
    ):
        settings = get_settings()
        self.host = host
        self.port = port if port is not None else settings.vnish_port
        self.username = username if username is not None else settings.vnish_username
        self.password = password if password is not None else settings.vnish_password
        self.timeout = timeout
        self.base_url = f"http://{host}:{self.port}"
    
    async def _request(self, endpoint: str, method: str = "GET", data: str = None) -> Dict[str, Any]:
        """
        Make a request to the Vnish Web API.
        
        Args:
            endpoint: API endpoint (e.g., "/cgi-bin/stop_bmminer.cgi")
            method: HTTP method ("GET" or "POST")
            data: POST data as string (e.g., "mode=1")
            
        Returns:
            Response as dict (or empty dict for success without JSON response)
        """
        url = f"{self.base_url}{endpoint}"
        
        try:
            # Use httpx with digest auth (Vnish uses digest)
            auth = httpx.DigestAuth(self.username, self.password)
            
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                if method == "POST":
                    headers = {"Content-Type": "application/json"}
                    response = await client.post(url, auth=auth, content=data, headers=headers)
                else:
                    response = await client.get(url, auth=auth)
                
                if response.status_code == 200:
                    try:
                        return response.json()
                    except:
                        # Some endpoints return non-JSON success (like "OK")
                        text = response.text.strip()
                        return {"success": True, "status": response.status_code, "response": text}
                elif response.status_code == 401:
                    # Try basic auth as fallback
                    basic_auth = httpx.BasicAuth(self.username, self.password)
                    if method == "POST":
                        response2 = await client.post(url, auth=basic_auth, content=data, headers=headers)
                    else:
                        response2 = await client.get(url, auth=basic_auth)
                    if response2.status_code == 200:
                        try:
                            return response2.json()
                        except:
                            text = response2.text.strip()
                            return {"success": True, "status": response2.status_code, "response": text}
                    raise ConnectionError(f"Auth failed: {response2.status_code}")
                else:
                    raise ConnectionError(f"HTTP {response.status_code}")
                        
        except httpx.TimeoutException:
            raise ConnectionError(f"Timeout connecting to {self.host}")
        except httpx.HTTPError as e:
            raise ConnectionError(f"HTTP error: {e}")
    
    async def set_sleep_mode(self, enable: bool) -> bool:
        """
        Set miner sleep mode using the do_sleep_mode.cgi endpoint.
        
        Per Vnish API docs:
        POST /cgi-bin/do_sleep_mode.cgi
        Content-Type: application/x-www-form-urlencoded
        mode=1 (sleep) or mode=0 (wake)
        
        Args:
            enable: True to enter sleep mode (pause), False to wake (resume)
            
        Returns:
            True if successful
        """
        url = f"{self.base_url}/cgi-bin/do_sleep_mode.cgi"
        mode = "1" if enable else "0"
        
        try:
            auth = httpx.DigestAuth(self.username, self.password)
            
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.post(
                    url,
                    auth=auth,
                    data={"mode": mode},
                    headers={"Content-Type": "application/x-www-form-urlencoded"}
                )
                
                if response.status_code == 200:
                    resp_text = response.text.strip().lower()
                    if resp_text == "ok" or "success" in resp_text or response.status_code == 200:
                        action = "enabled" if enable else "disabled"
                        logger.info(f"Vnish: Sleep mode {action}", host=self.host)
                        return True
                
                logger.warning("Vnish: Sleep mode command returned unexpected response", 
                             host=self.host, status=response.status_code, response=response.text[:100])
                return False
                
        except Exception as e:
            logger.error("Vnish: Failed to set sleep mode", host=self.host, error=str(e))
            return False
    
    async def stop_cgminer(self) -> bool:
        """
        Stop the cgminer process (put miner in idle/sleep mode).
        
        Uses stop_bmminer.cgi for reliable stop.
        Note: sleep mode API is unreliable on some Vnish versions.
        
        Returns:
            True if successful
        """
        # Use stop_bmminer.cgi directly - more reliable than sleep mode
        try:
            result = await self._request("/cgi-bin/stop_bmminer.cgi")
            if result.get("success") or result.get("response", "").lower() == "ok":
                logger.info("Vnish: CGMiner stopped", host=self.host)
                return True
            else:
                logger.warning("Vnish: stop_bmminer returned unexpected response", 
                              host=self.host, result=result)
                return False
        except Exception as e:
            logger.error("Vnish: Failed to stop cgminer", host=self.host, error=str(e))
            return False
    
    async def start_cgminer(self) -> bool:
        """
        Start/restart the cgminer process (resume mining from idle/sleep).
        
        Uses reboot_cgminer.cgi for reliable start.
        Note: This endpoint blocks until cgminer starts, so we use short timeout.
        
        Returns:
            True if the request was sent successfully
        """
        # Use reboot_cgminer.cgi directly - more reliable than sleep mode
        url = f"{self.base_url}/cgi-bin/reboot_cgminer.cgi"
        
        try:
            # Use a short timeout - we don't need to wait for cgminer to fully start
            auth = httpx.DigestAuth(self.username, self.password)
            
            async with httpx.AsyncClient(timeout=5.0) as client:
                try:
                    response = await client.get(url, auth=auth)
                    if response.status_code in [200]:
                        logger.info("Vnish: CGMiner start command sent", host=self.host)
                        return True
                except httpx.TimeoutException:
                    # Timeout is expected - the endpoint blocks until cgminer starts
                    # The request was sent, so consider it successful
                    logger.info("Vnish: CGMiner start command sent (response timed out, this is normal)", host=self.host)
                    return True
                    
            return False
            
        except Exception as e:
            logger.error("Vnish: Failed to start cgminer", host=self.host, error=str(e))
            return False
    
    async def get_status(self) -> Dict[str, Any]:
        """Get miner status from web API."""
        return await self._request("/cgi-bin/get_miner_status.cgi")
    
    async def get_system_info(self) -> Dict[str, Any]:
        """Get system info from web API."""
        return await self._request("/cgi-bin/get_system_info.cgi")
    
    async def get_config(self) -> Dict[str, Any]:
        """
        Get current miner configuration.
        
        Returns config including:
        - pools: List of 3 pool configs [{url, user, pass}, ...]
        - bitmain-fan-ctrl: Fan control enabled
        - bitmain-fan-pwm: Fan PWM value
        - bitmain-freq: Mining frequency
        - bitmain-voltage: Core voltage
        """
        return await self._request("/cgi-bin/get_miner_conf.cgi")
    
    async def set_pools(
        self, 
        pools: List[Dict[str, str]]
    ) -> Dict[str, Any]:
        """
        Update pool configuration.
        
        Args:
            pools: List of 3 pool configs, each with {url, user, pass}
                   Empty pools should have empty strings for all fields.
                   
        Example:
            await vnish.set_pools([
                {"url": "stratum+tcp://pool1:3333", "user": "wallet.w1", "pass": "x"},
                {"url": "stratum+tcp://pool2:3333", "user": "wallet.w2", "pass": "x"},
                {"url": "", "user": "", "pass": ""}
            ])
        """
        # Vnish expects exactly 3 pools
        while len(pools) < 3:
            pools.append({"url": "", "user": "", "pass": ""})
        pools = pools[:3]
        
        # Build form data for set_miner_conf.cgi
        data = {}
        for i, pool in enumerate(pools):
            idx = i + 1  # 1-indexed
            data[f"_ant_pool{idx}url"] = pool.get("url", "")
            data[f"_ant_pool{idx}user"] = pool.get("user", "")
            data[f"_ant_pool{idx}pw"] = pool.get("pass", "")
        
        return await self._post_config("/cgi-bin/set_miner_conf.cgi", data)
    
    async def set_fan_config(
        self,
        manual_pwm: Optional[int] = None,
        immersion_mode: Optional[bool] = None
    ) -> Dict[str, Any]:
        """
        Update fan configuration.
        
        Args:
            manual_pwm: If set, enable manual PWM control at this value (0-100).
                        If None, uses auto fan control.
            immersion_mode: If True, disable fan RPM alarm (for immersion cooling).
        """
        data = {}
        
        if manual_pwm is not None:
            data["_ant_fan_customize_switch"] = "true"
            data["_ant_fan_customize_value"] = str(manual_pwm)
        else:
            data["_ant_fan_customize_switch"] = "false"
            data["_ant_fan_customize_value"] = "100"
        
        if immersion_mode is not None:
            data["_ant_fan_rpm_off"] = "1" if immersion_mode else "0"
        
        return await self._post_config("/cgi-bin/set_miner_conf_custom.cgi", data)
    
    async def reboot_system(self) -> bool:
        """
        Perform full system reboot.
        
        Returns:
            True if reboot command was sent successfully
        """
        try:
            await self._request("/cgi-bin/reboot.cgi")
            logger.info("Vnish: System reboot initiated", host=self.host)
            return True
        except Exception as e:
            logger.error("Vnish: Failed to reboot", host=self.host, error=str(e))
            return False
    
    async def _post_config(self, endpoint: str, data: Dict[str, str]) -> Dict[str, Any]:
        """
        POST configuration to Vnish CGI endpoint.
        
        Args:
            endpoint: CGI endpoint path
            data: Form data to post
            
        Returns:
            Response dict
        """
        url = f"{self.base_url}{endpoint}"
        
        try:
            auth = httpx.DigestAuth(self.username, self.password)
            
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.post(url, auth=auth, data=data)
                
                if response.status_code == 200:
                    try:
                        return response.json()
                    except:
                        return {"success": True, "status": response.status_code}
                else:
                    raise ConnectionError(f"HTTP {response.status_code}")
                        
        except httpx.TimeoutException:
            raise ConnectionError(f"Timeout connecting to {self.host}")
        except httpx.HTTPError as e:
            raise ConnectionError(f"HTTP error: {e}")
    
    async def is_vnish_available(self) -> bool:
        """Check if Vnish web API is available."""
        try:
            await self.get_system_info()
            return True
        except:
            return False

    # =========================================================================
    # Vnish V3 API - Chip Hashrate & Tuning Logs
    # =========================================================================

    async def set_find_mode(self, enable: bool) -> Tuple[bool, str]:
        """
        Set the miner's find mode (LED blinking) on or off.
        
        Uses the Vnish find_mode.cgi endpoint which activates/deactivates
        the miner's LED blinking mode (same as "Find Miner" button in web UI).
        
        Args:
            enable: True to enable find mode, False to disable
            
        Returns:
            Tuple of (success, response_text)
        """
        try:
            url = f"{self.base_url}/cgi-bin/find_mode.cgi"
            mode_value = "1" if enable else "0"
            
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    url,
                    data=f"mode={mode_value}",
                    auth=httpx.DigestAuth(self.username, self.password),
                    timeout=self.timeout,
                    headers={"Content-Type": "application/x-www-form-urlencoded"}
                )
                result = response.text
                # Response is "Enabled" or "Disabled"
                success = response.status_code == 200 and result.strip() in ["Enabled", "Disabled"]
                if success:
                    logger.info("Vnish: Find mode set", host=self.host, enabled=enable, response=result.strip())
                else:
                    logger.warning("Vnish: Find mode failed", host=self.host, response=result)
                return success, result.strip()
            
        except Exception as e:
            logger.error("Vnish: Failed to set find mode", host=self.host, error=str(e))
            return False, str(e)

    async def blink_led(self, duration_seconds: int = 30) -> bool:
        """
        Blink the miner's LED to help locate it physically (timed).
        
        Uses the Vnish find_mode.cgi endpoint which activates
        the miner's LED blinking mode, then auto-disables after duration.
        
        Args:
            duration_seconds: How long to blink (default 30 seconds)
            
        Returns:
            True if the blink was started successfully
        """
        success, _ = await self.set_find_mode(enable=True)
        if not success:
            return False
            
        # Schedule automatic disable after duration
        async def disable_find_mode():
            await asyncio.sleep(duration_seconds)
            await self.set_find_mode(enable=False)
        
        asyncio.create_task(disable_find_mode())
        return True
    
    async def get_chip_hashrate(self) -> Dict[str, Any]:
        """
        Get per-chip hashrate data for all hashboards.
        
        Returns dict with 'chiphr' key containing list of hashboard data.
        Each hashboard has Asic00-AsicNN with hashrate values (MH/s per chip).
        
        Example response:
            {
                "chiphr": [
                    {"Asic00": "69", "Asic01": "67", ...},  # Board 1
                    {"Asic00": "64", "Asic01": "67", ...},  # Board 2
                    {"Asic00": "69", "Asic01": "70", ...}   # Board 3
                ]
            }
        """
        return await self._request("/cgi-bin/chip_hr.json")
    
    async def get_autofreq_log(self) -> str:
        """
        Get auto-frequency tuning logs.
        
        Returns log entries with timestamps showing tuning events,
        pool connections, and other status messages.
        
        Example:
            "[Mon Dec 29 12:30:16 2025] Online.."
        """
        result = await self._request("/cgi-bin/get_autofreq_log.cgi")
        return result.get("response", "") if isinstance(result, dict) else str(result)
    
    async def get_filesystem_status(self) -> Dict[str, Any]:
        """
        Get filesystem/security status check.
        
        Checks for unauthorized modifications like nightswitcher,
        antbuild, or sysfiles changes.
        
        Returns list of check results with name and status.
        """
        return await self._request("/cgi-bin/get_fs.cgi")

    # =========================================================================
    # Vnish V3 API - Profile/Preset Configuration
    # =========================================================================
    
    async def get_vnish_config(self) -> Dict[str, Any]:
        """
        Get full Vnish configuration including V3 parameters.
        
        The config contains embedded Vnish V3 settings in the bitmain-* fields:
        - Frequency/voltage per chain
        - Auto-scaling settings
        - Temperature thresholds
        - Profile switching settings
        
        Key V3 parameters (embedded in config values):
        - _ant_voltage: Global voltage
        - _ant_asicboost: AsicBoost enabled
        - _ant_tempoff: Temperature off threshold
        - _ant_target_temp: Target temperature
        - _ant_fan_customize_switch: Manual fan control
        - _ant_fan_customize_value: Fan PWM value
        - _ant_autodownscale_*: Auto-downscaling settings
        """
        return await self._request("/cgi-bin/get_miner_conf.cgi")
    
    async def set_vnish_profile(
        self,
        frequency: Optional[str] = None,
        voltage: Optional[str] = None,
        target_temp: Optional[int] = None,
        temp_off: Optional[int] = None,
        asicboost: Optional[bool] = None
    ) -> Dict[str, Any]:
        """
        Set Vnish mining profile/tuning parameters.
        
        Args:
            frequency: Mining frequency (e.g., "550" MHz)
            voltage: Core voltage (e.g., "8.8" V)
            target_temp: Target temperature for auto-fan (e.g., 75)
            temp_off: Temperature to shut off mining (e.g., 105)
            asicboost: Enable AsicBoost optimization
            
        Returns:
            Response dict
        """
        data = {}
        
        if frequency is not None:
            data["bitmain-freq"] = frequency
        
        if voltage is not None:
            data["_ant_voltage"] = voltage
        
        if target_temp is not None:
            data["_ant_target_temp"] = str(target_temp)
        
        if temp_off is not None:
            data["_ant_tempoff"] = str(temp_off)
        
        if asicboost is not None:
            data["_ant_asicboost"] = "true" if asicboost else "false"
        
        if not data:
            return {"success": True, "message": "No changes requested"}
        
        return await self._post_config("/cgi-bin/set_miner_conf_custom.cgi", data)
    
    async def set_auto_scaling(
        self,
        enabled: bool = True,
        min_preset: Optional[int] = None,
        max_preset: Optional[int] = None,
        temp_high_threshold: Optional[int] = None,
        temp_low_threshold: Optional[int] = None,
        downscale_timer: Optional[int] = None,
        downscale_after: Optional[int] = None,
        downscale_precision: Optional[int] = None
    ) -> Dict[str, Any]:
        """
        Configure Vnish auto-scaling/auto-downscale settings.
        
        These control how Vnish automatically adjusts mining intensity
        based on temperature and performance conditions.
        
        Args:
            enabled: Enable auto-scaling
            min_preset: Minimum preset level (don't go below)
            max_preset: Maximum preset level (don't raise above)
            temp_high_threshold: Lower preset if temp exceeds this
            temp_low_threshold: Raise preset if temp falls below this
            downscale_timer: Time between downscale checks (seconds)
            downscale_after: Consecutive checks before downscaling
            downscale_precision: Precision threshold for downscaling
            
        Returns:
            Response dict
        """
        data = {}
        
        if min_preset is not None:
            data["_ant_autodownscale_min"] = str(min_preset)
        
        if downscale_timer is not None:
            data["_ant_autodownscale_timer"] = str(downscale_timer)
        
        if downscale_after is not None:
            data["_ant_autodownscale_after"] = str(downscale_after)
        
        if downscale_precision is not None:
            data["_ant_autodownscale_prec"] = str(downscale_precision)
        
        # Auto-downscale profile: 0=disabled, 1=enabled
        data["_ant_autodownscale_profile"] = "1" if enabled else "0"
        
        if not data:
            return {"success": True, "message": "No changes requested"}
        
        return await self._post_config("/cgi-bin/set_miner_conf_custom.cgi", data)
    
    async def detect_firmware(self) -> str:
        """
        Detect the firmware type running on the miner.
        
        Returns:
            Firmware type string: "vnish", "braiins", "stock", or "unknown"
        """
        try:
            info = await self.get_system_info()
            minertype = info.get("minertype", "").lower()
            
            if "vnish" in minertype:
                return "vnish"
            elif "braiins" in minertype or "bos" in minertype:
                return "braiins"
            elif "antminer" in minertype:
                # Stock firmware just shows model
                return "stock"
            else:
                return "unknown"
        except Exception:
            return "unknown"
    
    async def get_vnish_version(self) -> Optional[str]:
        """
        Get the Vnish firmware version if running Vnish.
        
        Returns:
            Version string (e.g., "3.9.0") or None if not Vnish
        """
        try:
            info = await self.get_system_info()
            minertype = info.get("minertype", "")
            
            # Parse version from "Antminer S9 (vnish 3.9.0)"
            if "vnish" in minertype.lower():
                import re
                match = re.search(r'vnish\s*(\d+\.\d+\.?\d*)', minertype, re.IGNORECASE)
                if match:
                    return match.group(1)
            return None
        except Exception:
            return None


class MinerDiscoveryService:
    """
    Service for discovering and managing miners directly.
    
    Features:
    - Network scanning for miners
    - Direct CGMiner API communication
    - Idle mode support
    - Persistent miner registry
    """
    
    def __init__(
        self,
        network_cidr: str = "192.168.95.0/24",
        scan_ports: List[int] = None,
        scan_timeout: float = 1.0,
        api_timeout: float = 5.0
    ):
        self.network_cidr = network_cidr
        self.scan_ports = scan_ports or [4028]  # CGMiner default
        self.scan_timeout = scan_timeout
        self.api_timeout = api_timeout
        
        # Database service for persistence
        from app.database import get_db_service
        from app.config import get_settings
        self.db = get_db_service()
        settings = get_settings()
        
        # Registry of discovered miners
        self._miners: Dict[str, DiscoveredMiner] = {}
        
        # Lock for thread safety
        self._lock = asyncio.Lock()
        
        # Idle pool configuration (switch to this pool to "idle" miners)
        self._idle_pool_url: str = ""
        self._idle_pool_user: str = ""
        self._idle_pool_pass: str = ""
        
        # Active pool configuration (for resuming)
        self._active_pool_url: str = ""
        self._active_pool_user: str = ""
        self._active_pool_pass: str = ""
        
        # Snapshot timing - track last snapshot time per miner
        self._last_snapshot_time: Dict[str, datetime] = {}
        self._snapshot_interval = settings.snapshot_interval_seconds
    
    def _estimate_antminer_power(self, model: str) -> float:
        """Estimate rated power for Antminer models in watts."""
        model_lower = model.lower()
        
        # S-series (SHA-256)
        if "s9" in model_lower:
            return 1400.0
        elif "s17" in model_lower:
            return 2800.0 if "pro" in model_lower else 2400.0
        elif "s19" in model_lower:
            if "xp" in model_lower:
                return 3250.0
            elif "pro" in model_lower:
                return 3250.0
            else:
                return 3050.0
        elif "s21" in model_lower:
            return 3500.0
        
        # T-series
        elif "t9" in model_lower:
            return 1450.0
        elif "t17" in model_lower:
            return 2200.0
        elif "t19" in model_lower:
            return 3150.0
        
        # Default for unknown models
        return 3000.0
    
    def _save_miner_to_db(self, miner: DiscoveredMiner):
        """Save or update miner in database."""
        try:
            self.db.upsert_miner(
                ip=miner.ip,
                name=miner.hostname or f"Miner-{miner.ip.split('.')[-1]}",
                model=miner.model,
                firmware=miner.firmware_type.value if miner.firmware_type else None,
                firmware_version=miner.firmware_version,
                mac_address=miner.mac_address,
                rated_power_watts=int(miner.rated_power_watts) if miner.rated_power_watts else 1400,
                pool_url=miner.pool_url,
                pool_worker=None  # Not currently tracked
            )
        except Exception as e:
            logger.debug("Failed to save miner to database", ip=miner.ip, error=str(e))
    
    def _should_save_snapshot(self, miner_id: str) -> bool:
        """Check if enough time has passed to save a new snapshot."""
        now = datetime.utcnow()
        last_time = self._last_snapshot_time.get(miner_id)
        
        if last_time is None:
            return True
        
        elapsed = (now - last_time).total_seconds()
        return elapsed >= self._snapshot_interval
    
    def _save_miner_snapshot(self, miner: DiscoveredMiner):
        """Save miner snapshot for historical data (time-throttled)."""
        # Check if we should save (time-based throttling)
        if not self._should_save_snapshot(miner.id):
            return
        
        try:
            self.db.save_miner_snapshot(
                miner_ip=miner.ip,
                hashrate_ghs=miner.hashrate_ghs,
                power_watts=miner.power_watts,
                temperature=miner.temperature_c,
                fan_speed=miner.fan_speed_pct,
                frequency=miner.current_frequency,
                is_mining=miner.is_mining,
                uptime_seconds=miner.uptime_seconds
            )
            # Update last snapshot time on success
            self._last_snapshot_time[miner.id] = datetime.utcnow()
        except Exception as e:
            logger.debug("Failed to save miner snapshot", ip=miner.ip, error=str(e))
    
    @property
    def miners(self) -> List[DiscoveredMiner]:
        """Get list of all discovered miners."""
        return list(self._miners.values())
    
    def get_miner(self, miner_id: str) -> Optional[DiscoveredMiner]:
        """Get a specific miner by ID."""
        return self._miners.get(miner_id)
    
    # =========================================================================
    # Network Discovery
    # =========================================================================
    
    async def discover_miners(
        self,
        network_cidr: Optional[str] = None,
        concurrent_scans: int = 50
    ) -> List[DiscoveredMiner]:
        """
        Scan network for miners.
        
        Args:
            network_cidr: Network to scan (e.g., "192.168.1.0/24")
            concurrent_scans: Max concurrent connection attempts
            
        Returns:
            List of discovered miners
        """
        cidr = network_cidr or self.network_cidr
        logger.info("Starting miner discovery", network=cidr)
        
        try:
            network = ipaddress.ip_network(cidr, strict=False)
        except ValueError as e:
            logger.error("Invalid network CIDR", cidr=cidr, error=str(e))
            return []
        
        # Generate all IPs to scan
        hosts = list(network.hosts())
        logger.info(f"Scanning {len(hosts)} hosts for miners")
        
        discovered = []
        semaphore = asyncio.Semaphore(concurrent_scans)
        
        async def scan_host(ip: str) -> Optional[DiscoveredMiner]:
            async with semaphore:
                for port in self.scan_ports:
                    miner = await self._probe_miner(str(ip), port)
                    if miner:
                        return miner
            return None
        
        # Scan all hosts concurrently
        tasks = [scan_host(str(ip)) for ip in hosts]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        for result in results:
            if isinstance(result, DiscoveredMiner):
                discovered.append(result)
                async with self._lock:
                    self._miners[result.id] = result
                    # Save to database
                    self._save_miner_to_db(result)
        
        logger.info(
            "Discovery complete",
            total_scanned=len(hosts),
            miners_found=len(discovered)
        )
        
        return discovered
    
    async def _probe_miner(self, ip: str, port: int) -> Optional[DiscoveredMiner]:
        """
        Probe a single IP:port for a miner.
        
        First tries CGMiner API, then falls back to Vnish Web API.
        
        Returns:
            DiscoveredMiner if found, None otherwise
        """
        api = CGMinerAPI(ip, port, timeout=self.scan_timeout)
        
        try:
            # Try to get summary - this works on most miners
            summary = await api.get_summary()
            
            # If we got here, there's a miner!
            miner = DiscoveredMiner(ip=ip, port=port, is_online=True)
            
            # Try to identify miner type and get more info
            await self._identify_miner(miner, summary)
            
            logger.info(
                "Miner discovered",
                ip=ip,
                type=miner.miner_type.value,
                model=miner.model
            )
            
            return miner
            
        except ConnectionError:
            # CGMiner API not responding - try Vnish Web API
            # This allows discovering miners in idle mode (cgminer stopped)
            vnish = VnishWebAPI(ip, timeout=self.scan_timeout * 2)
            try:
                sysinfo = await vnish.get_system_info()
                if sysinfo and "minertype" in sysinfo:
                    minertype = sysinfo.get("minertype", "")
                    
                    # Detect firmware type from minertype string
                    firmware_type = FirmwareType.UNKNOWN
                    firmware_version = ""
                    if "vnish" in minertype.lower():
                        firmware_type = FirmwareType.VNISH
                        # Parse version from "Antminer S9 (vnish 3.9.0)"
                        match = re.search(r'vnish\s*(\d+\.\d+\.?\d*)', minertype, re.IGNORECASE)
                        if match:
                            firmware_version = match.group(1)
                    elif "braiins" in minertype.lower() or "bos" in minertype.lower():
                        firmware_type = FirmwareType.BRAIINS
                    elif "marathon" in minertype.lower():
                        firmware_type = FirmwareType.MARATHON
                    else:
                        firmware_type = FirmwareType.STOCK
                    
                    # It's a Vnish miner in idle mode!
                    miner = DiscoveredMiner(
                        ip=ip,
                        port=port,
                        is_online=True,  # Vnish web UI responding
                        is_mining=False,  # CGMiner not running
                        miner_type=MinerType.ANTMINER,
                        model=minertype,
                        hostname=sysinfo.get("hostname", ""),
                        mac_address=sysinfo.get("macaddr", ""),
                        power_mode=MinerPowerMode.IDLE,
                        firmware_type=firmware_type,
                        firmware_version=firmware_version
                    )
                    
                    # Set estimated rated power based on model
                    miner.rated_power_watts = self._estimate_antminer_power(miner.model)
                    
                    logger.info(
                        "Miner discovered (idle mode via Vnish)",
                        ip=ip,
                        model=miner.model,
                        firmware=firmware_type.value,
                        firmware_version=firmware_version
                    )
                    return miner
            except Exception as e:
                logger.debug("Vnish probe also failed", ip=ip, error=str(e))
            
            return None
        except Exception as e:
            logger.debug("Probe failed", ip=ip, error=str(e))
            return None
    
    async def _identify_miner(
        self,
        miner: DiscoveredMiner,
        summary: Dict[str, Any]
    ):
        """Identify miner type and extract information from summary."""
        api = CGMinerAPI(miner.ip, miner.port, timeout=self.api_timeout)
        
        # Extract summary info
        summary_data = summary.get("SUMMARY", [{}])[0]
        
        # Common fields - CGMiner sometimes returns strings, so convert to float
        hashrate_raw = summary_data.get("GHS 5s", 0) or summary_data.get("GHS av", 0)
        miner.hashrate_ghs = float(hashrate_raw) if hashrate_raw else 0.0
        miner.uptime_seconds = int(summary_data.get("Elapsed", 0) or 0)
        
        # Try to get version for model info
        try:
            version = await api.get_version()
            version_data = version.get("VERSION", [{}])[0]
            
            # Detect miner type from version
            miner_type = version_data.get("Type", "").lower()
            cgminer = version_data.get("CGMiner", "").lower()
            miner_str = version_data.get("Miner", "").lower()
            
            if "antminer" in miner_type or "antminer" in cgminer:
                miner.miner_type = MinerType.ANTMINER
                miner.model = version_data.get("Type", "Antminer")
                # Set rated power based on Antminer model
                miner.rated_power_watts = self._estimate_antminer_power(miner.model)
            elif "whatsminer" in miner_type or "btminer" in cgminer or "whatsminer" in miner_str:
                miner.miner_type = MinerType.WHATSMINER
                miner.model = version_data.get("Type", "Whatsminer")
                miner.rated_power_watts = 3400.0  # Default Whatsminer
            elif "avalon" in miner_type:
                miner.miner_type = MinerType.AVALON
                miner.model = version_data.get("Type", "Avalon")
                miner.rated_power_watts = 3200.0  # Default Avalon
            else:
                miner.miner_type = MinerType.CGMINER
                miner.model = version_data.get("Type", "Unknown CGMiner")
                
        except Exception:
            miner.miner_type = MinerType.CGMINER
            miner.model = "Unknown"
        
        # Try to detect firmware type via Vnish Web API
        if miner.miner_type == MinerType.ANTMINER:
            try:
                vnish = VnishWebAPI(miner.ip, timeout=self.api_timeout)
                sysinfo = await vnish.get_system_info()
                if sysinfo and "minertype" in sysinfo:
                    minertype = sysinfo.get("minertype", "")
                    
                    # Update model with full info from web API
                    miner.model = minertype
                    
                    # Detect firmware type
                    if "vnish" in minertype.lower():
                        miner.firmware_type = FirmwareType.VNISH
                        match = re.search(r'vnish\s*(\d+\.\d+\.?\d*)', minertype, re.IGNORECASE)
                        if match:
                            miner.firmware_version = match.group(1)
                    elif "braiins" in minertype.lower() or "bos" in minertype.lower():
                        miner.firmware_type = FirmwareType.BRAIINS
                    elif "marathon" in minertype.lower():
                        miner.firmware_type = FirmwareType.MARATHON
                    else:
                        miner.firmware_type = FirmwareType.STOCK
                    
                    # Get MAC address if not already set
                    if not miner.mac_address:
                        miner.mac_address = sysinfo.get("macaddr", "")
                    if not miner.hostname:
                        miner.hostname = sysinfo.get("hostname", "")
            except Exception:
                # Vnish API not available, assume stock firmware
                miner.firmware_type = FirmwareType.STOCK
        
        # Try to get stats for power info (varies by miner type)
        try:
            stats = await api.get_stats()
            await self._extract_power_info(miner, stats)
            # Only update rated power if current reading is HIGHER than estimate
            # (miner might be running at reduced frequency)
            if miner.power_watts > miner.rated_power_watts:
                miner.rated_power_watts = miner.power_watts * 1.1
        except Exception:
            pass
        
        # Check if mining (has valid pool with accepted shares)
        try:
            pools = await api.get_pools()
            pool_data = pools.get("POOLS", [])
            if pool_data:
                active_pool = pool_data[0]
                miner.pool_url = active_pool.get("URL", "")
                miner.is_mining = active_pool.get("Status", "") == "Alive"
        except Exception:
            pass
    
    async def _extract_power_info(
        self,
        miner: DiscoveredMiner,
        stats: Dict[str, Any]
    ):
        """Extract power and temperature info from stats."""
        stats_data = stats.get("STATS", [])
        
        for stat in stats_data:
            # Antminer/Vnish style - look for temp2_X fields (chip temps) or temp_max
            if "temp_max" in stat:
                miner.temperature_c = float(stat["temp_max"])
            elif "temp2_1" in stat or "temp2_6" in stat:
                # Get max temperature from temp2_X fields (chip temps)
                temps = [v for k, v in stat.items() 
                        if k.startswith("temp2_") and isinstance(v, (int, float)) and v > 0]
                if temps:
                    miner.temperature_c = max(temps)
            elif "temp_chip" in stat:
                temps = [v for k, v in stat.items() 
                        if k.startswith("temp") and isinstance(v, (int, float)) and v > 0]
                if temps:
                    miner.temperature_c = max(temps)
            
            # Get fan speed - fans are in RPM, convert to approximate %
            fans = [v for k, v in stat.items() 
                   if k.startswith("fan") and isinstance(v, (int, float)) and v > 0]
            if fans:
                max_fan_rpm = max(fans)
                # Antminer S9 max fan RPM is ~6000, estimate percentage
                miner.fan_speed_pct = min(100.0, (max_fan_rpm / 6000.0) * 100.0)
            
            # Power - check various formats
            # Vnish reports chain_consumption per hashboard
            chain_power = [v for k, v in stat.items() 
                          if k.startswith("chain_consumption") and isinstance(v, (int, float)) and v > 0]
            if chain_power:
                miner.power_watts = sum(chain_power)
            elif "Power" in stat:
                miner.power_watts = stat["Power"]
            elif "chain_power" in stat:
                miner.power_watts = stat["chain_power"]
            
            # Whatsminer style  
            if "Temperature" in stat and miner.temperature_c == 0:
                miner.temperature_c = stat.get("Temperature", 0)
            if "Fan Speed In" in stat and miner.fan_speed_pct == 0:
                miner.fan_speed_pct = stat.get("Fan Speed In", 0)
    
    # =========================================================================
    # Miner Status Updates
    # =========================================================================
    
    async def update_miner_status(self, miner_id: str) -> Optional[DiscoveredMiner]:
        """
        Update status for a single miner.
        
        Args:
            miner_id: The miner ID to update
            
        Returns:
            Updated miner or None if not found/offline
        """
        async with self._lock:
            miner = self._miners.get(miner_id)
            if not miner:
                return None
        
        api = CGMinerAPI(miner.ip, miner.port, timeout=self.api_timeout)
        
        try:
            # Get summary
            summary = await api.get_summary()
            summary_data = summary.get("SUMMARY", [{}])[0]
            
            # CGMiner sometimes returns strings, so convert to float
            hashrate_raw = summary_data.get("GHS 5s", 0) or summary_data.get("GHS av", 0)
            miner.hashrate_ghs = float(hashrate_raw) if hashrate_raw else 0.0
            miner.uptime_seconds = int(summary_data.get("Elapsed", 0) or 0)
            miner.is_online = True
            miner.last_seen = datetime.utcnow()
            miner.consecutive_failures = 0
            miner.power_mode = MinerPowerMode.NORMAL
            
            # Get detailed stats
            try:
                stats = await api.get_stats()
                await self._extract_power_info(miner, stats)
            except Exception:
                pass
            
            # Check pools for mining status
            try:
                pools = await api.get_pools()
                pool_data = pools.get("POOLS", [])
                if pool_data:
                    active_pool = pool_data[0]
                    miner.pool_url = active_pool.get("URL", "")
                    miner.is_mining = (
                        active_pool.get("Status", "") == "Alive" and
                        miner.hashrate_ghs > 0
                    )
            except Exception:
                pass
            
            # Get current frequency from miner config (for frequency-based power control)
            try:
                vnish = VnishWebAPI(miner.ip)
                config = await vnish.get_config()
                freq_str = config.get("bitmain-freq", "")
                if freq_str:
                    miner.current_frequency = int(freq_str)
                    logger.debug(
                        "Read miner frequency",
                        miner_id=miner_id,
                        ip=miner.ip,
                        frequency=miner.current_frequency
                    )
            except Exception:
                pass
            
            return miner
            
        except ConnectionError:
            # CGMiner API not responding - check if it's in idle mode via Vnish Web API
            logger.debug("CGMiner API not responding, trying Vnish Web API", ip=miner.ip)
            
            vnish = VnishWebAPI(miner.ip)
            try:
                if await vnish.is_vnish_available():
                    # Miner is reachable but CGMiner is stopped (idle mode)
                    miner.is_online = True  # Vnish web UI is responding
                    miner.is_mining = False  # CGMiner not running
                    miner.hashrate_ghs = 0
                    miner.power_watts = 0  # Not consuming mining power
                    miner.power_mode = MinerPowerMode.IDLE
                    miner.last_seen = datetime.utcnow()
                    miner.consecutive_failures = 0
                    logger.info(
                        "Miner in idle mode (CGMiner stopped)",
                        miner_id=miner_id,
                        ip=miner.ip
                    )
                    return miner
            except Exception as e:
                logger.debug("Vnish Web API also not available", ip=miner.ip, error=str(e))
            
            # Neither CGMiner API nor Vnish Web API responding
            # But if we recently sent a command, the miner might be transitioning
            if miner.is_transitioning:
                # Keep current online state during transition grace period
                logger.debug(
                    "Miner not responding but in transition grace period",
                    miner_id=miner_id,
                    ip=miner.ip,
                    command=miner.last_command_type,
                    elapsed_s=(datetime.utcnow() - miner.last_command_time).total_seconds()
                )
                # Don't mark offline during transition, but don't reset failures either
                return miner
            
            # Truly offline - not in transition
            miner.is_online = False
            miner.is_mining = False
            miner.consecutive_failures += 1
            logger.warning(
                "Miner offline",
                miner_id=miner_id,
                ip=miner.ip,
                failures=miner.consecutive_failures
            )
            return miner
    
    async def update_all_miners(self) -> List[DiscoveredMiner]:
        """Update status for all registered miners."""
        miner_ids = list(self._miners.keys())
        tasks = [self.update_miner_status(mid) for mid in miner_ids]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        updated = []
        for result in results:
            if isinstance(result, DiscoveredMiner):
                updated.append(result)
                # Save snapshot for historical data
                self._save_miner_snapshot(result)
        
        return updated
    
    # =========================================================================
    # Miner Control
    # =========================================================================
    
    async def set_miner_idle(self, miner_id: str) -> Tuple[bool, str]:
        """
        Put a miner into idle mode.
        
        For most ASICs, this is done by:
        1. Using Vnish Web API to stop CGMiner (preferred for Vnish firmware)
        2. Or disabling pools via CGMiner API (fallback)
        
        The miner stays on and can resume instantly.
        
        Args:
            miner_id: The miner to idle
            
        Returns:
            Tuple of (success, message)
        """
        miner = self._miners.get(miner_id)
        if not miner:
            return False, f"Miner {miner_id} not found"
        
        # Note: We don't check is_online here because we can use Vnish Web API
        # even if CGMiner is not responding
        
        # Different strategies based on miner type
        if miner.miner_type == MinerType.WHATSMINER:
            return await self._set_whatsminer_power_mode(miner, "low")
        else:
            # Generic: Try to disable pools or switch to invalid pool
            return await self._disable_miner_pools(miner)
    
    async def set_miner_active(self, miner_id: str) -> Tuple[bool, str]:
        """
        Resume a miner from idle mode.
        
        For Vnish firmware, uses the Web API to restart CGMiner.
        For others, re-enables pools via CGMiner API.
        
        Args:
            miner_id: The miner to activate
            
        Returns:
            Tuple of (success, message)
        """
        miner = self._miners.get(miner_id)
        if not miner:
            return False, f"Miner {miner_id} not found"
        
        # Note: We don't strictly check is_online because Vnish Web API
        # can be available even when CGMiner is stopped
        
        if miner.miner_type == MinerType.WHATSMINER:
            return await self._set_whatsminer_power_mode(miner, "normal")
        else:
            return await self._enable_miner_pools(miner)
    
    async def restart_miner(self, miner_id: str) -> Tuple[bool, str]:
        """
        Soft restart a miner (restart cgminer software only).
        
        This is a quick restart that just restarts the mining software
        without rebooting the entire system.
        
        NOTE: The miner typically doesn't send a response - it just restarts.
        The restart takes ~30-45 seconds to complete.
        
        Args:
            miner_id: The miner to restart
            
        Returns:
            Tuple of (success, message)
        """
        miner = self._miners.get(miner_id)
        if not miner:
            return False, f"Miner {miner_id} not found"
        
        # Mark that we're sending a restart command - miner will be unavailable
        miner.mark_command_sent('restart', grace_seconds=60)
        
        # Try Vnish Web API first for more reliable restart
        vnish = VnishWebAPI(miner.ip)
        try:
            if await vnish.is_vnish_available():
                try:
                    await vnish.start_cgminer()
                except Exception:
                    # Restart command often doesn't return a response - that's OK
                    pass
                logger.info("Miner soft restart initiated via Vnish API", miner_id=miner_id, ip=miner.ip)
                return True, "CGMiner restart initiated (takes ~30-45s)"
        except Exception as e:
            logger.debug("Vnish restart failed, trying CGMiner API", error=str(e))
        
        # Fallback to CGMiner API
        api = CGMinerAPI(miner.ip, miner.port, timeout=self.api_timeout)
        
        try:
            await api.send_command("restart")
        except Exception:
            # Restart command often doesn't return a response - that's expected
            pass
        
        logger.info("Miner restart command sent", miner_id=miner_id)
        return True, "Restart command sent (takes ~30-45s)"
    
    async def reboot_miner(self, miner_id: str) -> Tuple[bool, str]:
        """
        Full system reboot of a miner.
        
        This reboots the entire system, not just the mining software.
        Takes approximately 90-120 seconds to complete.
        
        NOTE: The miner typically doesn't send a response - it just reboots.
        
        Args:
            miner_id: The miner to reboot
            
        Returns:
            Tuple of (success, message)
        """
        miner = self._miners.get(miner_id)
        if not miner:
            return False, f"Miner {miner_id} not found"
        
        # Mark that we're sending a reboot command - miner will be unavailable for a while
        miner.mark_command_sent('reboot', grace_seconds=120)
        
        vnish = VnishWebAPI(miner.ip)
        
        try:
            # Reboot command usually doesn't return a response
            try:
                await vnish.reboot_system()
            except Exception:
                # No response expected - miner is rebooting
                pass
            logger.info("Miner full reboot initiated", miner_id=miner_id, ip=miner.ip)
            return True, "System reboot initiated (takes ~90-120 seconds)"
        except Exception as e:
            logger.error("Failed to send reboot command", miner_id=miner_id, ip=miner.ip, error=str(e))
            return False, f"Reboot command failed: {e}"

    async def blink_miner(self, miner_ip: str) -> Tuple[bool, str]:
        """
        Toggle a miner's find mode (LED blinking) on or off.
        
        Since the miner doesn't provide a way to query current state,
        we track it locally and toggle between on/off.
        
        Args:
            miner_ip: IP address of the miner
            
        Returns:
            Tuple of (success, message, is_enabled)
        """
        # Find miner by IP
        miner = None
        for m in self._miners.values():
            if m.ip == miner_ip:
                miner = m
                break
        
        if not miner:
            return False, f"Miner at {miner_ip} not found", False
        
        vnish = VnishWebAPI(miner_ip)
        
        try:
            # Track find mode state per miner (default to False/off)
            if not hasattr(self, '_find_mode_states'):
                self._find_mode_states = {}
            
            current_state = self._find_mode_states.get(miner_ip, False)
            new_state = not current_state
            
            success, response = await vnish.set_find_mode(enable=new_state)
            
            if success:
                self._find_mode_states[miner_ip] = new_state
                return True, response, new_state
            else:
                return False, f"Failed: {response}", current_state
                
        except Exception as e:
            logger.error("Failed to toggle miner find mode", ip=miner_ip, error=str(e))
            return False, f"Toggle failed: {e}", False
    
    async def factory_reset_miner(self, miner_id: str) -> Tuple[bool, str]:
        """
        Factory reset a miner to default configuration.
        
        This resets the miner configuration to defaults including:
        - Default pool settings (cleared)
        - Default frequency/voltage settings
        - Default fan settings
        
        Note: Network settings are NOT reset, only mining config.
        
        Args:
            miner_id: The miner to reset
            
        Returns:
            Tuple of (success, message)
        """
        miner = self._miners.get(miner_id)
        if not miner:
            return False, f"Miner {miner_id} not found"
        
        vnish = VnishWebAPI(miner.ip)
        
        try:
            if not await vnish.is_vnish_available():
                return False, "Vnish Web API not available - factory reset requires Vnish firmware"
            
            # Reset to default Vnish config
            # Clear pools and set default mining parameters
            default_config = {
                # Clear all pools
                "_ant_pool1url": "",
                "_ant_pool1user": "",
                "_ant_pool1pw": "",
                "_ant_pool2url": "",
                "_ant_pool2user": "",
                "_ant_pool2pw": "",
                "_ant_pool3url": "",
                "_ant_pool3user": "",
                "_ant_pool3pw": "",
                # Default mining settings for S9
                "_ant_freq": "550",
                "_ant_voltage": "8.8",
                "_ant_fan_customize_switch": "false",
                "_ant_fan_customize_value": "100",
                "_ant_fan_rpm_off": "0",
                "_ant_target_temp": "75",
                "_ant_tempoff": "105",
                "_ant_asicboost": "true",
                "_ant_nobeeper": "false",
                "_ant_notempoverctrl": "false",
                # Auto-downscale settings
                "_ant_autodownscale_timer": "2",
                "_ant_autodownscale_after": "10",
                "_ant_autodownscale_step": "25",
                "_ant_autodownscale_min": "400",
                "_ant_autodownscale_prec": "75",
                "_ant_autodownscale_profile": "1",
            }
            
            result = await vnish._post_config("/cgi-bin/set_miner_conf_custom.cgi", default_config)
            
            if result.get("success") or result.get("status") == 200:
                logger.info("Miner factory reset completed", miner_id=miner_id, ip=miner.ip)
                return True, "Factory reset completed - pools cleared, default settings restored"
            else:
                return False, f"Factory reset failed: {result}"
                
        except Exception as e:
            logger.error("Failed to factory reset miner", miner_id=miner_id, ip=miner.ip, error=str(e))
            return False, f"Factory reset failed: {e}"

    async def _set_whatsminer_power_mode(
        self,
        miner: DiscoveredMiner,
        mode: str
    ) -> Tuple[bool, str]:
        """
        Set Whatsminer power mode.
        
        Whatsminer supports: low, normal, high modes via API.
        """
        api = CGMinerAPI(miner.ip, miner.port, timeout=self.api_timeout)
        
        try:
            # Whatsminer uses "set_power_pct" or similar commands
            # The exact command varies by firmware version
            result = await api.send_command("set_power_mode", mode)
            
            status = result.get("STATUS", [{}])[0]
            if status.get("STATUS") == "S":
                miner.power_mode = MinerPowerMode.LOW if mode == "low" else MinerPowerMode.NORMAL
                return True, f"Power mode set to {mode}"
            else:
                return False, f"Failed: {status.get('Msg', 'Unknown error')}"
                
        except Exception as e:
            logger.warning(
                "Whatsminer power mode command failed, trying pool disable",
                error=str(e)
            )
            # Fall back to pool disable method
            if mode == "low":
                return await self._disable_miner_pools(miner)
            else:
                return await self._enable_miner_pools(miner)
    
    async def _disable_miner_pools(self, miner: DiscoveredMiner) -> Tuple[bool, str]:
        """
        Put miner into idle mode using Vnish sleep mode API.
        
        This uses the do_sleep_mode.cgi endpoint which is the safest way
        to pause mining - it preserves all config and pools.
        
        NO fallback to CGMiner API pool manipulation - that breaks configs!
        """
        vnish = VnishWebAPI(miner.ip)
        
        try:
            logger.info("Setting miner to sleep mode", ip=miner.ip)
            
            # Mark that we're sending a command - miner may fluctuate for ~30s
            miner.mark_command_sent('sleep', grace_seconds=45)
            
            # Use sleep mode API directly - don't check is_vnish_available
            # because that might fail if CGMiner is already in a weird state
            if await vnish.set_sleep_mode(enable=True):
                miner.power_mode = MinerPowerMode.IDLE
                miner.is_mining = False
                miner.hashrate_ghs = 0
                logger.info("Miner entered sleep mode", ip=miner.ip)
                return True, "Miner entered sleep mode"
            else:
                logger.warning("Sleep mode command failed", ip=miner.ip)
                return False, "Failed to set sleep mode"
                
        except Exception as e:
            logger.error("Failed to set sleep mode", ip=miner.ip, error=str(e))
            return False, f"Failed: {e}"
    
    async def _enable_miner_pools(self, miner: DiscoveredMiner) -> Tuple[bool, str]:
        """
        Wake miner from sleep mode using Vnish sleep mode API.
        
        This uses the do_sleep_mode.cgi endpoint with mode=0 which
        wakes the miner and resumes mining.
        
        NOTE: Waking takes 45-60 seconds. During this time:
        - Miner may appear offline briefly
        - Then idle again
        - Then finally mining
        """
        vnish = VnishWebAPI(miner.ip)
        
        try:
            logger.info("Waking miner from sleep mode", ip=miner.ip)
            
            # Mark that we're sending a wake command
            # Waking takes 45-60s, during which the miner status will fluctuate
            miner.mark_command_sent('wake', grace_seconds=75)
            
            # Use sleep mode API directly to wake - don't check is_vnish_available
            # because that calls get_system_info which might fail when sleeping
            if await vnish.set_sleep_mode(enable=False):
                miner.power_mode = MinerPowerMode.NORMAL
                # Don't set is_mining=True yet - will be confirmed on next status update
                logger.info("Miner wake command sent - will take 45-60s to resume", ip=miner.ip)
                return True, "Wake command sent - miner resuming (takes ~45-60s)"
            else:
                logger.warning("Wake from sleep command failed", ip=miner.ip)
                return False, "Failed to wake from sleep"
                
        except Exception as e:
            logger.error("Failed to wake from sleep", ip=miner.ip, error=str(e))
            return False, f"Failed: {e}"
    
    async def set_miner_frequency(
        self,
        miner_id: str,
        frequency: int
    ) -> Tuple[bool, str]:
        """
        Set miner frequency for power scaling.
        
        This allows proportional power control by adjusting frequency.
        Lower frequency = lower power consumption (and hashrate).
        
        For S9 miners, typical frequency range is ~300-700 MHz.
        Power consumption scales roughly linearly with frequency.
        
        Args:
            miner_id: The miner to configure
            frequency: Target frequency in MHz (e.g., 550)
            
        Returns:
            Tuple of (success, message)
        """
        miner = self._miners.get(miner_id)
        if not miner:
            return False, f"Miner {miner_id} not found"
        
        vnish = VnishWebAPI(miner.ip)
        
        try:
            if not await vnish.is_vnish_available():
                return False, "Vnish Web API not available - frequency control requires Vnish firmware"
            
            # Mark that we're sending a config change - saving config may reboot the miner
            miner.mark_command_sent('config', grace_seconds=120)
            
            result = await vnish.set_vnish_profile(frequency=str(frequency))
            
            if result.get("success") or result.get("status") == 200:
                # Store the frequency setting on the miner object
                miner.current_frequency = frequency
                logger.info(
                    "Miner frequency set",
                    miner_id=miner_id,
                    ip=miner.ip,
                    frequency=frequency
                )
                return True, f"Frequency set to {frequency} MHz"
            else:
                return False, f"Failed to set frequency: {result}"
                
        except Exception as e:
            logger.error(
                "Failed to set miner frequency",
                miner_id=miner_id,
                ip=miner.ip,
                error=str(e)
            )
            return False, f"Error: {e}"
    
    async def get_miner_frequency(self, miner_id: str) -> Optional[int]:
        """
        Get current miner frequency.
        
        Args:
            miner_id: The miner to query
            
        Returns:
            Current frequency in MHz, or None if unavailable
        """
        miner = self._miners.get(miner_id)
        if not miner:
            return None
        
        vnish = VnishWebAPI(miner.ip)
        
        try:
            if not await vnish.is_vnish_available():
                return None
            
            config = await vnish.get_miner_config()
            freq_str = config.get("bitmain-freq", "")
            
            if freq_str:
                return int(freq_str)
            return None
            
        except Exception as e:
            logger.debug("Failed to get miner frequency", miner_id=miner_id, error=str(e))
            return None
    
    # =========================================================================
    # Manual Miner Management
    # =========================================================================
    
    async def add_miner(
        self,
        ip: str,
        port: int = 4028,
        rated_power_watts: float = 3000.0
    ) -> Tuple[bool, Optional[DiscoveredMiner]]:
        """
        Manually add a miner by IP address.
        
        Args:
            ip: Miner IP address
            port: API port (default 4028)
            rated_power_watts: Rated power consumption
            
        Returns:
            Tuple of (success, miner or None)
        """
        miner = await self._probe_miner(ip, port)
        
        if miner:
            miner.rated_power_watts = rated_power_watts
            async with self._lock:
                self._miners[miner.id] = miner
            # Save to database
            self._save_miner_to_db(miner)
            logger.info("Miner added manually", ip=ip, model=miner.model)
            return True, miner
        else:
            logger.warning("Failed to add miner - not responding", ip=ip)
            return False, None
    
    def remove_miner(self, miner_id: str) -> bool:
        """Remove a miner from the registry."""
        if miner_id in self._miners:
            del self._miners[miner_id]
            logger.info("Miner removed", miner_id=miner_id)
            return True
        return False
    
    def configure_miner_power(self, miner_id: str, rated_power_watts: float):
        """Configure rated power for a miner."""
        if miner_id in self._miners:
            self._miners[miner_id].rated_power_watts = rated_power_watts
            logger.info(
                "Miner power configured",
                miner_id=miner_id,
                rated_watts=rated_power_watts
            )
    
    # =========================================================================
    # Persistence
    # =========================================================================
    
    def export_miners(self) -> List[Dict[str, Any]]:
        """Export miner registry for persistence."""
        return [
            {
                "ip": m.ip,
                "port": m.port,
                "rated_power_watts": m.rated_power_watts,
                "miner_type": m.miner_type.value,
                "model": m.model
            }
            for m in self._miners.values()
        ]
    
    async def import_miners(self, miner_configs: List[Dict[str, Any]]):
        """Import miners from saved configuration."""
        for config in miner_configs:
            await self.add_miner(
                ip=config["ip"],
                port=config.get("port", 4028),
                rated_power_watts=config.get("rated_power_watts", 3000.0)
            )


# Singleton instance
_discovery_service: Optional[MinerDiscoveryService] = None


def get_discovery_service() -> MinerDiscoveryService:
    """Get the singleton discovery service instance."""
    global _discovery_service
    if _discovery_service is None:
        _discovery_service = MinerDiscoveryService()
    return _discovery_service
