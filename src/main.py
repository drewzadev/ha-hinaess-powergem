"""Main entry point — startup, poll loop, and shutdown orchestration."""

import logging
import sys
import time

import serial

from src.config import parse_args, BAUD_RATE, RTN_OK
from src.utils import print_device_info, print_battery_data, print_system_summary
from src.serial_reader import (
    build_get_analog_cmd, build_get_protocol_version_cmd,
    open_serial, send_command, flush_bus, parse_frame,
    parse_analog_response, query_device_info,
)

try:
    from src.mqtt_client import (
        mqtt_connect, publish_availability, publish_discovery,
        publish_state, publish_cell_state, delete_devices,
    )
    import paho.mqtt.client as mqtt
    MQTT_AVAILABLE = True
except ImportError:
    MQTT_AVAILABLE = False

logger = logging.getLogger(__name__)


def configure_logging(debug: bool) -> None:
    """Configure the root logger."""
    logging.basicConfig(
        level=logging.DEBUG if debug else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


def main():
    cfg = parse_args()
    configure_logging(cfg.debug)

    # ── Command builder self-test ──
    expected = "~21004642E00200FD36\r"
    actual = build_get_analog_cmd(0).decode('ascii')
    if actual == expected:
        logger.debug("Command builder verified")
    else:
        logger.warning("Command mismatch: %r != %r", actual, expected)

    mqtt_enabled = cfg.mqtt or cfg.delete

    if mqtt_enabled and not MQTT_AVAILABLE:
        logger.error("paho-mqtt is not installed. Run: pip3 install paho-mqtt")
        sys.exit(1)

    # ── --delete flow: MQTT only, no RS485 required ──
    if cfg.delete:
        mqtt_addrs = list(range(1, cfg.bms_count + 1))
        logger.info("Deleting %d battery device(s) from Home Assistant...", len(mqtt_addrs))
        logger.info("Broker: %s:%d", cfg.mqtt_host, cfg.mqtt_port)

        del_client = mqtt.Client(client_id="hinaess-bridge-delete", clean_session=True)
        if cfg.mqtt_user:
            del_client.username_pw_set(cfg.mqtt_user, cfg.mqtt_password)
        try:
            del_client.connect(cfg.mqtt_host, cfg.mqtt_port, keepalive=30)
        except Exception as e:
            logger.error("Could not connect to MQTT broker: %s", e)
            sys.exit(1)
        del_client.loop_start()
        time.sleep(0.5)

        delete_devices(del_client, mqtt_addrs, cfg)

        del_client.loop_stop()
        del_client.disconnect()
        logger.info("Done. Entities removed from Home Assistant.")
        return

    # ── Open serial ──
    logger.info("Serial port opened: %s at %d baud", cfg.port, BAUD_RATE)
    try:
        ser = open_serial(cfg.port)
    except serial.SerialException as e:
        logger.error("Could not open %s: %s", cfg.port, e)
        logger.error("Troubleshooting: ls -la %s / sudo chmod 666 %s / sudo usermod -aG dialout $USER",
                      cfg.port, cfg.port)
        sys.exit(1)

    # ── Wake-up pass ──
    logger.info("Waking BMS...")
    wake_range = range(16) if cfg.scan else range(cfg.bms_count)
    for addr in wake_range:
        cmd = build_get_protocol_version_cmd(addr)
        send_command(ser, cmd, timeout=0.5)
        time.sleep(0.1)
    time.sleep(0.5)

    # ── Scan or use defaults ──
    if cfg.scan:
        logger.debug("Scanning addresses 0-15 for valid BMS units...")
        found = []
        for addr in range(16):
            cmd = build_get_analog_cmd(addr)
            resp = send_command(ser, cmd, timeout=1.0)
            frame = parse_frame(resp)
            if (frame.get("valid")
                    and frame.get("cid2") == RTN_OK
                    and len(frame.get("info_bytes", b'')) > 20):
                info_len = len(frame['info_bytes'])
                logger.debug("BMS #%d: VALID (%d data bytes)", addr, info_len)
                found.append(addr)
            elif len(resp) > 10:
                logger.debug("BMS #%d: responded but not valid analog data", addr)
            else:
                logger.debug("BMS #%d: no response", addr)
            time.sleep(0.2)

        if not found:
            logger.error("No valid BMS units found! Check wiring.")
            ser.close()
            sys.exit(1)
        logger.info("Found %d BMS unit(s) at addresses: %s", len(found), found)
        bms_addrs = found
    else:
        bms_addrs = list(range(cfg.bms_count))
        logger.debug("Polling BMS addresses: %s", bms_addrs)

    # ── Query device info (always when MQTT enabled; optional otherwise) ──
    device_infos = {}  # keyed by 1-based MQTT addr
    if mqtt_enabled or cfg.info:
        logger.debug("Querying device info...")
        for rs485_addr in bms_addrs:
            info = query_device_info(ser, rs485_addr)
            mqtt_addr = rs485_addr + 1
            device_infos[mqtt_addr] = info
            if cfg.info:
                print_device_info(info)

            # Log device info summary at INFO level
            hw = info.get("hw_version", "?")
            fw = info.get("fw_hint", "?")
            sn = info.get("pack_sn", "?")
            logger.info("BMS #%d: HW=%s FW=%s SN=%s", rs485_addr, hw, fw, sn)
        flush_bus(ser, delay=0.5)

    # ── Connect MQTT and start background loop ──
    mqtt_client_inst = None
    if mqtt_enabled:
        mqtt_addrs = [a + 1 for a in bms_addrs]
        logger.info("Connecting to MQTT broker at %s:%d...", cfg.mqtt_host, cfg.mqtt_port)
        mqtt_client_inst = mqtt_connect(cfg, mqtt_addrs, device_infos)
        mqtt_client_inst.loop_start()
        time.sleep(0.8)  # allow on_connect to fire and discovery to publish

    # ── Poll ──
    last_realtime_publish = [0]   # 0 → first poll publishes immediately
    last_longterm_publish = [0]

    def poll_once():
        now = time.time()
        logger.debug("Poll at %s", time.strftime('%Y-%m-%d %H:%M:%S'))

        results = []
        for rs485_addr in bms_addrs:
            mqtt_addr = rs485_addr + 1
            cmd = build_get_analog_cmd(rs485_addr)

            # Try up to 2 attempts per BMS (retry on parse failure)
            result = None
            for attempt in range(2):
                if attempt > 0:
                    flush_bus(ser, delay=0.3)

                resp = send_command(ser, cmd)

                if not resp or len(resp) < 16:
                    if attempt == 0:
                        continue  # retry
                    logger.warning("BMS #%d: No response", rs485_addr)
                    if mqtt_client_inst:
                        publish_availability(mqtt_client_inst, mqtt_addr, "offline", cfg)
                    break

                result = parse_analog_response(resp, rs485_addr)
                if "error" not in result:
                    break  # success
                elif attempt == 0:
                    flush_bus(ser, delay=0.3)
                    result = None  # clear and retry

            if result and "error" not in result:
                print_battery_data(result)
                results.append(result)
                if mqtt_client_inst and now - last_realtime_publish[0] >= cfg.realtime_interval:
                    publish_availability(mqtt_client_inst, mqtt_addr, "online", cfg)
                    publish_state(mqtt_client_inst, mqtt_addr, result, cfg)
            elif result and "error" in result:
                print_battery_data(result)

            time.sleep(0.2)

        # Update realtime timer if we published
        if mqtt_client_inst and results and now - last_realtime_publish[0] >= cfg.realtime_interval:
            last_realtime_publish[0] = now

        if len(results) > 1:
            print_system_summary(results)

        # ── Conditionally publish cell voltages ──
        if mqtt_client_inst and results and now - last_longterm_publish[0] >= cfg.cell_interval:
            for r in results:
                publish_cell_state(mqtt_client_inst, r["bms_addr"] + 1, r, cfg)
            last_longterm_publish[0] = now
            logger.debug("Published cell data (%d batteries)", len(results))

    try:
        if cfg.loop:
            logger.info("Continuous polling every %.1fs (Ctrl+C to stop)...", cfg.interval)
            while True:
                poll_once()
                time.sleep(cfg.interval)
        else:
            poll_once()
    except KeyboardInterrupt:
        logger.info("Stopped.")
    finally:
        if mqtt_client_inst:
            mqtt_addrs = [a + 1 for a in bms_addrs]
            logger.info("Shutting down - publishing offline status")
            for addr in mqtt_addrs:
                publish_availability(mqtt_client_inst, addr, "offline", cfg)
            time.sleep(0.5)
            mqtt_client_inst.loop_stop()
            mqtt_client_inst.disconnect()
        ser.close()
        logger.info("Serial port closed.")


if __name__ == "__main__":
    main()
