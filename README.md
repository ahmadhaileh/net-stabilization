# Net Stabilization - Mining Fleet Power Control

A control system for managing cryptocurrency mining fleet power consumption, enabling participation in electricity grid stabilization services through dynamic load adjustment.

## Overview

Net Stabilization bridges Energy Management Systems (EMS) with ASIC mining hardware, providing:

-   **EMS Protocol Integration** - REST API for grid operator dispatch commands
-   **Direct Miner Control** - Native Vnish/CGMiner API support (no AwesomeMiner dependency)
-   **Real-time Dashboard** - Web UI for fleet monitoring and manual control
-   **Power Management** - Intelligent power distribution across mining fleet

## Architecture

```
┌─────────────────┐                    ┌──────────────────────────────────────┐
│   EMS Server    │◄──── HTTP/JSON ───►│       Net Stabilization Server       │
│   (Grid Ops)    │   /api/status      │                                      │
└─────────────────┘   /api/activate    │  ┌──────────────────────────────┐   │
                      /api/deactivate  │  │      Web Dashboard           │   │
                                       │  │  • Fleet Overview            │   │
                                       │  │  • Miner Details/Config      │   │
                                       │  │  • Per-Chip Health Viz       │   │
                                       │  │  • Real-time Charts          │   │
                                       │  └──────────────────────────────┘   │
                                       │                                      │
                                       │  ┌──────────────────────────────┐   │
                                       │  │    Miner Discovery Service   │   │
                                       │  │  • Auto-scan network         │   │
                                       │  │  • Firmware detection        │   │
                                       │  │  • CGMiner/Vnish API         │   │
                                       │  └──────────────────────────────┘   │
                                       └────────────────┬─────────────────────┘
                                                        │
                              ┌─────────────────────────┼─────────────────────────┐
                              │                         │                         │
                              ▼                         ▼                         ▼
                    ┌──────────────────┐     ┌──────────────────┐     ┌──────────────────┐
                    │  Antminer S9/T9  │     │  Antminer L3+    │     │   Other ASIC     │
                    │  Vnish 3.9.x     │     │  Vnish 3.9.x     │     │   CGMiner API    │
                    └──────────────────┘     └──────────────────┘     └──────────────────┘
```

## Features

### EMS Integration

-   `GET /api/status` - Real-time fleet operational state
-   `POST /api/activate` - Start mining at specified power level
-   `POST /api/deactivate` - Stop all mining operations

### Dashboard Features

-   **Fleet Overview** - Total hashrate, power consumption, efficiency metrics
-   **Miner Cards** - Per-miner status with firmware badges (Vnish/BraiinsOS/Stock)
-   **Detailed Modal** - Comprehensive miner information with tabs:
    -   Overview: Status, hashrate, power, temperatures
    -   Hash Boards: Per-board stats with chip-level hashrate visualization
    -   Pools: Pool configuration and status
    -   Charts: Real-time hashrate/temp/power graphs
    -   Config: Frequency, voltage, thermal, auto-downscale settings
    -   System: Network info, firmware version, uptime

### Miner Control

-   Start/Stop/Reboot individual miners
-   Frequency and voltage adjustment (global and per-board)
-   Quick presets (Low Power, Balanced, Performance)
-   Auto-downscale thermal throttling
-   Sleep mode for immediate power reduction
-   Pool configuration

### Supported Hardware

-   **Antminer S9/T9** - Vnish 3.9.x firmware (full feature support)
-   **Antminer L3+** - Vnish 3.9.x firmware
-   **Generic CGMiner** - Basic monitoring and control

## Quick Start

### Prerequisites

-   Docker and Docker Compose
-   Network access to ASIC miners (ports 80 and 4028)

### Configuration

1. Copy environment template:

    ```bash
    cp .env.example .env
    ```

2. Configure settings in `.env`:

    ```env
    # Server settings
    HOST=0.0.0.0
    PORT=8080

    # Miner discovery
    MINER_DISCOVERY_ENABLED=true
    MINER_NETWORK_RANGE=192.168.1.0/24

    # Fleet configuration
    TOTAL_RATED_POWER_KW=50.0
    ```

### Running

```bash
# Build and start
docker-compose up -d

# View logs
docker-compose logs -f

# Stop
docker-compose down
```

### Development Mode

```bash
# Install dependencies
pip install -r requirements.txt

# Run development server
./run_dev.sh
```

### Access

-   **Web Dashboard:** http://localhost:8080/
-   **EMS API:** http://localhost:8080/api/
-   **Dashboard API:** http://localhost:8080/dashboard/api/

## API Documentation

### EMS Protocol

#### GET /api/status

```json
{
    "isAvailableForDispatch": true,
    "runningStatus": 2,
    "ratedPowerInKw": 50.0,
    "activePowerInKw": 45.0
}
```

#### POST /api/activate

```json
// Request
{"activationPowerInKw": 30.0}

// Response
{"accepted": true, "message": "Fleet activated successfully."}
```

#### POST /api/deactivate

```json
// Response
{ "accepted": true, "message": "Fleet deactivation command accepted." }
```

### Dashboard API

See [docs/VNISH_API_REFERENCE.md](docs/VNISH_API_REFERENCE.md) for detailed miner API documentation.

Key endpoints:

-   `GET /dashboard/api/discovery/miners` - List all discovered miners
-   `GET /dashboard/api/miner/{ip}/details` - Comprehensive miner details
-   `GET /dashboard/api/miner/{ip}/chip-hashrate` - Per-chip hashrate data
-   `POST /dashboard/api/miner/{ip}/config` - Update miner configuration

## Project Structure

```
net-stabilization/
├── app/
│   ├── api/
│   │   ├── dashboard.py    # Dashboard REST API
│   │   └── ems.py          # EMS protocol endpoints
│   ├── models/
│   │   ├── ems.py          # EMS data models
│   │   ├── miner.py        # Miner data models
│   │   └── state.py        # Fleet state models
│   ├── services/
│   │   ├── fleet_manager.py    # Power distribution logic
│   │   └── miner_discovery.py  # Miner discovery & control
│   ├── static/
│   │   ├── css/style.css   # Dashboard styles
│   │   └── js/dashboard.js # Dashboard JavaScript
│   ├── templates/
│   │   └── dashboard.html  # Dashboard template
│   ├── config.py           # Configuration management
│   └── main.py             # FastAPI application
├── docs/
│   └── VNISH_API_REFERENCE.md  # Vnish API documentation
├── scripts/
│   └── mock_awesome_miner.py   # Mock server for testing
├── tests/
│   ├── test_ems_api.py
│   └── test_fleet_manager.py
├── docker-compose.yml
├── Dockerfile
├── requirements.txt
└── README.md
```

## Configuration Options

| Variable                  | Description                    | Default          |
| ------------------------- | ------------------------------ | ---------------- |
| `HOST`                    | Server bind address            | `0.0.0.0`        |
| `PORT`                    | Server port                    | `8080`           |
| `MINER_DISCOVERY_ENABLED` | Enable direct miner discovery  | `true`           |
| `MINER_NETWORK_RANGE`     | Network CIDR for scanning      | `192.168.1.0/24` |
| `MINER_POLL_INTERVAL`     | Status poll interval (seconds) | `30`             |
| `TOTAL_RATED_POWER_KW`    | Fleet rated power capacity     | `50.0`           |
| `MIN_MINER_POWER_PERCENT` | Minimum power per miner        | `50`             |
| `LOG_LEVEL`               | Logging level                  | `INFO`           |

## License

Proprietary - All rights reserved
