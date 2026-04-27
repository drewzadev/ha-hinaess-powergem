"""Configuration constants, YAML loading, and CLI argument parsing."""

import argparse
import logging
import os
import sys

import yaml


logger = logging.getLogger(__name__)


# ── Sentinel for unset CLI args ──────────────────────────────────────────────

_UNSET = object()


# ── Protocol Constants ───────────────────────────────────────────────────────

BAUD_RATE       = 9600
VER             = 0x21        # Protocol version
CID1            = 0x46        # Command group (battery analog data)
CID2_GET_ANALOG = 0x42        # Get analog/battery values
CID2_GET_ALARM  = 0x44        # Get alarm info
CID2_GET_DATE   = 0x4D        # Get BMS date/time
CID2_GET_PROTO  = 0x4F        # Get protocol version (used as wake-up/ping)
CID2_GET_MFGR   = 0x51        # Get manufacturer info (HW version + Pack SN)

RTN_OK          = 0x00        # CID2 return code: success


# ── MQTT Configuration ───────────────────────────────────────────────────────

MQTT_HOST             = "localhost"
MQTT_PORT             = 1883
MQTT_USERNAME         = None
MQTT_PASSWORD         = None
MQTT_DISCOVERY_PREFIX = "homeassistant"
MQTT_BASE_TOPIC       = "hinaess"
MQTT_QOS              = 1


# ── Home Assistant Device Info ───────────────────────────────────────────────

DEVICE_NAME          = "HinaEss Powergem Max"
DEVICE_MANUFACTURER  = "HinaEss"
DEVICE_MODEL         = "Powergem Max"
DEVICE_AREA          = "Battery Room"


# ── Sensor Labels ────────────────────────────────────────────────────────────

TEMP_LABELS = ["T1", "T2", "T3", "T4", "MOS", "ENV"]


# ── Polling & Publish Intervals ─────────────────────────────────────────────

SERIAL_PORT        = "/dev/ttyUSB2"
POLL_INTERVAL      = 3.0
REALTIME_INTERVAL  = 20.0
LONGTERM_INTERVAL  = 600.0

EXCLUDED_SENSORS: list[str] = []


# ── Hardcoded defaults for YAML-backed options ──────────────────────────────

HARDCODED_DEFAULTS = {
    "port":                 SERIAL_PORT,
    "bms_count":            2,
    "mqtt_host":            MQTT_HOST,
    "mqtt_port":            MQTT_PORT,
    "mqtt_user":            MQTT_USERNAME,
    "mqtt_password":        MQTT_PASSWORD,
    "mqtt_prefix":          MQTT_DISCOVERY_PREFIX,
    "mqtt_base":            MQTT_BASE_TOPIC,
    "interval":             POLL_INTERVAL,
    "realtime_interval":    REALTIME_INTERVAL,
    "cell_interval":        LONGTERM_INTERVAL,
    "device_name":          DEVICE_NAME,
    "device_manufacturer":  DEVICE_MANUFACTURER,
    "device_model":         DEVICE_MODEL,
    "device_area":          DEVICE_AREA,
    "exclude_sensors":      EXCLUDED_SENSORS,
}

# Mapping from nested YAML keys to flat argparse attribute names
YAML_KEY_MAP = {
    ("serial", "port"):                     "port",
    ("serial", "bms_count"):                "bms_count",
    ("mqtt", "host"):                       "mqtt_host",
    ("mqtt", "port"):                       "mqtt_port",
    ("mqtt", "username"):                   "mqtt_user",
    ("mqtt", "password"):                   "mqtt_password",
    ("mqtt", "discovery_prefix"):           "mqtt_prefix",
    ("mqtt", "base_topic"):                 "mqtt_base",
    ("intervals", "poll"):                  "interval",
    ("intervals", "realtime"):              "realtime_interval",
    ("intervals", "cell"):                  "cell_interval",
    ("homeassistant", "device_name"):       "device_name",
    ("homeassistant", "device_manufacturer"): "device_manufacturer",
    ("homeassistant", "device_model"):      "device_model",
    ("homeassistant", "device_area"):       "device_area",
    ("exclude_sensors",):                   "exclude_sensors",
}


# ── YAML Config Loading ─────────────────────────────────────────────────────

def load_config(path: str) -> dict:
    """Load YAML config and flatten to argparse-compatible key-value pairs."""
    if not os.path.isfile(path):
        return {}
    try:
        with open(path, "r") as f:
            raw = yaml.safe_load(f) or {}
    except yaml.YAMLError as e:
        logger.error("Failed to parse config file %s: %s", path, e)
        sys.exit(1)

    flat = {}
    for yaml_keys, arg_name in YAML_KEY_MAP.items():
        value = raw
        for k in yaml_keys:
            if isinstance(value, dict):
                value = value.get(k, _UNSET)
            else:
                value = _UNSET
                break
        if value is not _UNSET:
            flat[arg_name] = value
    return flat


# ── CLI Argument Parsing ─────────────────────────────────────────────────────

