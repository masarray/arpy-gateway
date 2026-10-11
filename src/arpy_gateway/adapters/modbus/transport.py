"""PyModbus v3.15 transport boundary, not exposed to canonical domain.

The backend can be injected for deterministic tests. I/O is async and bounded.
Serial bus has exactly one shared client; one lock serializes all RTU requests.
"""
from __future__ import annotations
from dataclasses import dataclass
from .configuration import DeviceConfig, validate_serial
from .planner import ReadRequest

# Import once: this code runs for every Modbus request. The adapter remains
# importable without optional transport when inspecting contracts offline.
try:
    from pymodbus.exceptions import ModbusIOException as _NoResponseType
except ImportError:
    _NoResponseType = ()


class ModbusReadError(Exception):
    """Validated Modbus exception/response error (not a valid measurement)."""


class ModbusNoResponseError(ModbusReadError):
    """A slave did not answer. It does not imply the shared RS-485 port failed."""


class ModbusBundleError(RuntimeError):
    """Required embedded protocol transport is absent or mismatched."""


REQUIRED_PYMODBUS = "3.15.0"


def verify_embedded_transport(protocol: str) -> None:
    """Fail fast during daemon startup, never pip-install on a live gateway.

    A packager MUST include PyModbus and PySerial in the installer; optional
    source-only developer workflows may install them separately.
    """
    try:
        import pymodbus
        from pymodbus.client import AsyncModbusTcpClient
        if protocol == "modbus_rtu":
            import serial
            from pymodbus.client import AsyncModbusSerialClient
        elif protocol != "modbus_tcp":
            raise ValueError("Unsupported Modbus transport")
    except ImportError as exc:
        raise ModbusBundleError(
            "Bundled Modbus engine is missing. Repair/reinstall ARPY Gateway; "
            "no user command or online download is required."
        ) from exc
    found = getattr(pymodbus, "__version__", None)
    if found != REQUIRED_PYMODBUS:
        raise ModbusBundleError(
            f"Incompatible bundled Modbus engine ({found or 'unknown'}); "
            f"ARPY requires tested PyModbus {REQUIRED_PYMODBUS}. "
            "Repair installation instead of upgrading packages at runtime."
        )


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
        try:
            response = await getattr(self.client, names[req.function])(
                address=req.address, count=req.count, device_id=unit_id)
        except Exception as exc:
            # PyModbus raises ModbusIOException for a missing response (not a
            # device ExceptionResponse). Other transport faults need reconnect.
            if isinstance(exc, _NoResponseType):
                raise ModbusNoResponseError("Slave did not respond") from exc
            raise
        if response is None:
            raise ModbusNoResponseError("Empty response from Modbus slave")
        if isinstance(response, _NoResponseType):
            raise ModbusNoResponseError("Slave did not respond")
        if response.isError():
            raise ModbusReadError(f"Modbus FC{req.function:02d} exception: {response}")
        payload = response.bits if req.function in (1, 2) else response.registers
        if payload is None or len(payload) < req.count:
            raise ModbusReadError("Incomplete Modbus read response")
        return tuple(payload[:req.count])

    def close(self):
        self.client.close()


class PymodbusFactory:
    def preflight(self, protocol: str) -> None:
        verify_embedded_transport(protocol)

    def tcp(self, device: DeviceConfig):
        try:
            from pymodbus.client import AsyncModbusTcpClient
        except ImportError as exc:
            raise ModbusBundleError("Bundled Modbus TCP engine missing; repair ARPY installation") from exc
        return PymodbusClient(AsyncModbusTcpClient(
            device.host, port=device.port, timeout=device.timeout_ms/1000,
            retries=0, reconnect_delay=1, reconnect_delay_max=10))

    def serial(self, instance, timeout_seconds: float):
        try:
            from pymodbus.client import AsyncModbusSerialClient
        except ImportError as exc:
            raise ModbusBundleError("Bundled Modbus RTU/serial engine missing; repair ARPY installation") from exc
        options = validate_serial(instance)
        return PymodbusClient(AsyncModbusSerialClient(
            **options, timeout=timeout_seconds, retries=0))
