"""MQTT client, discovery payloads, and publish helpers for Home Assistant."""

import json
import logging
import sys
import time

import paho.mqtt.client as mqtt

from src.config import TEMP_LABELS

logger = logging.getLogger(__name__)


# ── Topic Helpers ─────────────────────────────────────────────────────────────

def avail_topic(base: str, addr: int) -> str:
    """Availability topic for a 1-based battery address."""
    return f"{base}/battery_{addr}/availability"


def state_topic(base: str, addr: int) -> str:
    """State (JSON) topic for a 1-based battery address."""
    return f"{base}/battery_{addr}/state"


def attrs_topic(base: str, addr: int) -> str:
    """Attributes (JSON) topic for a 1-based battery address."""
    return f"{base}/battery_{addr}/attributes"


def cells_topic(base: str, addr: int) -> str:
    """Cell voltage (JSON) topic for a 1-based battery address."""
    return f"{base}/battery_{addr}/cells"


def discovery_topic(prefix: str, component: str, addr: int, slug: str) -> str:
    """HA MQTT discovery topic for a given entity."""
    return f"{prefix}/{component}/battery_{addr}_{slug}/config"


# ── Discovery Payloads ────────────────────────────────────────────────────────

def build_discovery_payloads(addr: int, device_info: dict, cfg) -> list:
    """
    Build all MQTT discovery payloads for a single battery.

    addr        -- 1-based MQTT battery number
    device_info -- dict from query_device_info() (may be empty for --delete)
    cfg         -- argparse namespace with mqtt_* attributes

    Returns a list of (topic, payload_dict) tuples.
    """
    base   = cfg.mqtt_base
    prefix = cfg.mqtt_prefix
    avail  = avail_topic(base, addr)
    state  = state_topic(base, addr)
    attrs  = attrs_topic(base, addr)
    cells  = cells_topic(base, addr)

    excluded = getattr(cfg, "exclude_sensors", set())

    identifiers = [f"hinaess_battery_{addr}"]
    pack_sn = device_info.get("pack_sn")
    if pack_sn:
        identifiers.append(pack_sn)

    device_full = {
        "identifiers": identifiers,
        "name": f"{cfg.device_name} #{addr}",
        "manufacturer": cfg.device_manufacturer,
        "model": cfg.device_model,
        "suggested_area": cfg.device_area,
    }
    if device_info.get("hw_version"):
        device_full["hw_version"] = device_info["hw_version"]
    if pack_sn:
        device_full["serial_number"] = pack_sn

    device_ref = {"identifiers": identifiers}

    avail_fields = {
        "availability_topic": avail,
        "payload_available": "online",
        "payload_not_available": "offline",
    }

    payloads = []

    # ── Online binary_sensor (full device block on first entity) ──
    if "online" not in excluded:
        payloads.append((
            discovery_topic(prefix, "binary_sensor", addr, "online"),
            {
                "name": "Online",
                "unique_id": f"hinaess_battery_{addr}_online",
                "device_class": "connectivity",
                "state_topic": avail,
                "payload_on": "online",
                "payload_off": "offline",
                **avail_fields,
                "device": device_full,
            }
        ))

    def _sensor(slug, name, device_class, state_class, unit, value_field,
                icon=None, json_attrs=False, state_topic_override=None):
        if slug in excluded:
            return None
        p = {
            "name": name,
            "unique_id": f"hinaess_battery_{addr}_{slug}",
            "state_topic": state_topic_override or state,
            "value_template": f"{{{{ value_json.{value_field} }}}}",
            **avail_fields,
            "device": device_ref,
        }
        if device_class:
            p["device_class"] = device_class
        if state_class:
            p["state_class"] = state_class
        if unit:
            p["unit_of_measurement"] = unit
        if icon:
            p["icon"] = icon
        if json_attrs:
            p["json_attributes_topic"] = attrs
        return (discovery_topic(prefix, "sensor", addr, slug), p)

    # ── Scalar sensors ──
    for entry in [
        _sensor("soc",        "SOC",         "battery",     "measurement",      "%",   "soc_pct",      json_attrs=True),
        _sensor("voltage",    "Voltage",     "voltage",     "measurement",      "V",   "voltage_v"),
        _sensor("current",    "Current",     "current",     "measurement",      "A",   "current_a"),
        _sensor("power",      "Power",       "power",       "measurement",      "W",   "power_w"),
        _sensor("soh",        "SOH",         None,          "measurement",      "%",   "soh_pct",      icon="mdi:battery-heart"),
        _sensor("cycles",     "Cycle Count", None,          "total_increasing", None,  "cycle_count",  icon="mdi:counter"),
        _sensor("cell_delta", "Cell Delta",  "voltage",     "measurement",      "mV",  "cell_diff_mv", icon="mdi:approximately-equal", state_topic_override=cells),
    ]:
        if entry is not None:
            payloads.append(entry)

    # ── Cell voltage sensors (published on cells topic at cell_interval) ──
    for i in range(1, 17):
        slug = f"cell_{i:02d}_v"
        entry = _sensor(
            slug, f"Cell {i}",
            "voltage", "measurement", "mV",
            slug,
            icon="mdi:flash-triangle",
            state_topic_override=cells,
        )
        if entry is not None:
            payloads.append(entry)

    entry = _sensor(
        "cell_min_v", "Cell Min",
        "voltage", "measurement", "mV",
        "min_cell_v",
        icon="mdi:arrow-down-bold",
        state_topic_override=cells,
    )
    if entry is not None:
        payloads.append(entry)

    entry = _sensor(
        "cell_max_v", "Cell Max",
        "voltage", "measurement", "mV",
        "max_cell_v",
        icon="mdi:arrow-up-bold",
        state_topic_override=cells,
    )
    if entry is not None:
        payloads.append(entry)

    # ── Temperature sensors ──
    for label in TEMP_LABELS:
        slug = f"temp_{label.lower()}"
        entry = _sensor(
            slug, f"Temp {label}",
            "temperature", "measurement", "\u00b0C",
            f"temps.{label}",
        )
        if entry is not None:
            payloads.append(entry)

    # ── Charging binary_sensor ──
    if "charging" not in excluded:
        payloads.append((
            discovery_topic(prefix, "binary_sensor", addr, "charging"),
            {
                "name": "Charging",
                "unique_id": f"hinaess_battery_{addr}_charging",
                "device_class": "battery_charging",
                "state_topic": state,
                "value_template": "{{ 'ON' if value_json.current_a > 0.5 else 'OFF' }}",
                "payload_on": "ON",
                "payload_off": "OFF",
                **avail_fields,
                "device": device_ref,
            }
        ))

    return payloads


