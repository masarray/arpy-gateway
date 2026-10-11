"""Adapter-owned configuration. This is NOT the project database/schema (#3/#13).

Engine consumes stable connection identities through injection; never infer an
IED from signal row/name/IP. RTU port/baud belongs to instance shared bus.
"""
from __future__ import annotations
from dataclasses import dataclass
from ipaddress import ip_address
from typing import Mapping
import math
from arpy_gateway.domain.models import ProtocolInstance, ProtocolSignal, InstanceRole, ValueType, SignalFamily

TABLE_TO_FC = {"coil": 1, "discrete": 2, "holding": 3, "input": 4}
WIDTH = {ValueType.BOOL: 1, ValueType.INT16: 1, ValueType.UINT16: 1,
         ValueType.INT32: 2, ValueType.UINT32: 2, ValueType.FLOAT32: 2,
         ValueType.INT64: 4, ValueType.UINT64: 4, ValueType.FLOAT64: 4}


def integer(value, label: str, low: int, high: int) -> int:
    if isinstance(value, bool) or isinstance(value, float):
        raise ValueError(f"{label} must be an integer")
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} must be an integer") from exc
    if not low <= parsed <= high:
        raise ValueError(f"{label} must be within {low}..{high}")
    return parsed


@dataclass(frozen=True)
class DeviceConfig:
    id: str
    instance_id: str
    name: str
    unit_id: int
    host: str = ""  # TCP: per IED; RTU: empty (same serial bus)
    port: int = 502
    poll_ms: int = 1000
    timeout_ms: int = 900
    enabled: bool = True

    def validate(self, instance: ProtocolInstance) -> None:
        if instance.protocol not in ("modbus_tcp", "modbus_rtu") or instance.role != InstanceRole.CLIENT:
            raise ValueError("Modbus client only: instance must be client with valid transport")
        if not self.id or not self.name.strip() or self.instance_id != instance.id:
            raise ValueError("Device requires stable ID, name, correct instance ownership")
        integer(self.unit_id, "unit_id", 1, 247)  # no RTU broadcast reads
        integer(self.poll_ms, "poll_ms", 50, 3_600_000)
        integer(self.timeout_ms, "timeout_ms", 50, 120_000)
        if instance.protocol == "modbus_tcp":
            if not self.host or len(self.host) > 255 or any(x in self.host for x in " /\\\t\n\r"):
                raise ValueError("TCP peer requires valid hostname/IP")
            integer(self.port, "TCP port", 1, 65535)
        elif self.host:
            raise ValueError("RTU device host must be empty; shared COM/TTY lives in instance")


def validate_serial(instance: ProtocolInstance) -> dict:
    if instance.protocol != "modbus_rtu":
        raise ValueError("Serial bus configuration requested for non-RTU instance")
    opts = dict(instance.settings)
    port = opts.get("serial_port", "")
    if not isinstance(port, str) or not port.strip() or len(port) > 255:
        raise ValueError("RTU requires serial_port (COMx or /dev/tty...)")
    baudrate = integer(opts.get("baudrate", 9600), "baudrate", 1200, 921600)
    if baudrate not in {1200, 2400, 4800, 9600, 19200, 38400, 57600, 115200, 230400, 460800, 921600}:
        raise ValueError("Unsupported baudrate")
    parity = opts.get("parity", "N")
    if parity not in ("N", "E", "O"):
        raise ValueError("parity must be N/E/O")
    stopbits = integer(opts.get("stopbits", 1), "stopbits", 1, 2)
    return dict(port=port, baudrate=baudrate, parity=parity, stopbits=stopbits, bytesize=8)


@dataclass(frozen=True)
class ParsedSignal:
    signal: ProtocolSignal
    device_id: str
    function: int
    address: int
    width: int
    byte_order: str
    word_order: str
    scale: float
    offset: float


def parse_signal(signal: ProtocolSignal, device_ids: set[str]) -> ParsedSignal:
    p: Mapping = signal.parameters
    device_id = p.get("device_id", "")
    if not isinstance(device_id, str) or device_id not in device_ids:
        raise ValueError(f"{signal.id}: missing/invalid connection ID (RFC #13)")
    table = str(p.get("register_table", "")).lower()
    if table not in TABLE_TO_FC:
        raise ValueError(f"{signal.id}: invalid Modbus register table")
    typ = signal.value_type
    if typ not in WIDTH or (table in ("coil", "discrete") and typ is not ValueType.BOOL):
        raise ValueError(f"{signal.id}: unsupported value type/bit table mismatch")
    if table in ("holding", "input") and typ is ValueType.BOOL:
        raise ValueError(f"{signal.id}: register table needs a numeric value type")
    width = WIDTH[typ]
    if "register_count" in p and integer(p["register_count"], "register_count", 1, 125) != width:
        raise ValueError(f"{signal.id}: register_count inconsistent with value_type")
    address = integer(p.get("native_address"), "native_address", 0, 65535)
    if address + width > 65536:
        raise ValueError(f"{signal.id}: address+width exceeds PDU 16-bit limit")
    byte_order = p.get("byte_order", "big")
    word_order = p.get("word_order", "big")
    if byte_order not in ("big", "little") or word_order not in ("big", "little"):
        raise ValueError(f"{signal.id}: byte_order and word_order must be big/little")
    try:
        scale, offset = float(p.get("scale", 1)), float(p.get("offset", 0))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{signal.id}: bad scaling") from exc
    if not math.isfinite(scale) or not math.isfinite(offset):
        raise ValueError(f"{signal.id}: non-finite scaling")
    if signal.family is SignalFamily.COMMAND:
        raise ValueError(f"{signal.id}: command cannot be polled as read-only monitoring")
    return ParsedSignal(signal, device_id, TABLE_TO_FC[table], address,
                        width, byte_order, word_order, scale, offset)
