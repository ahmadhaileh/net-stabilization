"""
Power Meter Service - Reads actual power from the BESS EMS measurement device.

This provides ground-truth power readings from a physical meter installed
at the plant, replacing the estimated sums from individual miner reports
which can be inaccurate when miners lose connection.

API: GET http://<host>:<port>/api/miners/get-measurement-data
Response: { "minersTotalPower": <kW>, "plantTotalPower": <kW>, "voltage": <V> }

The voltage field is critical for safety: voltage == 0 means the mining
container has lost power. When power is restored the server must idle
the entire fleet because miners may have rebooted into a hashing state.
"""
import asyncio
from datetime import datetime
from typing import Optional, Tuple

import httpx
import structlog

from app.config import Settings, get_settings

logger = structlog.get_logger()


class PowerMeterReading:
    """A single reading from the power meter."""
    __slots__ = ("miners_total_power_kw", "plant_total_power_kw", "voltage", "timestamp")

    def __init__(self, miners_total_power_kw: float, plant_total_power_kw: float, voltage: float = 0.0):
        self.miners_total_power_kw = miners_total_power_kw
        self.plant_total_power_kw = plant_total_power_kw
        self.voltage = voltage
        self.timestamp = datetime.utcnow()


class PowerMeterService:
    """
    Client for the BESS EMS power measurement API.

    Provides the measured total power consumed by miners,
    which is more accurate than summing individual miner reports.
    Falls back gracefully when the meter is unreachable.
    """

    def __init__(self, settings: Optional[Settings] = None):
        self.settings = settings or get_settings()
        self._base_url = (
            f"http://{self.settings.power_meter_host}:{self.settings.power_meter_port}"
        )
        self._endpoint = f"{self._base_url}/api/miners/get-measurement-data"
        self._timeout = self.settings.power_meter_timeout
        self._last_reading: Optional[PowerMeterReading] = None
        self._consecutive_failures: int = 0
        self._enabled = self.settings.power_meter_enabled
        logger.info(
            "Power meter service initialised",
            enabled=self._enabled,
            endpoint=self._endpoint,
        )

    @property
    def last_reading(self) -> Optional[PowerMeterReading]:
        return self._last_reading

    @property
    def is_healthy(self) -> bool:
        return self._consecutive_failures < 3

    async def get_power(self) -> Optional[PowerMeterReading]:
        """
        Fetch current power from the measurement device.

        Returns a PowerMeterReading on success, or None if the meter
        is disabled / unreachable / returns an error.
        """
        if not self._enabled:
            return None

        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                resp = await client.get(self._endpoint)
                resp.raise_for_status()
                data = resp.json()

            miners_kw = float(data["minersTotalPower"])
            plant_kw = float(data["plantTotalPower"])
            voltage = float(data.get("voltage", 0.0))

            reading = PowerMeterReading(miners_kw, plant_kw, voltage)
            self._last_reading = reading
            self._consecutive_failures = 0

            logger.debug(
                "Power meter reading",
                miners_kw=round(miners_kw, 2),
                plant_kw=round(plant_kw, 2),
                voltage=round(voltage, 1),
            )
            return reading

        except httpx.HTTPStatusError as e:
            self._consecutive_failures += 1
            logger.warning(
                "Power meter HTTP error",
                status=e.response.status_code,
                failures=self._consecutive_failures,
            )
            return None
        except (httpx.RequestError, KeyError, ValueError, TypeError) as e:
            self._consecutive_failures += 1
            logger.warning(
                "Power meter read failed",
                error=str(e),
                failures=self._consecutive_failures,
            )
            return None


# ── Singleton ─────────────────────────────────────────────────────
_power_meter_service: Optional[PowerMeterService] = None


def get_power_meter_service() -> PowerMeterService:
    """Get the singleton power meter service instance."""
    global _power_meter_service
    if _power_meter_service is None:
        _power_meter_service = PowerMeterService()
    return _power_meter_service