# ── State / Attributes ────────────────────────────────────────────────────────

def build_state_payload(data: dict, excluded: set = ()) -> dict:
    """Convert an analog result dict to the MQTT state JSON dict."""
    keys = [
        "soc_pct", "voltage_v", "current_a", "power_w",
        "remain_ah", "full_ah", "soh_pct", "cycle_count",
    ]
    payload = {k: data[k] for k in keys if k in data and k not in excluded}
    if "temps" in data:
        temps = {}
        for t in data["temps"]:
            slug = f"temp_{t['label'].lower()}"
            if slug not in excluded:
                temps[t["label"]] = t["celsius"]
        if temps:
            payload["temps"] = temps
    return payload


def build_attrs_payload(data: dict) -> dict:
    """Extract supplementary fields published to the attributes topic."""
    keys = ["remain_ah", "full_ah"]
    return {k: data[k] for k in keys if k in data}


def build_cell_state_payload(data: dict, excluded: set = ()) -> dict:
    """Build the cell voltage MQTT payload (volts -> mV conversion)."""
    payload = {}
    for i, v in enumerate(data.get("cells_v", []), start=1):
        slug = f"cell_{i:02d}_v"
        if slug not in excluded:
            payload[slug] = round(v * 1000, 1)
    for k in ("min_cell_v", "max_cell_v"):
        if k in data and f"cell_{k.split('_')[0]}_{k.split('_')[1]}_v" not in excluded:
            payload[k] = round(data[k] * 1000, 1)
    for k in ("min_cell_idx", "max_cell_idx", "cell_diff_mv"):
        if k in data:
            payload[k] = data[k]
    return payload


# ── Publish Helpers ───────────────────────────────────────────────────────────

