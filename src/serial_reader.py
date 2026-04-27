"""RS485 serial communication and PACE protocol frame parsing."""

import logging
import time

import serial

from src.config import (
    BAUD_RATE, VER, CID1,
    CID2_GET_ANALOG, CID2_GET_DATE, CID2_GET_MFGR, CID2_GET_PROTO,
    RTN_OK, TEMP_LABELS,
)
from src.utils import get_u16, get_s16

logger = logging.getLogger(__name__)


# ── Checksum & Frame Building ─────────────────────────────────────────────────

def compute_checksum(ascii_hex_str: str) -> str:
    total = sum(ord(c) for c in ascii_hex_str)
    chksum = (~total + 1) & 0xFFFF
    return f"{chksum:04X}"


def encode_length(hex_char_count: int) -> str:
    lchksum = (~(hex_char_count & 0xFFF) + 1) & 0xF
    length_field = (lchksum << 12) | (hex_char_count & 0xFFF)
    return f"{length_field:04X}"


def build_command(bms_addr: int, cid2: int, info_bytes: bytes = b'') -> bytes:
    info_hex = info_bytes.hex().upper()
    length_hex = encode_length(len(info_hex))
    inner = f"{VER:02X}{bms_addr:02X}{CID1:02X}{cid2:02X}{length_hex}{info_hex}"
    chksum = compute_checksum(inner)
    frame = f"~{inner}{chksum}\r"
    return frame.encode('ascii')


# ── Command Builders ──────────────────────────────────────────────────────────

def build_get_analog_cmd(bms_addr: int) -> bytes:
    return build_command(bms_addr, CID2_GET_ANALOG, bytes([bms_addr]))


def build_get_protocol_version_cmd(bms_addr: int) -> bytes:
    return build_command(bms_addr, CID2_GET_PROTO, bytes([bms_addr]))


def build_get_manufacturer_info_cmd(bms_addr: int) -> bytes:
    return build_command(bms_addr, CID2_GET_MFGR, bytes([bms_addr]))


def build_get_date_cmd(bms_addr: int) -> bytes:
    return build_command(bms_addr, CID2_GET_DATE, bytes([bms_addr]))


# ── Serial Communication ─────────────────────────────────────────────────────

def open_serial(port: str) -> serial.Serial:
    ser = serial.Serial(
        port=port,
        baudrate=BAUD_RATE,
        bytesize=serial.EIGHTBITS,
        parity=serial.PARITY_NONE,
        stopbits=serial.STOPBITS_ONE,
        timeout=1.0,
    )
    ser.reset_input_buffer()
    ser.reset_output_buffer()
    return ser


def send_command(ser: serial.Serial, cmd: bytes, timeout: float = 1.5) -> str:
    """
    Send command, read until \\r or timeout.
    Returns the clean ASCII hex payload between '~' and '\\r'.
    """
    ser.reset_input_buffer()
    ser.write(cmd)
    ser.flush()

    logger.debug("TX > %s", cmd.decode('ascii').strip())

    response = b''
    start = time.time()

    while (time.time() - start) < timeout:
        if ser.in_waiting > 0:
            byte = ser.read(1)
            if byte == b'\r':
                break
            response += byte
        else:
            time.sleep(0.01)

    # Find the '~' SOI marker — everything before it is garbage
    try:
        soi_idx = response.index(b'~')
        payload = response[soi_idx + 1:]
    except ValueError:
        payload = response

    # Only keep valid ASCII hex characters (0-9, A-F, a-f)
    clean = ''.join(chr(b) for b in payload
                    if chr(b) in '0123456789ABCDEFabcdef').upper()

    if clean:
        disp = clean[:80] + ('...' if len(clean) > 80 else '')
        logger.debug("RX < %s", disp)

    return clean


def send_raw_command(ser: serial.Serial, cmd: bytes, timeout: float = 2.0) -> bytes:
    """
    Send command and return raw frame bytes between '~' and '\\r'.
    Used for commands like 0x51 that return raw ASCII (not hex-encoded) INFO.
    """
    ser.reset_input_buffer()
    ser.write(cmd)
    ser.flush()

    response = b''
    start = time.time()
    while (time.time() - start) < timeout:
        if ser.in_waiting > 0:
            byte = ser.read(1)
            response += byte
            if byte == b'\r':
                break
        else:
            time.sleep(0.01)

    # Extract bytes between ~ and \r
    try:
        soi = response.index(b'~')
        try:
            eoi = response.index(b'\r', soi + 1)
            return response[soi + 1:eoi]
        except ValueError:
            return response[soi + 1:]
    except ValueError:
        return response


def flush_bus(ser: serial.Serial, delay: float = 0.3):
    """
    Drain any leftover bytes from the RS485 bus.
    Critical after raw ASCII commands (0x51) which can leave
    null bytes or partial frames that corrupt subsequent reads.
    """
    time.sleep(delay)
    while ser.in_waiting > 0:
        ser.read(ser.in_waiting)
        time.sleep(0.05)
    ser.reset_input_buffer()


