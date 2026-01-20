# Net Stabilization API Documentation

**Version:** 1.0.0  
**Last Updated:** January 20, 2026  
**Base URL:** `http://<server-ip>:8000`

---

## Table of Contents

1. [Overview](#overview)
2. [Authentication](#authentication)
3. [Response Formats](#response-formats)
4. [EMS Protocol API](#ems-protocol-api)
   - [GET /api/status](#get-apistatus)
   - [POST /api/activate](#post-apiactivate)
   - [POST /api/deactivate](#post-apideactivate)
5. [Dashboard API](#dashboard-api)
   - [Fleet Status](#fleet-status-endpoints)
   - [Miner Status](#miner-status-endpoints)
   - [Control Endpoints](#control-endpoints)
   - [Power Control](#power-control-endpoints)
   - [Discovery](#discovery-endpoints)
   - [Configuration](#configuration-endpoints)
   - [History](#history-endpoints)
   - [Health Check](#health-check)
6. [Data Models](#data-models)
7. [Error Codes](#error-codes)
8. [Usage Examples](#usage-examples)

---

## Overview

The Net Stabilization system provides a REST API for integrating mining fleet power consumption with Energy Management Systems (EMS). The system supports:

- **Real-time power monitoring** - Track fleet and individual miner power consumption
- **Dynamic power control** - Activate/deactivate miners to meet target power levels
- **Frequency-based power scaling** - Fine-grained power control via frequency adjustment (300-650 MHz)
- **On/Off power control** - Coarse-grained control by turning miners on/off
- **Manual override** - Dashboard-based manual control that bypasses EMS commands

### System Architecture

```
┌──────────────────┐      ┌─────────────────────┐      ┌─────────────────┐
│                  │      │                     │      │                 │
│   EMS System     │◄────►│  Net Stabilization  │◄────►│  Mining Fleet   │
│  (Power Factory) │      │      Server         │      │  (S9 Miners)    │
│                  │      │                     │      │                 │
└──────────────────┘      └─────────────────────┘      └─────────────────┘
        │                          │
        │                          │
        └──────────┬───────────────┘
                   │
          ┌────────▼────────┐
          │    Dashboard    │
          │   (Web UI)      │
          └─────────────────┘
```

### Supported Hardware

| Hardware | Firmware | Power Range | Frequency Range |
|----------|----------|-------------|-----------------|
| Antminer S9 | Vnish 3.9.x | 300-1460W | 300-650 MHz |

---

## Authentication

### EMS API Endpoints (`/api/*`)

**No authentication required.** These endpoints are designed for machine-to-machine communication and should be protected at the network level.

### Dashboard API Endpoints (`/dashboard/api/*`)

**No authentication required.** Intended for internal dashboard use. Recommend restricting access via firewall rules in production.

### Miner Authentication

The system uses HTTP Digest Authentication to communicate with miners:
- **Username:** `root`
- **Password:** `root`

---

## Response Formats

All API responses use JSON format with UTF-8 encoding.

### Success Response

```json
{
  "field1": "value1",
  "field2": "value2"
}
```

### Error Response

```json
{
  "accepted": false,
  "message": "Human-readable error description"
}
```

### HTTP Status Codes

| Code | Description |
|------|-------------|
| 200 | Success |
| 400 | Bad Request - Invalid parameters |
| 404 | Not Found - Resource doesn't exist |
| 409 | Conflict - Operation blocked (e.g., manual override active) |
| 500 | Internal Server Error |
| 503 | Service Unavailable - Fleet offline |

---

## EMS Protocol API

These endpoints implement the EMS specification for third-party device integration. They are the primary interface for external power management systems.

### GET /api/status

Retrieves the real-time operational state of the mining fleet.

**Polling Frequency:** Every 1-5 seconds  
**Response Time:** ≤ 1 second

#### Request

```http
GET /api/status HTTP/1.1
Host: <server-ip>:8000
Accept: application/json
```

#### Response

```json
{
  "isAvailableForDispatch": true,
  "runningStatus": 2,
  "ratedPowerInKw": 2.92,
  "activePowerInKw": 2.45
}
```

#### Response Fields

| Field | Type | Description |
|-------|------|-------------|
| `isAvailableForDispatch` | boolean | `true` if fleet is ready to accept commands |
| `runningStatus` | integer | `1` = StandBy (idle), `2` = Running (mining) |
| `ratedPowerInKw` | float | Maximum power capacity in kilowatts |
| `activePowerInKw` | float | Current power consumption in kilowatts |

#### Running Status Values

| Value | Status | Description |
|-------|--------|-------------|
| 1 | StandBy | Fleet is idle, ready to activate |
| 2 | Running | Fleet is actively mining and consuming power |

#### Error Responses

| Status | Condition |
|--------|-----------|
| 503 | Fleet is offline or unreachable |
| 500 | Internal server error |

---

### POST /api/activate

Requests the fleet to start operation at a specified power level.

**Response Time:** ≤ 2 seconds

#### Request

```http
POST /api/activate HTTP/1.1
Host: <server-ip>:8000
Content-Type: application/json
Accept: application/json

{
  "activationPowerInKw": 2.0
}
```

#### Request Fields

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `activationPowerInKw` | float | Yes | Target power consumption in kW (must be ≥ 0 and ≤ rated power) |

#### Response (Success)

```json
{
  "accepted": true,
  "message": "Fleet activated successfully at 2000W (2 miners at full power)"
}
```

#### Response (Error)

```json
{
  "accepted": false,
  "message": "Requested power exceeds rated limits."
}
```

#### Error Responses

| Status | Condition |
|--------|-----------|
| 400 | Requested power exceeds rated capacity |
| 409 | Manual override is active OR fleet in fault state |
| 500 | Internal server error |

#### Power Control Behavior

The system uses one of two power control modes:

**Frequency Mode (`power_control_mode: frequency`):**
- Full-power miners run at 650 MHz (~1460W each)
- One "swing" miner adjusts frequency for fine-grained control
- Remaining miners stay idle

**On/Off Mode (`power_control_mode: on_off`):**
- Miners are simply turned on or off
- Coarser granularity but more reliable

---

### POST /api/deactivate

Stops the fleet's active operation and returns it to StandBy mode.

**Response Time:** ≤ 2 seconds

#### Request

```http
POST /api/deactivate HTTP/1.1
Host: <server-ip>:8000
Content-Type: application/json
Accept: application/json

{}
```

#### Response (Success)

```json
{
  "accepted": true,
  "message": "Fleet deactivation command accepted. All miners stopping."
}
```

#### Error Responses

| Status | Condition |
|--------|-----------|
| 409 | Manual override is active OR fleet in fault state |
| 500 | Internal server error |

---

## Dashboard API

Internal APIs for the web dashboard to monitor and control the fleet.

### Fleet Status Endpoints

#### GET /dashboard/api/status

Get detailed fleet status for dashboard display.

#### Response

```json
{
  "state": "active",
  "is_available_for_dispatch": true,
  "running_status": 2,
  "rated_power_kw": 2.92,
  "active_power_kw": 2.45,
  "target_power_kw": 2.0,
  "total_miners": 2,
  "online_miners": 2,
  "mining_miners": 2,
  "manual_override_active": false,
  "override_target_power_kw": null,
  "last_update": "2026-01-20T14:30:00Z",
  "last_ems_command": "2026-01-20T14:25:00Z",
  "errors": [],
  "power_control_mode": "on_off"
}
```

#### Response Fields

| Field | Type | Description |
|-------|------|-------------|
| `state` | string | Fleet state: `initializing`, `standby`, `activating`, `active`, `deactivating`, `fault` |
| `is_available_for_dispatch` | boolean | Ready for EMS commands |
| `running_status` | integer | 1=StandBy, 2=Running |
| `rated_power_kw` | float | Total fleet capacity |
| `active_power_kw` | float | Current consumption |
| `target_power_kw` | float | Target power (null if no target) |
| `total_miners` | integer | Total registered miners |
| `online_miners` | integer | Currently reachable miners |
| `mining_miners` | integer | Currently mining miners |
| `manual_override_active` | boolean | Manual override engaged |
| `override_target_power_kw` | float | Override power target |
| `last_update` | datetime | Last status update |
| `last_ems_command` | datetime | Last EMS command received |
| `errors` | array | List of error messages |
| `power_control_mode` | string | `frequency` or `on_off` |

---

### Miner Status Endpoints

#### GET /dashboard/api/miners

Get status of all miners.

#### Response

```json
[
  {
    "miner_id": "192.168.1.56",
    "name": "S9-56",
    "is_online": true,
    "is_mining": true,
    "power_kw": 1.46,
    "rated_power_kw": 1.46,
    "target_power_kw": null,
    "last_update": "2026-01-20T14:30:00Z",
    "error": null
  },
  {
    "miner_id": "192.168.1.167",
    "name": "S9-167",
    "is_online": true,
    "is_mining": true,
    "power_kw": 1.46,
    "rated_power_kw": 1.46,
    "target_power_kw": null,
    "last_update": "2026-01-20T14:30:00Z",
    "error": null
  }
]
```

---

#### GET /dashboard/api/miners/{miner_id}

Get status of a specific miner.

#### Parameters

| Parameter | Type | Description |
|-----------|------|-------------|
| `miner_id` | string | Miner ID (IP address) |

#### Response

Same format as individual miner in the array above.

---

#### GET /dashboard/api/miner/{miner_ip}/details

Get comprehensive details from a specific miner.

#### Response

```json
{
  "ip": "192.168.1.56",
  "status": { /* raw status data */ },
  "system": {
    "minertype": "Antminer S9",
    "hostname": "antMiner",
    "macaddr": "12:34:56:78:9A:BC",
    "ipaddress": "192.168.1.56",
    "firmware_version": "vnish 3.9.0",
    "uptime": "5d 12:34:56"
  },
  "config": {
    "frequency": "650",
    "voltage": "880",
    "fan_ctrl": false,
    "fan_pwm": "100",
    "target_temp": "75",
    "shutdown_temp": "105",
    "asicboost": true
  },
  "boards": [
    {
      "id": 1,
      "hashrate_ghs": 4500.0,
      "chip_temp": 72,
      "pcb_temp": 65,
      "power_watts": 486.67,
      "frequency_mhz": 650,
      "chips_total": 63,
      "chips_ok": 63,
      "status": "healthy"
    }
  ],
  "pools": [
    {
      "id": 0,
      "url": "stratum+tcp://pool.example.com:3333",
      "worker": "worker1",
      "status": "Alive",
      "accepted": 1234,
      "rejected": 5,
      "stratum_active": true
    }
  ],
  "shares": {
    "accepted": 1234,
    "rejected": 5,
    "stale": 2,
    "hw_errors": 0,
    "reject_rate": 0.40
  },
  "summary": {
    "elapsed": 432000,
    "hashrate_ghs_5s": 13500.0,
    "hashrate_ghs_avg": 13450.0,
    "frequency_mhz": 650,
    "fans": [{"id": 1, "rpm": 4200}, {"id": 2, "rpm": 4180}]
  }
}
```

---

### Control Endpoints

#### POST /dashboard/api/override

Enable or disable manual override mode.

#### Request

```json
{
  "enabled": true,
  "target_power_kw": 1.5
}
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `enabled` | boolean | Yes | Enable/disable override |
| `target_power_kw` | float | No | Target power when enabled (null = stop all) |

#### Response

```json
{
  "success": true,
  "message": "Manual override enabled at 1.5 kW",
  "override_active": true
}
```

---

#### GET /dashboard/api/power-mode

Get the current power control mode.

#### Response

```json
{
  "mode": "on_off"
}
```

---

#### POST /dashboard/api/power-mode

Set the power control mode.

#### Request

```json
{
  "mode": "frequency"
}
```

| Field | Type | Values | Description |
|-------|------|--------|-------------|
| `mode` | string | `frequency`, `on_off` | Power control strategy |

#### Response

```json
{
  "success": true,
  "message": "Power control mode set to frequency",
  "mode": "frequency"
}
```

---

#### POST /dashboard/api/miners/{miner_id}/control

Manually control a specific miner.

#### Request

```json
{
  "action": "stop"
}
```

| Action | Description | Duration |
|--------|-------------|----------|
| `start` | Resume mining (wake from sleep) | ~5-10 seconds |
| `stop` | Pause mining (enter sleep mode) | Immediate |
| `restart` | Soft restart (restart cgminer) | ~10-30 seconds |
| `reboot` | Full system reboot | ~60-90 seconds |
| `reset` | Factory reset | ~2-3 minutes |

#### Response

```json
{
  "success": true,
  "miner_id": "192.168.1.56",
  "action": "stop",
  "message": "Miner stopped successfully"
}
```

---

#### POST /dashboard/api/miner/{miner_ip}/blink

Toggle miner LED blinking to help locate it physically.

#### Response

```json
{
  "success": true,
  "miner_ip": "192.168.1.56",
  "message": "Find mode toggled",
  "is_enabled": true
}
```

---

### Power Control Endpoints

#### GET /dashboard/api/power/frequencies

Get list of valid frequencies for Vnish firmware.

#### Response

```json
{
  "valid_frequencies": [300, 325, 350, 375, 400, 425, 450, 475, 500, 525, 550, 575, 600, 625, 650],
  "min_frequency": 300,
  "max_frequency": 650,
  "default_frequency": 650
}
```

---

#### GET /dashboard/api/power/curve

Get the power-frequency mapping curve for S9 miners.

#### Response

```json
{
  "curve": [
    {"frequency_mhz": 300, "power_watts": 600, "hashrate_ths": 6.5, "voltage": 8.1},
    {"frequency_mhz": 350, "power_watts": 700, "hashrate_ths": 7.5, "voltage": 8.2},
    {"frequency_mhz": 400, "power_watts": 800, "hashrate_ths": 8.6, "voltage": 8.3},
    {"frequency_mhz": 450, "power_watts": 900, "hashrate_ths": 9.6, "voltage": 8.4},
    {"frequency_mhz": 500, "power_watts": 1000, "hashrate_ths": 10.7, "voltage": 8.5},
    {"frequency_mhz": 550, "power_watts": 1140, "hashrate_ths": 11.8, "voltage": 8.6},
    {"frequency_mhz": 600, "power_watts": 1280, "hashrate_ths": 12.9, "voltage": 8.7},
    {"frequency_mhz": 650, "power_watts": 1460, "hashrate_ths": 14.0, "voltage": 8.8}
  ]
}
```

---

#### POST /dashboard/api/power/calculate

Calculate power allocation for a target power level without applying changes.

#### Request

```json
{
  "target_power_kw": 2.0
}
```

#### Response

```json
{
  "target_power_kw": 2.0,
  "allocation": [
    {"ip": "192.168.1.56", "action": "full", "frequency": 650, "voltage": 8.8, "estimated_power": 1460},
    {"ip": "192.168.1.167", "action": "swing", "frequency": 475, "voltage": 8.4, "estimated_power": 540}
  ],
  "summary": {
    "full_miners": 1,
    "swing_miners": 1,
    "idle_miners": 0,
    "estimated_power_watts": 2000,
    "estimated_power_kw": 2.0
  }
}
```

---

#### POST /dashboard/api/power/apply

Calculate and apply power allocation for a target power level.

#### Request

```json
{
  "target_power_kw": 2.0
}
```

#### Response

```json
{
  "success": true,
  "message": "Fleet activated at 2.0 kW",
  "target_power_kw": 2.0
}
```

---

#### POST /dashboard/api/power/set-frequency

Set a specific miner's frequency directly.

#### Request

```json
{
  "miner_ip": "192.168.1.56",
  "frequency_mhz": 550,
  "voltage": 8.6
}
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `miner_ip` | string | Yes | Miner IP address |
| `frequency_mhz` | integer | Yes | Frequency (100-1175 MHz) |
| `voltage` | float | No | Voltage (auto-selected if not specified) |

#### Response

```json
{
  "success": true,
  "message": "Frequency set to 550 MHz",
  "miner_ip": "192.168.1.56",
  "frequency_mhz": 550,
  "voltage": 8.6,
  "estimated_power_watts": 1140
}
```

---

#### GET /dashboard/api/power/miner/{miner_ip}

Get current power-related info for a specific miner.

#### Response

```json
{
  "miner_ip": "192.168.1.56",
  "frequency_mhz": 650,
  "voltage": 8.8,
  "estimated_power_watts": 1460,
  "estimated_power_kw": 1.46
}
```

---

### Discovery Endpoints

#### POST /dashboard/api/discovery/scan

Scan the network for miners.

#### Request (Optional)

```json
{
  "network_cidr": "192.168.1.0/24"
}
```

#### Response

```json
{
  "success": true,
  "miners_found": 2,
  "network_scanned": "192.168.1.0/24",
  "miners": [
    {
      "id": "192.168.1.56",
      "ip": "192.168.1.56",
      "model": "Antminer S9",
      "type": "asic",
      "is_online": true,
      "is_mining": true
    }
  ]
}
```

---

#### POST /dashboard/api/discovery/add

Manually add a miner by IP address.

#### Request

```json
{
  "ip": "192.168.1.100",
  "port": 4028,
  "rated_power_watts": 1460.0
}
```

#### Response

```json
{
  "success": true,
  "miner": {
    "id": "192.168.1.100",
    "ip": "192.168.1.100",
    "model": "Antminer S9",
    "type": "asic",
    "is_online": true,
    "rated_power_watts": 1460.0
  }
}
```

---

#### DELETE /dashboard/api/discovery/miners/{miner_id}

Remove a miner from the registry.

#### Response

```json
{
  "success": true,
  "miner_id": "192.168.1.100"
}
```

---

#### GET /dashboard/api/discovery/miners

Get all discovered miners with detailed information.

#### Response

```json
{
  "miners": [
    {
      "id": "192.168.1.56",
      "ip": "192.168.1.56",
      "port": 4028,
      "model": "Antminer S9",
      "type": "asic",
      "firmware_type": "vnish",
      "firmware_version": "3.9.0",
      "is_online": true,
      "is_mining": true,
      "hashrate_ghs": 13500.0,
      "power_watts": 1460.0,
      "power_kw": 1.46,
      "rated_power_watts": 1460.0,
      "rated_power_kw": 1.46,
      "temperature_c": 72,
      "fan_speed_pct": 65,
      "power_mode": "normal",
      "pool_url": "stratum+tcp://pool.example.com:3333",
      "uptime_seconds": 432000,
      "last_seen": "2026-01-20T14:30:00Z",
      "consecutive_failures": 0
    }
  ],
  "total": 2,
  "online": 2,
  "mining": 2
}
```

---

### Configuration Endpoints

#### GET /dashboard/api/config

Get current runtime configuration.

#### Response

```json
{
  "rated_power_kw_override": null,
  "power_distribution_strategy": "equal",
  "miner_priority": [],
  "max_power_change_rate_kw_per_sec": 50.0,
  "min_miner_power_percent": 0
}
```

---

#### PATCH /dashboard/api/config

Update runtime configuration.

#### Request

```json
{
  "rated_power_kw_override": 3.0,
  "max_power_change_rate_kw_per_sec": 100.0
}
```

---

### History Endpoints

#### GET /dashboard/api/history

Get recent command history.

#### Parameters

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `limit` | integer | 100 | Maximum records to return |

#### Response

```json
{
  "commands": [
    {
      "timestamp": "2026-01-20T14:25:00Z",
      "source": "ems",
      "command": "activate",
      "parameters": {"power_kw": 2.0},
      "success": true,
      "message": "Fleet activated at 2.0 kW"
    }
  ]
}
```

---

### Health Check

#### GET /dashboard/api/health

Check health of all services.

#### Response

```json
{
  "healthy": true,
  "mode": "direct",
  "network_cidr": "192.168.1.0/24",
  "services": {
    "miner_discovery": true,
    "fleet_manager": true,
    "awesome_miner": "disabled"
  },
  "miners_registered": 2,
  "miners_online": 2,
  "fleet_state": "active",
  "timestamp": "2026-01-20T14:30:00.000Z"
}
```

---

## Data Models

### Fleet States

| State | Description |
|-------|-------------|
| `initializing` | System starting up |
| `standby` | Fleet idle, ready for commands |
| `activating` | Transitioning to active state |
| `active` | Mining at target power |
| `deactivating` | Transitioning to standby |
| `fault` | Error condition |

### Power Modes

| Mode | Description |
|------|-------------|
| `normal` | Full power operation |
| `sleep` | Idle/standby mode |

### Miner Types

| Type | Description |
|------|-------------|
| `asic` | ASIC miner (e.g., Antminer S9) |

### Firmware Types

| Type | Description |
|------|-------------|
| `vnish` | Vnish custom firmware |
| `stock` | Bitmain stock firmware |
| `unknown` | Unidentified firmware |

---

## Error Codes

### EMS API Errors

| HTTP Status | Error Code | Description |
|-------------|------------|-------------|
| 400 | INVALID_POWER | Requested power exceeds capacity |
| 409 | MANUAL_OVERRIDE | Manual override is active |
| 409 | FAULT_STATE | Fleet is in fault state |
| 503 | FLEET_OFFLINE | Fleet is unreachable |
| 500 | INTERNAL_ERROR | Unexpected server error |

### Common Error Response Format

```json
{
  "accepted": false,
  "message": "Detailed error description"
}
```

---

## Usage Examples

### Example 1: Basic EMS Integration Loop

```python
import requests
import time

BASE_URL = "http://192.168.1.100:8000"

def ems_control_loop():
    while True:
        # 1. Get current status
        status = requests.get(f"{BASE_URL}/api/status").json()
        
        print(f"Power: {status['activePowerInKw']} / {status['ratedPowerInKw']} kW")
        print(f"Status: {'Running' if status['runningStatus'] == 2 else 'StandBy'}")
        
        # 2. Make power decision based on grid needs
        target_power = calculate_target_power()  # Your logic here
        
        # 3. Send command
        if target_power > 0:
            response = requests.post(
                f"{BASE_URL}/api/activate",
                json={"activationPowerInKw": target_power}
            )
        else:
            response = requests.post(
                f"{BASE_URL}/api/deactivate",
                json={}
            )
        
        result = response.json()
        print(f"Command {'accepted' if result.get('accepted') else 'rejected'}: {result.get('message')}")
        
        time.sleep(5)  # Poll every 5 seconds
```

### Example 2: cURL Commands

**Get Status:**
```bash
curl -X GET "http://192.168.1.100:8000/api/status" \
  -H "Accept: application/json"
```

**Activate at 2.5 kW:**
```bash
curl -X POST "http://192.168.1.100:8000/api/activate" \
  -H "Content-Type: application/json" \
  -H "Accept: application/json" \
  -d '{"activationPowerInKw": 2.5}'
```

**Deactivate:**
```bash
curl -X POST "http://192.168.1.100:8000/api/deactivate" \
  -H "Content-Type: application/json" \
  -H "Accept: application/json" \
  -d '{}'
```

**Set Power Mode:**
```bash
curl -X POST "http://192.168.1.100:8000/dashboard/api/power-mode" \
  -H "Content-Type: application/json" \
  -d '{"mode": "frequency"}'
```

**Enable Manual Override:**
```bash
curl -X POST "http://192.168.1.100:8000/dashboard/api/override" \
  -H "Content-Type: application/json" \
  -d '{"enabled": true, "target_power_kw": 1.5}'
```

---

## Appendix

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `MINER_DISCOVERY_ENABLED` | `true` | Enable auto-discovery |
| `MINER_NETWORK_CIDR` | `192.168.1.0/24` | Network range to scan |
| `POLL_INTERVAL_SECONDS` | `5` | Status polling interval |
| `SNAPSHOT_INTERVAL_SECONDS` | `60` | Database snapshot interval |
| `SNAPSHOT_RETENTION_HOURS` | `24` | Data retention period |
| `POWER_CONTROL_MODE` | `on_off` | Default power control mode |
| `RATED_POWER_KW` | Auto | Fleet power capacity override |

### Rate Limits

| Endpoint | Recommended Limit |
|----------|------------------|
| GET /api/status | 1 request/second |
| POST /api/activate | 1 request/2 seconds |
| POST /api/deactivate | 1 request/2 seconds |

### Support

For technical support or questions about this API, contact your system administrator.

---

*This documentation was generated on January 20, 2026.*
