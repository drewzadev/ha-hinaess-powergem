# HinaEss Powergem MQTT Bridge

A Python bridge that reads HinaEss Powergem Max LiFePO4 battery data over RS485 (using the PACE/Topband BMS protocol) and publishes it to Home Assistant via MQTT auto-discovery.

## Background

HinaEss Powergem Max batteries use Topband BMS controllers that communicate over an RS485 bus at 9600 baud. Each BMS is assigned an address (0-15) and responds to PACE-protocol commands for analog data, device info, and alarms. This bridge polls one or more BMS units, decodes the binary responses, and pushes structured JSON to an MQTT broker where Home Assistant picks them up automatically.

## Requirements

- Python 3.7+
- RS485 USB adapter wired to the battery BMS daisy-chain
- MQTT broker (e.g. Mosquitto) reachable from the host (this is usually provided by Home Assistant)
- Home Assistant with the MQTT integration enabled

## Installation

```bash
git clone <repo-url> && cd ha-hinaess-powergem
pip3 install -r requirements.txt
```
For Raspbian:
```bash
sudo apt install python3-paho-mqtt
pip3 install pyserial pyyaml
```

Dependencies: `pyserial` (RS485 communication), `paho-mqtt` (MQTT client), and `pyyaml` (config file parsing).

## Quick Start

```bash
# Single poll, terminal output only (no MQTT)
python3 src/hinaess-powergem-monitor.py --port /dev/ttyUSB2

# Single poll + publish to MQTT
python3 src/hinaess-powergem-monitor.py --port /dev/ttyUSB2 --mqtt --mqtt-host 192.168.1.10

# Continuous polling every 5s with MQTT
python3 src/hinaess-powergem-monitor.py --port /dev/ttyUSB2 --mqtt --mqtt-host 192.168.1.10 --loop --interval 5

# Query device info (HW version, serial number, BMS clock) on startup
python3 src/hinaess-powergem-monitor.py --port /dev/ttyUSB2 --mqtt --loop --info
```

After the first `--mqtt` run, entities appear automatically in Home Assistant under **Settings > Devices & Services > MQTT**.

## Configuration File

Most settings can be stored in a `config.yaml` file instead of passing them as CLI flags on every run. Copy the included template and edit it:

```bash
cp config.example.yaml config.yaml
nano config.yaml
```

```yaml
serial:
  port: /dev/ttyUSB2
  bms_count: 1

mqtt:
  host: 192.168.0.10
  port: 1883
  username: my_user
  password: my_secret

intervals:
  poll: 5.0
  realtime: 20.0
  cell: 600.0

homeassistant:
  device_name: "HinaESS Powergem Max"
  device_manufacturer: "HinaESS"
  device_model: "Powergem Max"
  device_area: "Battery Room"

exclude_sensors: []
```

With a config file in place, the command line becomes much simpler:

```bash
# Continuous polling with MQTT — all connection details come from config.yaml
python3 src/hinaess-powergem-monitor.py --mqtt --loop
```

**Precedence:** CLI arguments override config.yaml values, which override built-in defaults. Any option can be set in either place; the CLI always wins.

Use `--config PATH` to load a config file from a non-default location. If `--config` is explicitly set and the file does not exist, the script exits with an error. If the default `config.yaml` is missing, built-in defaults are used silently.

> **Note:** `config.yaml` is gitignored by default since it may contain MQTT credentials. Only `config.example.yaml` is tracked.

## Configuration Reference

### RS485 / Polling Options

