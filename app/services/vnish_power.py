"""
Vnish Power Control Service.

This module provides fine-grained power control for miners with Vnish firmware
by adjusting frequency and voltage settings.

Key features:
- Power-to-frequency mapping based on real S9 measurements
- Swing miner frequency calculation for fractional power
- Safe frequency transitions with CGMiner restart
- Power estimation from current frequency
"""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Optional, Dict, Any, List, Tuple
import httpx
import requests
from requests.auth import HTTPDigestAuth
import structlog

logger = structlog.get_logger()


@dataclass
class PowerFrequencyPoint:
    """A single data point mapping frequency to power consumption."""
    frequency_mhz: int
    power_watts: int
    hashrate_ths: float  # Terahash/s
    voltage: float  # Volts


# Power-Frequency mapping for Antminer S9 with Vnish firmware
# Based on actual measurements and Mining Profiles from web UI
# Format: (frequency_mhz, power_watts, hashrate_ths, voltage)
S9_POWER_CURVE = [
    PowerFrequencyPoint(350, 660, 7.5, 8.2),    # Measured: 663W at 350M
    PowerFrequencyPoint(387, 875, 10.0, 8.4),   # Mining Profile: LPM
    PowerFrequencyPoint(450, 950, 11.0, 8.5),   # Interpolated
    PowerFrequencyPoint(481, 1020, 11.0, 8.6),  # Mining Profile
    PowerFrequencyPoint(525, 1145, 12.0, 8.7),  # Mining Profile
    PowerFrequencyPoint(550, 1250, 12.5, 8.8),  # Interpolated
    PowerFrequencyPoint(575, 1285, 13.0, 8.8),  # Mining Profile
    PowerFrequencyPoint(600, 1350, 13.3, 8.9),  # Interpolated
    PowerFrequencyPoint(650, 1460, 13.7, 8.9),  # Measured: 1461W at 650M
    PowerFrequencyPoint(700, 1650, 15.0, 9.0),  # Interpolated
    PowerFrequencyPoint(750, 1850, 16.0, 9.1),  # Interpolated
    PowerFrequencyPoint(800, 1900, 17.0, 9.2),  # Mining Profile
]

# Valid frequencies available on Vnish firmware (from web UI dropdown)
VALID_FREQUENCIES = [
    100, 125, 150, 175, 200, 225, 250, 275, 300, 325, 350, 375, 400,
    404, 406, 408, 412, 416, 418, 420, 425, 429, 431, 433, 437, 441,
    443, 445, 450, 454, 456, 458, 462, 466, 468, 470, 475, 479, 481,
    483, 487, 491, 493, 495, 500, 504, 506, 508, 512, 516, 518, 520,
    525, 529, 531, 533, 537, 543, 550, 556, 562, 568, 575, 581, 587,
    593, 600, 606, 612, 618, 625, 631, 637, 643, 650, 656, 662, 668,
    675, 681, 687, 693, 700, 706, 712, 718, 725, 731, 737, 743, 750,
    756, 762, 768, 775, 781, 787, 793, 800, 825, 850, 875, 900, 925,
    950, 975, 1000, 1025, 1050, 1075, 1100, 1125, 1150, 1175
]


