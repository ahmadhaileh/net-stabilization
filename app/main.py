"""
Net Stabilization - Mining Fleet Power Control System

Main FastAPI application entry point.
"""
import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
import structlog

from app.config import get_settings
from app.api.ems import router as ems_router
from app.api.dashboard import router as dashboard_router
from app.services.fleet_manager import get_fleet_manager
from app.services.awesome_miner import get_awesome_miner_client

# Configure structured logging
structlog.configure(
    processors=[
        structlog.stdlib.filter_by_level,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.stdlib.PositionalArgumentsFormatter(),
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        structlog.processors.UnicodeDecoder(),
        structlog.processors.JSONRenderer()
    ],
    wrapper_class=structlog.stdlib.BoundLogger,
    context_class=dict,
    logger_factory=structlog.stdlib.LoggerFactory(),
    cache_logger_on_first_use=True,
)

logger = structlog.get_logger()
settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan manager for startup/shutdown."""
    # Startup
    logger.info(
        "Starting Net Stabilization server",
        awesome_miner_host=settings.awesome_miner_host,
        awesome_miner_port=settings.awesome_miner_port
    )
    
    # Initialize services
    fleet_manager = get_fleet_manager()
        
    # Start background polling
    await fleet_manager.start_polling()
    
    # Initial status update
    try:
        await fleet_manager.update_status()
        logger.info("Initial status update completed")
    except Exception as e:
        logger.warning("Initial status update failed", error=str(e))
    
    yield
    
    # Shutdown
    logger.info("Shutting down Net Stabilization server")
    
    # Stop background tasks
    await fleet_manager.stop_polling()
    
    # Close clients
    am_client = get_awesome_miner_client()
    await am_client.close()


# Create FastAPI application
app = FastAPI(
    title="Net Stabilization",
    description="""
    Mining Fleet Power Control System for EMS Integration.
    
    This system manages a cryptocurrency mining fleet as a dynamic electricity
    user for grid stabilization services. It implements the EMS protocol for
    external control and provides a web dashboard for monitoring.
    
    ## EMS Protocol Endpoints
    
    - **GET /api/status** - Get current operational status
    - **POST /api/activate** - Activate fleet at specified power
    - **POST /api/deactivate** - Deactivate fleet (standby mode)
    
    ## Dashboard
    
    Access the web dashboard at the root URL (/) for monitoring and manual control.
    """,
    version="1.0.0",
    lifespan=lifespan
)

# Add CORS middleware for development
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include routers
app.include_router(ems_router)
app.include_router(dashboard_router)

# Mount static files
app.mount("/static", StaticFiles(directory="app/static"), name="static")

# Templates
templates = Jinja2Templates(directory="app/templates")


# =========================================================================
# Root Routes
# =========================================================================

@app.get("/", response_class=HTMLResponse, include_in_schema=False)
async def dashboard(request: Request):
    """Serve the main dashboard page."""
    return templates.TemplateResponse(
        "dashboard.html",
        {"request": request}
    )


@app.get("/health", tags=["Health"])
async def health_check():
    """
    Health check endpoint for Docker/load balancer probes.
    
    Returns basic health status of the application.
    """
    try:
        fleet_manager = get_fleet_manager()
        return {
            "status": "healthy",
            "fleet_state": fleet_manager.status.state.value,
            "miners_online": fleet_manager.status.online_miners
        }
    except Exception as e:
        return JSONResponse(
            status_code=503,
            content={"status": "unhealthy", "error": str(e)}
        )


# =========================================================================
# Error Handlers
# =========================================================================

@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    """Global exception handler ensuring JSON responses."""
    logger.error(
        "Unhandled exception",
        error=str(exc),
        path=request.url.path,
        method=request.method
    )
    
    return JSONResponse(
        status_code=500,
        content={
            "accepted": False,
            "message": "Internal server error occurred."
        }
    )


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=8080,
        reload=True
    )