def publish_availability(client, addr: int, status: str, cfg):
    """Publish 'online' or 'offline' to a battery's availability topic (retained)."""
    client.publish(avail_topic(cfg.mqtt_base, addr), status,
                   qos=cfg.mqtt_qos, retain=True)


def publish_discovery(client, addrs: list, device_infos: dict, cfg):
    """Publish all MQTT discovery messages for every battery (retained)."""
    for addr in addrs:
        info = device_infos.get(addr, {})
        for topic, payload in build_discovery_payloads(addr, info, cfg):
            client.publish(topic, json.dumps(payload),
                           qos=cfg.mqtt_qos, retain=True)
    logger.info("MQTT discovery published for %d battery/batteries", len(addrs))


def publish_state(client, addr: int, data: dict, cfg):
    """Publish state JSON and attributes JSON for one battery (not retained)."""
    excluded = getattr(cfg, "exclude_sensors", set())
    client.publish(state_topic(cfg.mqtt_base, addr),
                   json.dumps(build_state_payload(data, excluded)),
                   qos=cfg.mqtt_qos, retain=False)
    client.publish(attrs_topic(cfg.mqtt_base, addr),
                   json.dumps(build_attrs_payload(data)),
                   qos=cfg.mqtt_qos, retain=False)


def publish_cell_state(client, addr: int, data: dict, cfg):
    """Publish cell voltage JSON for one battery (not retained)."""
    excluded = getattr(cfg, "exclude_sensors", set())
    client.publish(cells_topic(cfg.mqtt_base, addr),
                   json.dumps(build_cell_state_payload(data, excluded)),
                   qos=cfg.mqtt_qos, retain=False)


def delete_devices(client, addrs: list, cfg):
    """
    Remove all HA entities by publishing empty payloads to discovery topics.
    Also publishes 'offline' to availability topics.
    """
    for addr in addrs:
        for topic, _ in build_discovery_payloads(addr, {}, cfg):
            client.publish(topic, b"", qos=cfg.mqtt_qos, retain=True)
        publish_availability(client, addr, "offline", cfg)
    time.sleep(1.5)  # allow retained messages to propagate


# ── MQTT Client Setup ────────────────────────────────────────────────────────

def mqtt_connect(cfg, addrs: list, device_infos: dict):
    """
    Create, configure, and connect an MQTT client.

    Sets up on_connect / on_message callbacks so that:
    - On connect: publishes 'online' availability + discovery for all batteries
    - On homeassistant/status == 'online': re-publishes discovery (HA restart)
    """
    bridge_status_topic = f"{cfg.mqtt_base}/bridge/status"

    client = mqtt.Client(client_id="hinaess-bridge", clean_session=True)

    if cfg.mqtt_user:
        client.username_pw_set(cfg.mqtt_user, cfg.mqtt_password)

    client.will_set(bridge_status_topic, "offline", qos=cfg.mqtt_qos, retain=True)

    def on_connect(c, userdata, flags, rc):
        if rc != 0:
            logger.error("MQTT connection failed (rc=%d). Check host/port/credentials.", rc)
            return
        logger.info("MQTT connected to %s:%d", cfg.mqtt_host, cfg.mqtt_port)
        c.publish(bridge_status_topic, "online", qos=cfg.mqtt_qos, retain=True)
        for addr in addrs:
            publish_availability(c, addr, "online", cfg)
        publish_discovery(c, addrs, device_infos, cfg)
        c.subscribe("homeassistant/status", qos=cfg.mqtt_qos)

    def on_message(c, userdata, msg):
        payload = msg.payload.decode(errors="replace").strip()
        if msg.topic == "homeassistant/status" and payload == "online":
            logger.info("Home Assistant restarted - re-publishing discovery")
            publish_discovery(c, addrs, device_infos, cfg)

    def on_disconnect(c, userdata, rc):
        if rc != 0:
            logger.warning("MQTT unexpected disconnect (rc=%d)", rc)

    client.on_connect = on_connect
    client.on_message = on_message
    client.on_disconnect = on_disconnect

    try:
        client.connect(cfg.mqtt_host, cfg.mqtt_port, keepalive=60)
    except Exception as e:
        logger.error("MQTT could not connect to %s:%d: %s", cfg.mqtt_host, cfg.mqtt_port, e)
        sys.exit(1)

    return client
