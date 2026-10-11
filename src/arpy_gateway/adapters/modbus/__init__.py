"""Modbus adapter: pure planning/decoding plus optional async PyModbus transport.

No Qt dependency. The stable device/connection identifier is not an IP or Unit ID.
"""
from .configuration import DeviceConfig, ParsedSignal, parse_signal
from .adapter import ModbusClientAdapter, DeviceState, PollStatus
from .planner import ReadRequest, compile_reads
from .encoding import decode_value

__all__ = ["DeviceConfig", "ParsedSignal", "parse_signal", "ModbusClientAdapter",
           "DeviceState", "PollStatus", "ReadRequest", "compile_reads", "decode_value"]
from .server import ModbusTcpServerAdapter
__all__.append("ModbusTcpServerAdapter")