class VnishPowerService:
    """
    Service for controlling miner power via frequency adjustment.
    
    This service provides:
    - Power-to-frequency conversion
    - Frequency-to-power estimation
    - Safe frequency change via Vnish API
    - Optimal voltage selection for each frequency
    """
    
    def __init__(self):
        # Power curve - can be customized per miner model
        self.power_curve = S9_POWER_CURVE
        
        # Cache of current miner frequencies
        self._miner_frequencies: Dict[str, int] = {}
        
        # Default settings
        self.min_frequency = 300  # Don't go below this
        self.max_frequency = 800  # Don't go above this for safety
        self.default_frequency = 650  # Full power frequency
        
        # Hysteresis to avoid constant changes
        self.min_power_change_watts = 50  # Ignore changes smaller than this
        self.min_frequency_change = 25  # Don't change freq by less than 25 MHz
    
    def power_to_frequency(self, target_power_watts: int) -> Tuple[int, float]:
        """
        Convert target power to optimal frequency.
        
        Uses linear interpolation between known power curve points.
        
        Args:
            target_power_watts: Desired power consumption in watts
            
        Returns:
            Tuple of (frequency_mhz, voltage)
        """
        # Clamp to valid range
        min_power = self.power_curve[0].power_watts
        max_power = self.power_curve[-1].power_watts
        
        if target_power_watts <= min_power:
            point = self.power_curve[0]
            return self._snap_to_valid_frequency(point.frequency_mhz), point.voltage
        
        if target_power_watts >= max_power:
            point = self.power_curve[-1]
            return self._snap_to_valid_frequency(point.frequency_mhz), point.voltage
        
        # Find the two points to interpolate between
        for i in range(len(self.power_curve) - 1):
            p1 = self.power_curve[i]
            p2 = self.power_curve[i + 1]
            
            if p1.power_watts <= target_power_watts <= p2.power_watts:
                # Linear interpolation
                ratio = (target_power_watts - p1.power_watts) / (p2.power_watts - p1.power_watts)
                freq = int(p1.frequency_mhz + ratio * (p2.frequency_mhz - p1.frequency_mhz))
                voltage = p1.voltage + ratio * (p2.voltage - p1.voltage)
                
                return self._snap_to_valid_frequency(freq), round(voltage, 1)
        
        # Fallback
        return self.default_frequency, 8.9
    
    def frequency_to_power(self, frequency_mhz: int) -> int:
        """
        Estimate power consumption for a given frequency.
        
        Args:
            frequency_mhz: Mining frequency in MHz
            
        Returns:
            Estimated power in watts
        """
        # Find the two points to interpolate between
        for i in range(len(self.power_curve) - 1):
            p1 = self.power_curve[i]
            p2 = self.power_curve[i + 1]
            
            if p1.frequency_mhz <= frequency_mhz <= p2.frequency_mhz:
                # Linear interpolation
                ratio = (frequency_mhz - p1.frequency_mhz) / (p2.frequency_mhz - p1.frequency_mhz)
                power = int(p1.power_watts + ratio * (p2.power_watts - p1.power_watts))
                return power
        
        # Outside range - extrapolate from nearest point
        if frequency_mhz < self.power_curve[0].frequency_mhz:
            # Below minimum - linear extrapolation
            p = self.power_curve[0]
            ratio = frequency_mhz / p.frequency_mhz
            return int(p.power_watts * ratio)
        else:
            # Above maximum
            p = self.power_curve[-1]
            ratio = frequency_mhz / p.frequency_mhz
            return int(p.power_watts * ratio)
    
    def _snap_to_valid_frequency(self, freq: int) -> int:
        """Snap a frequency to the nearest valid value."""
        # Find closest valid frequency
        closest = min(VALID_FREQUENCIES, key=lambda x: abs(x - freq))
        
        # Apply min/max limits
        return max(self.min_frequency, min(self.max_frequency, closest))
    
    def get_voltage_for_frequency(self, frequency_mhz: int) -> float:
        """Get recommended voltage for a frequency."""
        # Find surrounding points
        for i in range(len(self.power_curve) - 1):
            p1 = self.power_curve[i]
            p2 = self.power_curve[i + 1]
            
            if p1.frequency_mhz <= frequency_mhz <= p2.frequency_mhz:
                ratio = (frequency_mhz - p1.frequency_mhz) / (p2.frequency_mhz - p1.frequency_mhz)
                return round(p1.voltage + ratio * (p2.voltage - p1.voltage), 1)
        
        # Default voltage
        return 8.9
    
    def calculate_swing_miner_frequency(
        self,
        remaining_power_watts: int,
        miner_full_power_watts: int = 1460
    ) -> Tuple[int, float, int]:
        """
        Calculate the frequency for a "swing" miner to achieve remaining power.
        
        The swing miner is the one that runs at partial power to achieve
        fine-grained power control.
        
        Args:
            remaining_power_watts: How much power we need from this miner
            miner_full_power_watts: Full power consumption of this miner type
            
        Returns:
            Tuple of (frequency_mhz, voltage, estimated_power_watts)
        """
        # If remaining power is very small, use minimum frequency
        if remaining_power_watts < self.power_curve[0].power_watts * 0.5:
            point = self.power_curve[0]
            return self.min_frequency, point.voltage, self.frequency_to_power(self.min_frequency)
        
        # If remaining power is close to full, use full power
        if remaining_power_watts >= miner_full_power_watts * 0.95:
            return self.default_frequency, 8.9, miner_full_power_watts
        
        # Calculate optimal frequency
        freq, voltage = self.power_to_frequency(remaining_power_watts)
        estimated_power = self.frequency_to_power(freq)
        
        logger.info(
            "Swing miner frequency calculated",
            target_power=remaining_power_watts,
            frequency=freq,
            voltage=voltage,
            estimated_power=estimated_power
        )
        
        return freq, voltage, estimated_power
    
    async def set_miner_frequency(
        self,
        host: str,
        frequency_mhz: int,
        voltage: Optional[float] = None,
        username: str = "root",
        password: str = "root"
    ) -> Tuple[bool, str]:
        """
        Set miner frequency via Vnish API.
        
        Uses requests library with DigestAuth (httpx has async digest issues).
        Runs in executor to avoid blocking the event loop.
        
        Args:
            host: Miner IP address
            frequency_mhz: Target frequency in MHz
            voltage: Target voltage (auto-selected if None)
            username: Web UI username
            password: Web UI password
            
        Returns:
            Tuple of (success, message)
        """
        if voltage is None:
            voltage = self.get_voltage_for_frequency(frequency_mhz)
        
        # Snap to valid frequency
        frequency_mhz = self._snap_to_valid_frequency(frequency_mhz)
        voltage_int = int(voltage * 100)  # Convert 8.9 to 890
        
        logger.info(
            "Setting miner frequency",
            host=host,
            frequency=frequency_mhz,
            voltage=voltage
        )
        
        def _set_config_sync():
            """Synchronous config update using requests with retry."""
            import time
            auth = HTTPDigestAuth(username, password)
            base_url = f"http://{host}"
            
            # Retry logic for connection issues (miner might be restarting)
            max_retries = 3
            retry_delay = 10  # seconds
            
            for attempt in range(max_retries):
                try:
                    # Step 1: Get current config to preserve pool settings
                    resp = requests.get(
                        f"{base_url}/cgi-bin/get_miner_conf.cgi",
                        auth=auth,
                        timeout=30
                    )
                    if resp.status_code == 200:
                        break  # Success, continue
                except (requests.exceptions.ConnectionError, requests.exceptions.Timeout) as e:
                    if attempt < max_retries - 1:
                        time.sleep(retry_delay)
                        continue
                    return False, f"Connection failed after {max_retries} attempts: {e}"
            
            if resp.status_code != 200:
                return False, f"Failed to get config: HTTP {resp.status_code}"
            
            current_config = resp.json()
            pools = current_config.get("pools", [])
            
            pool1 = pools[0] if len(pools) > 0 else {}
            pool2 = pools[1] if len(pools) > 1 else {}
            pool3 = pools[2] if len(pools) > 2 else {}
            
            # Step 2: Build form data in exact order vnish expects
            form_data = [
                ("_ant_pool1url", pool1.get("url", "")),
                ("_ant_pool1user", pool1.get("user", "")),
                ("_ant_pool1pw", pool1.get("pass", "")),
                ("_ant_pool2url", pool2.get("url", "")),
                ("_ant_pool2user", pool2.get("user", "")),
                ("_ant_pool2pw", pool2.get("pass", "")),
                ("_ant_pool3url", pool3.get("url", "")),
                ("_ant_pool3user", pool3.get("user", "")),
                ("_ant_pool3pw", pool3.get("pass", "")),
                ("_ant_nobeeper", "false"),
                ("_ant_notempoverctrl", "false"),
                ("_ant_fan_customize_switch", "false"),
                ("_ant_fan_customize_value", "100"),
                ("_ant_freq", str(frequency_mhz)),
                ("_ant_freq1", "0"),
                ("_ant_freq2", "0"),
                ("_ant_freq3", "0"),
                ("_ant_voltage", str(voltage_int)),
                ("_ant_voltage1", "0"),
                ("_ant_voltage2", "0"),
                ("_ant_voltage3", "0"),
                ("_ant_fan_rpm_off", "0"),
                ("_ant_chip_freq", ""),
                ("_ant_autodownscale", "false"),
                ("_ant_autodownscale_watch", "false"),
                ("_ant_autodownscale_watchtimer", "false"),
                ("_ant_autodownscale_timer", "1"),
                ("_ant_autodownscale_after", "120"),
                ("_ant_autodownscale_step", "2"),
                ("_ant_autodownscale_min", "650"),
                ("_ant_autodownscale_prec", "75"),
                ("_ant_autodownscale_profile", "0"),
                ("_ant_minhr", "0"),
                ("_ant_asicboost", "false"),
                ("_ant_tempoff", "0"),
                ("_ant_altdf", "true"),
                ("_ant_presave", "1"),
                ("_ant_name", "0"),
                ("_ant_warn", ""),
                ("_ant_maxx", ""),
                ("_ant_trigger_reboot", ""),
                ("_ant_target_temp", "0"),
                ("_ant_silentstart", "false"),
                ("_ant_altdfno", "0"),
                ("_ant_autodownscale_reboot", "false"),
                ("_ant_hotel_fee", "false"),
                ("_ant_lpm_mode", "false"),
                ("_ant_dchain5", "false"),
                ("_ant_dchain6", "false"),
                ("_ant_dchain7", "false"),
            ]
            
            # Step 3: Post config
            resp = requests.post(
                f"{base_url}/cgi-bin/set_miner_conf_custom.cgi",
                auth=auth,
                data=form_data,
                timeout=30
            )
            if resp.status_code != 200:
                return False, f"Failed to set config: HTTP {resp.status_code}"
            
            # Step 4: Restart CGMiner - use very short timeout, it never responds
            try:
                requests.get(
                    f"{base_url}/cgi-bin/reboot_cgminer.cgi",
                    auth=auth,
                    timeout=2
                )
            except (requests.Timeout, requests.exceptions.ReadTimeout, requests.exceptions.ConnectionError):
                pass  # Expected - endpoint never responds
            
            return True, f"Frequency set to {frequency_mhz}MHz @ {voltage}V"
        
        try:
            loop = asyncio.get_event_loop()
            success, message = await loop.run_in_executor(None, _set_config_sync)
            
            if success:
                self._miner_frequencies[host] = frequency_mhz
                logger.info(
                    "Miner frequency set successfully",
                    host=host,
                    frequency=frequency_mhz,
                    voltage=voltage
                )
            
            return success, message
            
        except Exception as e:
            logger.error("Failed to set frequency", host=host, error=str(e))
            return False, str(e)
    
    async def get_miner_frequency(
        self,
        host: str,
        username: str = "root",
        password: str = "root"
    ) -> Optional[int]:
        """
        Get current miner frequency from Vnish API.
        
        Args:
            host: Miner IP address
            username: Web UI username
            password: Web UI password
            
        Returns:
            Current frequency in MHz, or None if unavailable
        """
        try:
            auth = httpx.DigestAuth(username, password)
            
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get(
                    f"http://{host}/cgi-bin/get_miner_conf.cgi",
                    auth=auth
                )
                
                if response.status_code == 200:
                    config = response.json()
                    freq_str = config.get("bitmain-freq", "")
                    if freq_str:
                        return int(freq_str)
                        
        except Exception as e:
            logger.debug("Failed to get miner frequency", host=host, error=str(e))
        
        return None
    
    def get_power_allocation(
        self,
        target_power_kw: float,
        miners: List[Dict[str, Any]],
        full_power_watts: int = 1460
    ) -> List[Dict[str, Any]]:
        """
        Calculate power allocation for a fleet of miners.
        
        Strategy: "Full + Swing"
        - Calculate how many miners run at full power
        - One "swing" miner runs at variable frequency for remainder
        - Remaining miners stay idle
        
        Args:
            target_power_kw: Total target power in kW
            miners: List of miner info dicts with 'ip', 'is_mining', 'is_online'
            full_power_watts: Power consumption of one miner at full frequency
            
        Returns:
            List of allocation dicts: {
                'ip': str,
                'action': 'full' | 'swing' | 'idle',
                'frequency': int (MHz),
                'voltage': float,
                'estimated_power': int (watts)
            }
        """
        target_power_watts = int(target_power_kw * 1000)
        
        # Filter to online miners only
        online_miners = [m for m in miners if m.get('is_online', False)]
        
        if not online_miners:
            return []
        
        # Minimum useful power for a swing miner (below this, just idle)
        min_useful_power = self.frequency_to_power(self.min_frequency)  # ~565W at 300MHz
        
        # Calculate allocation
        allocation = []
        remaining_power = target_power_watts
        
        # Sort miners by IP for consistent ordering
        sorted_miners = sorted(online_miners, key=lambda m: m.get('ip', ''))
        
        for i, miner in enumerate(sorted_miners):
            ip = miner.get('ip', '')
            
            if remaining_power <= 0:
                # No more power needed - idle this miner
                allocation.append({
                    'ip': ip,
                    'action': 'idle',
                    'frequency': 0,
                    'voltage': 0,
                    'estimated_power': 0
                })
                
            elif remaining_power >= full_power_watts:
                # Full power miner
                allocation.append({
                    'ip': ip,
                    'action': 'full',
                    'frequency': self.default_frequency,
                    'voltage': 8.9,
                    'estimated_power': full_power_watts
                })
                remaining_power -= full_power_watts
                
            elif remaining_power >= min_useful_power:
                # Swing miner - runs at partial power (only if remaining is useful)
                freq, voltage, est_power = self.calculate_swing_miner_frequency(
                    remaining_power,
                    full_power_watts
                )
                allocation.append({
                    'ip': ip,
                    'action': 'swing',
                    'frequency': freq,
                    'voltage': voltage,
                    'estimated_power': est_power
                })
                remaining_power = 0
            else:
                # Remaining power too small to be useful - idle this miner
                allocation.append({
                    'ip': ip,
                    'action': 'idle',
                    'frequency': 0,
                    'voltage': 0,
                    'estimated_power': 0
                })
        
        # Log allocation summary
        full_count = sum(1 for a in allocation if a['action'] == 'full')
        swing_count = sum(1 for a in allocation if a['action'] == 'swing')
        idle_count = sum(1 for a in allocation if a['action'] == 'idle')
        total_estimated = sum(a['estimated_power'] for a in allocation)
        
        logger.info(
            "Power allocation calculated",
            target_kw=target_power_kw,
            full_miners=full_count,
            swing_miners=swing_count,
            idle_miners=idle_count,
            estimated_power_watts=total_estimated
        )
        
        return allocation


# Singleton instance
_vnish_power_service: Optional[VnishPowerService] = None


def get_vnish_power_service() -> VnishPowerService:
    """Get the singleton VnishPowerService instance."""
    global _vnish_power_service
    if _vnish_power_service is None:
        _vnish_power_service = VnishPowerService()
    return _vnish_power_service