| Flag | Default        | Description |
|---|----------------|---|
| `--port PORT` | `/dev/ttyUSB2` | Serial port of the RS485 adapter. Use `ls /dev/ttyUSB*` to find yours. |
| `--bms-count N` | `1`            | Number of BMS units to poll, starting from address 0. Set this to match the number of batteries wired on the RS485 bus. |
| `--scan` | off            | Auto-scan all 16 RS485 addresses and only poll units that respond. Use this instead of `--bms-count` when you're unsure which addresses are active. |
| `--loop` | off            | Poll continuously until Ctrl+C. Without this flag the script runs a single poll and exits. |
| `--interval SECS` | `3.0`          | How often to read the RS485 bus in loop mode. Lower values give faster updates but increase bus traffic. |
| `--realtime-interval SECS` | `20.0`         | Minimum interval between MQTT state publishes. The bus is polled every `--interval` seconds, but state messages are only sent to MQTT at this cadence to reduce broker load. |
| `--cell-interval SECS` | `600.0`        | Minimum interval between cell voltage publishes. Cell data changes slowly, so this defaults to 10 minutes to avoid flooding MQTT with 16+ sensor updates. |
| `--info` | off            | Query and log device info (hardware version, pack serial number, BMS clock) on startup before polling begins. Always runs automatically when MQTT is enabled so device metadata appears in HA. |
| `--debug` | off            | Enable verbose DEBUG-level logging. Shows raw serial TX/RX frames, per-cell voltages, temperatures, and full battery data each poll. Useful for troubleshooting communication issues. |
| `--raw` | off            | Deprecated alias for `--debug`. |
| `--exclude-sensors SLUG...` | none           | Space-separated list of sensor slugs to omit from MQTT discovery and state payloads. See [Sensor Slugs](#sensor-slugs) below. |

### MQTT Options

| Flag | Default | Description |
|---|---|---|
| `--mqtt` | off | Enable MQTT publishing. Without this flag the script only prints to the terminal. |
| `--mqtt-host HOST` | `localhost` | Hostname or IP of your MQTT broker. |
| `--mqtt-port PORT` | `1883` | MQTT broker port. Use `8883` if your broker requires TLS (not currently supported). |
| `--mqtt-user USER` | none | MQTT username. Leave unset if your broker allows anonymous connections. |
| `--mqtt-password PASS` | none | MQTT password. Only used when `--mqtt-user` is set. |
| `--mqtt-prefix PREFIX` | `homeassistant` | The MQTT discovery prefix configured in Home Assistant. Change this only if you've customised `discovery_prefix` in your HA MQTT config. |
| `--mqtt-base TOPIC` | `hinaess` | Root topic for all state, availability, and cell data messages. Each battery publishes under `<base>/battery_N/`. |
| `--delete` | off | Publish empty payloads to all discovery topics to remove entities from Home Assistant, then exit. No RS485 connection needed. |

### Home Assistant Device Options

| Flag | Default | Description |
|---|---|---|
| `--device-name NAME` | `HinaEss Powergem Max` | Device name shown in HA. The battery number is appended (e.g. "HinaEss Powergem Max #1"). |
| `--device-manufacturer MFR` | `HinaEss` | Manufacturer field in the HA device registry. |
| `--device-model MODEL` | `Powergem Max` | Model field in the HA device registry. |
| `--device-area AREA` | `Battery Room` | Suggested area in HA. Home Assistant uses this as a hint when the device is first discovered. |

## MQTT Topic Layout

```
hinaess/
  battery_1/
    availability        "online" | "offline"  (retained)
    state               JSON: soc, voltage, current, power, temps, soh, cycles
    attributes          JSON: remain_ah, full_ah
    cells               JSON: cell voltages, min/max/delta (published at cell-interval)
  battery_2/
    ...
  bridge/
    status              "online" | "offline"  (retained, via MQTT last-will)
```

Discovery config topics:
```
homeassistant/<component>/battery_<N>_<slug>/config
```

## Home Assistant Entities

Each battery creates one device with these entities:

| Entity | Type | Device Class | Notes |
|---|---|---|---|
| Online | Binary sensor | `connectivity` | Tracks RS485 reachability |
| SOC | Sensor | `battery` (%) | Calculated from remain_ah / full_ah |
| Voltage | Sensor | `voltage` (V) | Pack voltage |
| Current | Sensor | `current` (A) | Positive = charging, negative = discharging |
| Power | Sensor | `power` (W) | Derived from voltage x current |
| Temp T1-T4, MOS, ENV | Sensor | `temperature` (C) | Up to 6 temperature probes per BMS |
| SOH | Sensor | - (%) | State of health reported by BMS |
| Cycle Count | Sensor | - (total_increasing) | Charge cycle counter |
| Cell Delta | Sensor | `voltage` (mV) | Spread between highest and lowest cell |
| Cell 1-16 | Sensor | `voltage` (mV) | Individual cell voltages (published at cell-interval) |
| Cell Min / Cell Max | Sensor | `voltage` (mV) | Lowest and highest cell voltage |
| Charging | Binary sensor | `battery_charging` | ON when current > 0.5 A |

### Sensor Slugs

Use these with `--exclude-sensors` to suppress specific entities:

`online`, `soc`, `voltage`, `current`, `power`, `soh`, `cycles`, `cell_delta`, `cell_01_v` ... `cell_16_v`, `cell_min_v`, `cell_max_v`, `temp_t1` ... `temp_t4`, `temp_mos`, `temp_env`, `charging`

Example: `--exclude-sensors temp_env cell_delta` omits the ENV temperature and cell delta sensors.

## Removing Devices from Home Assistant

```bash
python3 src/hinaess-powergem-monitor.py --delete --bms-count 2 --mqtt-host 192.168.1.10
```

This publishes empty retained payloads to all discovery topics, which tells Home Assistant to remove the entities. No RS485 connection is required.

## Running as a Service

```ini
# /etc/systemd/system/ha-hinaess-powergem.service
[Unit]
Description=HinaEss Powergem MQTT Bridge
After=network.target

[Service]
ExecStart=/usr/bin/python3 /opt/ha-hinaess-powergem/src/hinaess-powergem-monitor.py --mqtt --loop
WorkingDirectory=/opt/ha-hinaess-powergem
Restart=on-failure
RestartSec=10

[Install]
WantedBy=multi-user.target

```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now hinaess-bridge
sudo journalctl -fu hinaess-bridge
```

## Troubleshooting

**Serial port permission denied**
```bash
sudo chmod 666 /dev/ttyUSB2
# or permanently:
sudo usermod -aG dialout $USER
```

**No BMS response** — Use `--scan --debug` to see which addresses respond and inspect raw frames:
```bash
python3 src/hinaess-powergem-monitor.py --port /dev/ttyUSB2 --scan --debug
```

**Entities not appearing in HA** — Verify the MQTT integration is enabled, the discovery prefix matches (`homeassistant` by default), and messages are reaching the broker:
```bash
mosquitto_sub -v -t 'homeassistant/#' -t 'hinaess/#'
```

**paho-mqtt not found** — `pip3 install paho-mqtt`