# ── Frame Parsing ─────────────────────────────────────────────────────────────

def parse_frame(hex_str: str) -> dict:
    """Parse a PACE-style response frame header and extract INFO bytes."""
    if len(hex_str) < 16:
        return {"valid": False, "error": f"Frame too short ({len(hex_str)} hex chars)"}

    try:
        ver   = int(hex_str[0:2], 16)
        addr  = int(hex_str[2:4], 16)
        cid1  = int(hex_str[4:6], 16)
        cid2  = int(hex_str[6:8], 16)
        length_field = int(hex_str[8:12], 16)
        info_hex_len = length_field & 0x0FFF

        if info_hex_len % 2 != 0:
            return {"valid": False, "error": f"Odd INFO length ({info_hex_len}) - corrupted frame"}

        info_hex = hex_str[12:12 + info_hex_len]
        chksum_hex = hex_str[12 + info_hex_len:12 + info_hex_len + 4]

        info_bytes = bytes.fromhex(info_hex) if info_hex else b''

        return {
            "valid": True,
            "ver": ver,
            "addr": addr,
            "cid1": cid1,
            "cid2": cid2,
            "info_bytes": info_bytes,
            "chksum": chksum_hex,
        }
    except (ValueError, IndexError) as e:
        return {"valid": False, "error": str(e), "raw": hex_str[:60]}


def parse_analog_response(hex_str: str, bms_addr: int) -> dict:
    """
    Parse battery analog values response.

    INFO byte layout:
      [0]           flag/address echo byte
      [1]           cell count (N)
      [2..2+N*2]    cell voltages (u16 big-endian, mV)
      [next]        temperature count (T)
      [+T*2]        temperatures (u16 big-endian, 0.1K, offset 2731 = 0C)
      Then main values:
        [+0..1]     current   (s16, *0.01 = Amps)
        [+2..3]     voltage   (u16, *0.01 = Volts)
        [+4..5]     remain_ah (u16, *0.01 = Ah)
        [+6]        skip byte (observed: 0x05)
        [+7..8]     full_ah   (u16, *0.01 = Ah)
        [+9]        skip byte (observed: 0x00)
        [+10]       cycle_count (u8)
        [+11]       soc_int     (u8, integer %)
        [+12]       soh         (u8, %)
    """
    frame = parse_frame(hex_str)
    if not frame["valid"]:
        return {"error": frame.get("error", "Invalid frame")}

    if frame["cid2"] != RTN_OK:
        return {"error": f"BMS returned error code 0x{frame['cid2']:02X}"}

    data = frame["info_bytes"]
    if len(data) < 10:
        return {"error": f"INFO too short ({len(data)} bytes)"}

    result = {"bms_addr": bms_addr}

    try:
        p = 0
        _flag = data[p]; p += 1
        cell_count = data[p]; p += 1
        if cell_count > 32:
            cell_count = 32

        # ── Cell Voltages ──
        cells = []
        min_v, max_v = 99.0, 0.0
        min_idx, max_idx = 0, 0

        for i in range(cell_count):
            if p + 1 >= len(data):
                break
            mv = get_u16(data, p)
            v = mv / 1000.0
            cells.append(v)
            if v > 0.1:
                if v < min_v:
                    min_v = v; min_idx = i + 1
                if v > max_v:
                    max_v = v; max_idx = i + 1
            p += 2

        result["cell_count"] = cell_count
        result["cells_v"] = cells
        result["min_cell_v"] = round(min_v, 3)
        result["max_cell_v"] = round(max_v, 3)
        result["min_cell_idx"] = min_idx
        result["max_cell_idx"] = max_idx
        result["cell_diff_mv"] = round((max_v - min_v) * 1000, 1)

        # ── Temperatures ──
        if p < len(data):
            temp_count = data[p]; p += 1
            if temp_count > 8:
                temp_count = 8

            temps = []
            for i in range(temp_count):
                if p + 1 >= len(data):
                    break
                raw = get_u16(data, p)
                temp_c = (raw - 2731) / 10.0
                if temp_c < -50 or temp_c > 150:
                    temp_c = None
                label = TEMP_LABELS[i] if i < len(TEMP_LABELS) else f"T{i+1}"
                temps.append({"label": label, "celsius": temp_c})
                p += 2

            result["temps"] = temps

        # ── Main Battery Values ──
        if p + 6 <= len(data):
            current_a = get_s16(data, p) * 0.01;  p += 2
            voltage_v = get_u16(data, p) * 0.01;   p += 2
            remain_ah = get_u16(data, p) * 0.01;   p += 2

            result["current_a"] = round(current_a, 2)
            result["voltage_v"] = round(voltage_v, 2)
            result["remain_ah"] = round(remain_ah, 2)
            result["power_w"]   = round(current_a * voltage_v, 1)

            # Skip 1 byte (0x05), then full_ah
            if p + 2 < len(data):
                p += 1
                full_ah = get_u16(data, p) * 0.01;  p += 2
                result["full_ah"] = round(full_ah, 2)

                soc = (remain_ah / full_ah * 100.0) if full_ah > 0 else 0
                if soc > 100:
                    soc = 100
                result["soc_pct"] = round(soc, 1)

            # Skip 1 byte (0x00), then three u8 fields
            if p + 3 < len(data):
                p += 1                          # skip byte
                cycle_count = data[p]; p += 1   # u8: cycle count
                _soc_int    = data[p]; p += 1   # u8: integer SOC (redundant)
                soh         = data[p]; p += 1   # u8: SOH %

                result["cycle_count"] = cycle_count
                if soh <= 100:
                    result["soh_pct"] = soh

    except (IndexError, ValueError) as e:
        result["parse_error"] = str(e)

    return result


