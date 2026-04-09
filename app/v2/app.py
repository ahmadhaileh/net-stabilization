"""
Grid Stabilization v2 — FastAPI application entry point.

Maestro → SectionManagers architecture.
"""
import asyncio
import logging
import os
import sys
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
import structlog

from app.config import get_settings
from app.v2.maestro import create_maestro, get_maestro
from app.v2.power_meter import PowerMeterService
from app.v2.api import ems, dash

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
log_level = os.environ.get("LOG_LEVEL", "INFO").upper()
logging.basicConfig(
    format="%(message)s",
    stream=sys.stdout,
    level=getattr(logging, log_level, logging.INFO),
)
structlog.configure(
    processors=[
        structlog.stdlib.filter_by_level,
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.format_exc_info,
        structlog.processors.UnicodeDecoder(),
        structlog.processors.JSONRenderer(),
    ],
    wrapper_class=structlog.stdlib.BoundLogger,
    context_class=dict,
    logger_factory=structlog.stdlib.LoggerFactory(),
    cache_logger_on_first_use=True,
)

logger = structlog.get_logger()
settings = get_settings()


# ---------------------------------------------------------------------------
# Lifespan
# ---------------------------------------------------------------------------
@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting Grid Stabilization v2",
                network=settings.miner_network_cidr,
                port=settings.host_port)

    meter = PowerMeterService(
        host=settings.power_meter_host,
        port=settings.power_meter_port,
        timeout=settings.power_meter_timeout,
        enabled=settings.power_meter_enabled,
    )

    maestro = create_maestro(
        network_cidr=settings.miner_network_cidr,
        vnish_username=settings.vnish_username,
        vnish_password=settings.vnish_password,
        power_meter=meter,
        idle_on_startup=settings.idle_all_on_startup,
    )

    await maestro.start()
    logger.info("Maestro started",
                sections=len(maestro.managers),
                miners=maestro.total_miners)

    yield

    logger.info("Shutting down")
    await maestro.stop()


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------
app = FastAPI(
    title="Grid Stabilization v2",
    version="2.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(ems)
app.include_router(dash)
app.mount("/static", StaticFiles(directory="app/v2/static"), name="static")