def parse_args() -> argparse.Namespace:
    """Parse command-line arguments, merge with YAML config, and return a config namespace."""
    parser = argparse.ArgumentParser(
        description="Query Topband BMS batteries over RS485"
    )
    parser.add_argument(
        "--config", default="config.yaml",
        help="Path to YAML config file (default: config.yaml)"
    )
    parser.add_argument(
        "--port", default=_UNSET,
        help=f"Serial port (default: {SERIAL_PORT})"
    )
    parser.add_argument(
        "--bms-count", type=int, default=_UNSET,
        help="Number of BMS units to poll (default: 2)"
    )
    parser.add_argument(
        "--scan", action="store_true",
        help="Scan all 16 addresses, only keep valid responders"
    )
    parser.add_argument(
        "--debug", action="store_true",
        help="Enable verbose DEBUG-level logging (includes raw serial frames)"
    )
    parser.add_argument(
        "--raw", action="store_true",
        help="(Deprecated) Alias for --debug"
    )
    parser.add_argument(
        "--loop", action="store_true",
        help="Continuously poll"
    )
    parser.add_argument(
        "--info", action="store_true",
        help="Query device info (HW version, Pack SN, BMS clock)"
    )
    parser.add_argument(
        "--interval", type=float, default=_UNSET,
        help=f"Poll interval in seconds (default: {POLL_INTERVAL})"
    )
    parser.add_argument(
        "--realtime-interval", type=float, default=_UNSET,
        metavar="SECS",
        help=f"Publish interval for realtime sensors in seconds (default: {REALTIME_INTERVAL})"
    )
    parser.add_argument(
        "--exclude-sensors", nargs="*", default=_UNSET,
        metavar="SLUG",
        help="Sensor slugs to exclude from MQTT discovery and state payloads"
    )

    # ── MQTT arguments ──
    mqtt_group = parser.add_argument_group(
        "MQTT",
        "Options for publishing to Home Assistant via MQTT."
    )
    mqtt_group.add_argument(
        "--mqtt", action="store_true",
        help="Enable MQTT publishing to Home Assistant"
    )
    mqtt_group.add_argument(
        "--mqtt-host", default=_UNSET, metavar="HOST",
        help=f"MQTT broker hostname or IP (default: {MQTT_HOST})"
    )
    mqtt_group.add_argument(
        "--mqtt-port", type=int, default=_UNSET, metavar="PORT",
        help=f"MQTT broker port (default: {MQTT_PORT})"
    )
    mqtt_group.add_argument(
        "--mqtt-user", default=_UNSET, metavar="USER",
        help="MQTT username (default: none)"
    )
    mqtt_group.add_argument(
        "--mqtt-password", default=_UNSET, metavar="PASS",
        help="MQTT password (default: none)"
    )
    mqtt_group.add_argument(
        "--mqtt-prefix", default=_UNSET, metavar="PREFIX",
        help=f"HA MQTT discovery prefix (default: {MQTT_DISCOVERY_PREFIX})"
    )
    mqtt_group.add_argument(
        "--mqtt-base", default=_UNSET, metavar="TOPIC",
        help=f"Base MQTT topic for state/availability (default: {MQTT_BASE_TOPIC})"
    )
    mqtt_group.add_argument(
        "--delete", action="store_true",
        help="Remove device(s) from Home Assistant via MQTT and exit (no RS485 needed)"
    )
    mqtt_group.add_argument(
        "--cell-interval", type=float, default=_UNSET, metavar="SECS",
        help=f"Publish interval for cell voltage data in seconds (default: {LONGTERM_INTERVAL})"
    )
    mqtt_group.add_argument(
        "--device-name", default=_UNSET, metavar="NAME",
        help=f"HA device name prefix, address appended (default: {DEVICE_NAME})"
    )
    mqtt_group.add_argument(
        "--device-manufacturer", default=_UNSET, metavar="MFR",
        help=f"HA device manufacturer (default: {DEVICE_MANUFACTURER})"
    )
    mqtt_group.add_argument(
        "--device-model", default=_UNSET, metavar="MODEL",
        help=f"HA device model (default: {DEVICE_MODEL})"
    )
    mqtt_group.add_argument(
        "--device-area", default=_UNSET, metavar="AREA",
        help=f"HA suggested area (default: {DEVICE_AREA})"
    )

    args = parser.parse_args()

    # ── Load YAML config and merge ──
    config_explicitly_set = args.config != "config.yaml"
    yaml_values = load_config(args.config)

    if yaml_values:
        logger.info("Loaded configuration from %s", args.config)
    elif config_explicitly_set:
        logger.error("Config file not found: %s", args.config)
        sys.exit(1)

    # Merge: CLI > YAML > hardcoded default
    for key, hardcoded in HARDCODED_DEFAULTS.items():
        cli_value = getattr(args, key)
        if cli_value is _UNSET:
            setattr(args, key, yaml_values.get(key, hardcoded))

    # --raw is a deprecated alias for --debug
    if args.raw:
        args.debug = True

    # Attach QoS so helper functions can use cfg.mqtt_qos uniformly
    args.mqtt_qos = MQTT_QOS

    # Normalise exclude_sensors to a set for fast lookup
    args.exclude_sensors = set(args.exclude_sensors or [])

    return args
