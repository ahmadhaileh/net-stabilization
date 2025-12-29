# Vnish Firmware API Reference

API documentation for Antminer S9/T9/L3+ miners running Vnish 3.9.x firmware.

## Authentication

All CGI endpoints use HTTP Digest Authentication:

-   **Username:** `root`
-   **Password:** `root`

## CGI Endpoints

### Read Endpoints

| Endpoint                        | Method | Description                |
| ------------------------------- | ------ | -------------------------- |
| `/cgi-bin/get_miner_status.cgi` | GET    | Full mining statistics     |
| `/cgi-bin/get_miner_conf.cgi`   | GET    | Current configuration      |
| `/cgi-bin/get_system_info.cgi`  | GET    | System information         |
| `/cgi-bin/chip_hr.json`         | GET    | Per-chip hashrate data     |
| `/cgi-bin/get_autofreq_log.cgi` | GET    | Auto-frequency tuning logs |
| `/cgi-bin/get_fs.cgi`           | GET    | Filesystem/security check  |

### Control Endpoints

| Endpoint                             | Method | Description             |
| ------------------------------------ | ------ | ----------------------- |
| `/cgi-bin/stop_bmminer.cgi`          | GET    | Stop mining (idle mode) |
| `/cgi-bin/reboot_cgminer.cgi`        | GET    | Restart mining software |
| `/cgi-bin/reboot.cgi`                | GET    | Full system reboot      |
| `/cgi-bin/do_sleep_mode.cgi`         | POST   | Enter/exit sleep mode   |
| `/cgi-bin/set_miner_conf.cgi`        | POST   | Set basic config        |
| `/cgi-bin/set_miner_conf_custom.cgi` | POST   | Set full config         |

## CGMiner TCP API (Port 4028)

Standard CGMiner JSON-over-TCP protocol:

```json
{"command":"summary"}    // Mining summary stats
{"command":"stats"}      // Detailed statistics
{"command":"pools"}      // Pool configuration
{"command":"devs"}       // Device information
{"command":"config"}     // CGMiner configuration
{"command":"version"}    // Firmware version
```

## Response Examples

### get_system_info.cgi

```json
{
    "minertype": "Antminer S9 (vnish 3.9.0)",
    "hostname": "antMiner",
    "macaddr": "4C:F4:5D:FB:F0:CC",
    "ipaddress": "192.168.1.167",
    "netmask": "255.255.255.0",
    "ant_hwv": "26.0.1.3",
    "system_kernel_version": "Linux 3.14.0-xilinx",
    "file_system_version": "Tue Nov 30 17:18:39 CST 2021",
    "bmminer_version": "4.11.1"
}
```

### chip_hr.json (Per-ASIC Hashrate)

```json
{
    "chiphr": [
        { "Asic00": "69", "Asic01": "67", "Asic02": "67" },
        { "Asic00": "64", "Asic01": "67", "Asic02": "68" },
        { "Asic00": "69", "Asic01": "70", "Asic02": "67" }
    ]
}
```

### Sleep Mode Control

```http
POST /cgi-bin/do_sleep_mode.cgi
Content-Type: application/x-www-form-urlencoded

mode=1    # 1 = sleep, 0 = wake
```

## Configuration Parameters

### Required Parameters for set_miner_conf_custom.cgi

All parameters must be included or config may become corrupted:

```
_ant_pool1url, _ant_pool1user, _ant_pool1pw
_ant_pool2url, _ant_pool2user, _ant_pool2pw
_ant_pool3url, _ant_pool3user, _ant_pool3pw
_ant_freq, _ant_freq1, _ant_freq2, _ant_freq3
_ant_voltage, _ant_voltage1, _ant_voltage2, _ant_voltage3
_ant_fan_customize_switch, _ant_fan_customize_value
_ant_fan_rpm_off (1=immersion mode)
_ant_target_temp, _ant_tempoff
_ant_asicboost
_ant_autodownscale, _ant_autodownscale_step, _ant_autodownscale_min
_ant_nobeeper
```

## Firmware Detection

| Firmware  | Detection Method                                              |
| --------- | ------------------------------------------------------------- |
| Vnish     | `file_system_version` contains "vnish" or chip_hr.json exists |
| BraiinsOS | `/api/bos/info` endpoint exists                               |
| Stock     | Default CGMiner responses only                                |