def parse_manufacturer_info(raw_frame: bytes, bms_addr: int) -> dict:
    """
    Parse CID2=0x51 manufacturer info response.
    The INFO field is raw ASCII text (not hex-encoded).
    """
    if len(raw_frame) < 16:
        return {"error": "Frame too short"}

    try:
        header = raw_frame[:12].decode('ascii', errors='replace')
        cid2 = int(header[6:8], 16)
        if cid2 != 0x00:
            return {"error": f"Return code 0x{cid2:02X}"}

        length_val = int(header[8:12], 16)
        info_len = length_val & 0x0FFF
    except (ValueError, IndexError) as e:
        return {"error": f"Header parse failed: {e}"}

    # INFO is raw ASCII (may contain nulls for padding)
    info_raw = raw_frame[12:12 + info_len]
    info_str = ''
    for b in info_raw:
        if 32 <= b < 127:
            info_str += chr(b)
        elif b == 0:
            pass  # skip null padding
        else:
            info_str += f'\\x{b:02X}'

    result = {"bms_addr": bms_addr, "raw_info": info_str}

    # Split on space to separate HW version from rest
    parts = info_str.split(' ', 1)
    if len(parts) >= 1:
        result["hw_version"] = parts[0]
    if len(parts) >= 2:
        rest = parts[1]
        if len(rest) >= 5:
            result["fw_hint"] = rest[:4]
            result["pack_sn"] = rest[4:]
        else:
            result["pack_sn"] = rest

    return result


def parse_date_response(raw_frame: bytes, bms_addr: int) -> dict:
    """
    Parse CID2=0x4D date/time response.
    INFO is hex-encoded: YYYY MM DD HH MM SS (each as hex pairs).
    """
    if len(raw_frame) < 16:
        return {"error": "Frame too short"}

    try:
        header = raw_frame[:12].decode('ascii', errors='replace')
        cid2 = int(header[6:8], 16)
        if cid2 != 0x00:
            return {"error": f"Return code 0x{cid2:02X}"}

        length_val = int(header[8:12], 16)
        info_len = length_val & 0x0FFF
    except (ValueError, IndexError) as e:
        return {"error": f"Header parse failed: {e}"}

    info_hex = raw_frame[12:12 + info_len].decode('ascii', errors='replace')

    result = {"bms_addr": bms_addr}
    try:
        if len(info_hex) >= 14:
            year   = int(info_hex[0:4], 16)
            month  = int(info_hex[4:6], 16)
            day    = int(info_hex[6:8], 16)
            hour   = int(info_hex[8:10], 16)
            minute = int(info_hex[10:12], 16)
            second = int(info_hex[12:14], 16)
            result["datetime"] = f"{year}-{month:02d}-{day:02d} {hour:02d}:{minute:02d}:{second:02d}"
    except ValueError as e:
        result["error"] = f"Date parse failed: {e}"

    return result


def query_device_info(ser, bms_addr: int) -> dict:
    """Query all info commands for a BMS and return combined result."""
    info = {"bms_addr": bms_addr}

    # CID2=0x51: Manufacturer Info (HW version + Pack SN)
    cmd = build_get_manufacturer_info_cmd(bms_addr)
    raw = send_raw_command(ser, cmd, timeout=2.0)
    if len(raw) > 16:
        mfgr = parse_manufacturer_info(raw, bms_addr)
        info.update(mfgr)

    flush_bus(ser, delay=0.4)

    # CID2=0x4D: Date/Time
    cmd = build_get_date_cmd(bms_addr)
    raw = send_raw_command(ser, cmd, timeout=2.0)
    if len(raw) > 12:
        dt = parse_date_response(raw, bms_addr)
        if "datetime" in dt:
            info["bms_datetime"] = dt["datetime"]

    flush_bus(ser, delay=0.4)

    return info
