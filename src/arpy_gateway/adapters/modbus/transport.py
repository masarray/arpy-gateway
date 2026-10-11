"""PyModbus v3.15 transport boundary, not exposed to canonical domain.

The backend can be injected for deterministic tests. I/O is async and bounded.
Serial bus has exactly one shared client; one lock serializes all RTU requests.
"""
from __future__ import annotations
from dataclasses import dataclass
from .configuration import DeviceConfig, validate_serial
from .planner import ReadRequest


class ModbusReadError(Exception):
    """Validated Modbus exception/response error (not a valid measurement)."""


class PymodbusClient:
    def __init__(self, client):
        self.client = client

    async def connect(self):
        result = await self.client.connect()
        if result is False or not self.client.connected:
            raise ConnectionError("Modbus connection failed")

    async def read(self, req: ReadRequest, unit_id: int) -> tuple[int | bool, ...]:
        names = {1: "read_coils", 2: "read_discrete_inputs",
                 3: "read_holding_registers", 4: "read_input_registers"}
        response = await getattr(self.client, names[req.function])(
            address=req.address, count=req.count, device_id=unit_id)
        if response is None or response.isError():
            raise ModbusReadError(f"Modbus FC{req.function:02d} exception: {response}")
        payload = response.bits if req.function in (1, 2) else response.registers
        if payload is None or len(payload) < req.count:
            raise ModbusReadError("Incomplete Modbus read response")
        return tuple(payload[:req.count])

    def close(self):
        self.client.close()


class PymodbusFactory:
    def tcp(self, device: DeviceConfig):
        try:
            from pymodbus.client import AsyncModbusTcpClient
        except ImportError as exc:
            raise RuntimeError("Install optional transport: pymodbus==3.15.0") from exc
        return PymodbusClient(AsyncModbusTcpClient(
            device.host, port=device.port, timeout=device.timeout_ms/1000,
            retries=0, reconnect_delay=1, reconnect_delay_max=10))

    def serial(self, instance, timeout_seconds: float):
        try:
            from pymodbus.client import AsyncModbusSerialClient
        except ImportError as exc:
            raise RuntimeError("Install pymodbus[serial]==3.15.0") from exc
        options = validate_serial(instance)
        return PymodbusClient(AsyncModbusSerialClient(
            **options, timeout=timeout_seconds, retries=0))
