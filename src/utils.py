"""Byte helpers and battery data display utilities."""

import logging

logger = logging.getLogger(__name__)


# ── Byte Helpers ──────────────────────────────────────────────────────────────

def get_u16(data: bytes, offset: int) -> int:
    """Extract unsigned 16-bit big-endian integer."""
    return (data[offset] << 8) | data[offset + 1]


def get_s16(data: bytes, offset: int) -> int:
    """Extract signed 16-bit big-endian integer."""
    val = get_u16(data, offset)
    return val - 65536 if val > 32767 else val


# ── Display Functions (DEBUG level) ───────────────────────────────────────────

def print_device_info(info: dict):
    """Log device info at DEBUG level."""
    addr = info.get("bms_addr", "?")
    lines = [f"BMS #{addr} - Device Info"]

    if "hw_version" in info:
        lines.append(f"  HW Version:    {info['hw_version']}")
    if "fw_hint" in info:
        lines.append(f"  FW Hint:       {info['fw_hint']}")
    if "pack_sn" in info:
        lines.append(f"  Pack SN:       {info['pack_sn']}")
    if "bms_datetime" in info:
        lines.append(f"  BMS Clock:     {info['bms_datetime']}")
    if "raw_info" in info and "hw_version" not in info:
        lines.append(f"  Raw Info:      {info['raw_info']}")
    if "error" in info:
        lines.append(f"  Error:         {info['error']}")

    logger.debug("\n".join(lines))


def print_battery_data(data: dict):
    """Log battery data at DEBUG level."""
    if "error" in data:
        logger.debug("BMS #%s: ERROR - %s", data.get("bms_addr", "?"), data["error"])
        if "raw" in data:
            logger.debug("  Raw: %s", data["raw"])
        return

    addr = data.get("bms_addr", "?")
    lines = [f"BMS #{addr} - Battery Data"]

    if "voltage_v" in data:
        status = "IDLE"
        if data["current_a"] > 0.5:
            status = "CHARGING"
        elif data["current_a"] < -0.5:
            status = "DISCHARGING"

        lines.append(f"  Status:          {status}")
        lines.append(f"  Pack Voltage:    {data['voltage_v']:.2f} V")
        lines.append(f"  Current:         {data['current_a']:+.2f} A")
        lines.append(f"  Power:           {data['power_w']:.1f} W")

        if "soc_pct" in data:
            lines.append(f"  SOC:             {data['soc_pct']:.1f} %")

        lines.append(f"  Remaining:       {data['remain_ah']:.2f} Ah")
        if "full_ah" in data:
            lines.append(f"  Full Capacity:   {data['full_ah']:.2f} Ah")
        if "soh_pct" in data:
            lines.append(f"  SOH:             {data['soh_pct']} %")
        if "cycle_count" in data:
            lines.append(f"  Cycle Count:     {data['cycle_count']}")

    if "cells_v" in data:
        lines.append(
            f"  Cells ({data['cell_count']}):  "
            f"min={data['min_cell_v']:.3f}V (#{data['min_cell_idx']})  "
            f"max={data['max_cell_v']:.3f}V (#{data['max_cell_idx']})  "
            f"diff={data['cell_diff_mv']:.1f}mV"
        )
        row = "    "
        for i, v in enumerate(data["cells_v"]):
            tag = ""
            if i + 1 == data["min_cell_idx"]:
                tag = " min"
            elif i + 1 == data["max_cell_idx"]:
                tag = " max"
            row += f"C{i+1:02d}={v:.3f}V{tag}  "
            if (i + 1) % 8 == 0:
                lines.append(row)
                row = "    "
        if row.strip():
            lines.append(row)

    if "temps" in data:
        lines.append("  Temperatures:")
        for t in data["temps"]:
            val = f"{t['celsius']:.1f}C" if t["celsius"] is not None else "N/A"
            lines.append(f"    {t['label']:>4s}: {val}")

    logger.debug("\n".join(lines))


def print_system_summary(results: list):
    """Log multi-battery system summary at DEBUG level."""
    valid = [r for r in results if "voltage_v" in r]
    if not valid:
        return

    total_current = sum(r["current_a"] for r in valid)
    avg_voltage = sum(r["voltage_v"] for r in valid) / len(valid)
    total_power = total_current * avg_voltage
    total_remain = sum(r["remain_ah"] for r in valid)
    total_full = sum(r.get("full_ah", 0) for r in valid)
    avg_soc = sum(r.get("soc_pct", 0) for r in valid) / len(valid)

    lines = [f"SYSTEM TOTAL ({len(valid)} batteries in parallel)"]
    lines.append(f"  Voltage:         {avg_voltage:.2f} V")
    lines.append(f"  Total Current:   {total_current:+.2f} A")
    lines.append(f"  Total Power:     {total_power:.1f} W")
    lines.append(f"  Average SOC:     {avg_soc:.1f} %")
    lines.append(f"  Total Remaining: {total_remain:.2f} Ah")
    if total_full > 0:
        energy_kwh = total_full * avg_voltage / 1000
        remain_kwh = total_remain * avg_voltage / 1000
        lines.append(f"  Total Capacity:  {total_full:.2f} Ah  ({energy_kwh:.1f} kWh)")
        lines.append(f"  Energy Remaining:{remain_kwh:.1f} kWh")

    logger.debug("\n".join(lines))
