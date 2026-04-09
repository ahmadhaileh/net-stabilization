"""
Power meter client — reads actual power from the BESS EMS measurement device.

API: GET http://<host>:<port>/api/miners/get-measurement-data
Response: { "minersTotalPower": <kW>, "plantTotalPower": <kW>, "voltage": <V> }
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

import httpx
import structlog

logger = structlog.get_logger()


@dataclass
class PowerMeterReading:
    miners_total_power_kw: float
    plant_total_power_kw: float
    voltage: float = 0.0
    timestamp: datetime = field(default_factory=datetime.utcnow)


class PowerMeterService:
    def __init__(self, host: str = "192.168.95.4", port: int = 8044,
                 timeout: float = 3.0, enabled: bool = True):
        self._endpoint = f"http://{host}:{port}/api/miners/get-measurement-data"
        self._timeout = timeout
        self._enabled = enabled
        self._last: Optional[PowerMeterReading] = None
        self._failures: int = 0

    @property
    def last_reading(self) -> Optional[PowerMeterReading]:
        return self._last

    async def get_power(self) -> Optional[PowerMeterReading]:
        if not self._enabled:
            return None
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as c:
                r = await c.get(self._endpoint)
                r.raise_for_status()
                d = r.json()
            reading = PowerMeterReading(
                miners_total_power_kw=float(d["minersTotalPower"]),
                plant_total_power_kw=float(d["plantTotalPower"]),
                voltage=float(d.get("voltage", 0.0)),
            )
            self._last = reading
            self._failures = 0
            return reading
        except Exception as e:
            self._failures += 1
            if self._failures <= 3:
                logger.warning("Meter read failed", err=str(e),
                               failures=self._failures)
            return None
