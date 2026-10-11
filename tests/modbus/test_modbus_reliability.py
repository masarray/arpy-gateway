"""Fault isolation and offline-bundle acceptance for Modbus issue #6.

No Qt, DB, downloads or hardware needed. Real PyModbus is installed by
Windows/Linux protocol CI; fake transports prove deterministic fault behavior.
"""
from __future__ import annotations

import asyncio
import unittest
from unittest.mock import patch

from arpy_gateway.domain.models import (
    InstanceRole, ProtocolInstance, ProtocolSignal, SignalFamily,
    SignalDirection, ValueType, Quality,
)
from arpy_gateway.adapters.modbus import DeviceConfig, ModbusClientAdapter
from arpy_gateway.adapters.modbus.transport import (
    ModbusNoResponseError, ModbusBundleError, verify_embedded_transport,
)


def instance():
    return ProtocolInstance(
        "bus", "Physical RTU bus", "modbus_rtu", InstanceRole.CLIENT, True,
        {"serial_port": "loop://", "baudrate": 9600, "parity": "N", "stopbits": 1},
    )


def signal(name, unit):
    return ProtocolSignal(
        name, "bus", name, SignalFamily.ANALOG, ValueType.UINT16,
        SignalDirection.INBOUND, True, "",
        {"device_id": f"device-{unit}", "register_table": "holding",
         "native_address": 100},
    )


class BusClient:
    def __init__(self):
        self.calls = []
        self.closed = False

    async def connect(self):
        return None

    async def read(self, req, unit):
        self.calls.append(unit)
        if unit == 1:
            raise ModbusNoResponseError("silent slave")
        return (250,)

    def close(self):
        self.closed = True


class BusFactory:
    def __init__(self):
        self.connections = []

    def serial(self, instance, timeout):
        obj = BusClient()
        self.connections.append(obj)
        return obj


class FaultIsolationTests(unittest.IsolatedAsyncioTestCase):
    async def test_silent_rtu_slave_does_not_restart_bus_or_block_healthy_slave(self):
        factory = BusFactory()
        devices = (
            DeviceConfig("device-1", "bus", "Missing Slave", 1, "", 502, 60, 80),
            DeviceConfig("device-2", "bus", "Working Slave", 2, "", 502, 60, 80),
        )
        adapter = ModbusClientAdapter(devices, factory=factory, retry_limit_seconds=2)
        try:
            await adapter.start(instance(), (signal("missing", 1), signal("working", 2)))
            await asyncio.sleep(0.38)
            self.assertEqual(len(factory.connections), 1, "Silent slave must not restart whole bus")
            current = adapter.latest_snapshot()
            self.assertIsNone(current["missing"].value)
            self.assertEqual(current["missing"].quality, Quality.COMMUNICATION_LOST)
            self.assertEqual(current["working"].value, 250)
            self.assertEqual(current["working"].quality, Quality.GOOD)
            self.assertGreaterEqual(factory.connections[0].calls.count(2), 2)
            # Backoff throttles silent slave while good device keeps polling.
            self.assertLess(factory.connections[0].calls.count(1),
                            factory.connections[0].calls.count(2))
            status = {row.device_id: row for row in adapter.device_statuses()}
            self.assertEqual(status["device-1"].last_error, "SLAVE_NO_RESPONSE")
            self.assertGreaterEqual(status["device-2"].reads_ok, 2)
        finally:
            await adapter.stop()
        self.assertTrue(factory.connections[0].closed)

    async def test_bundle_failure_is_detected_before_any_network_or_background_task(self):
        class BadFactory(BusFactory):
            def preflight(self, protocol):
                raise ModbusBundleError("Bundled transport missing")
            def tcp(self, device):
                raise AssertionError("A socket must never open")
        tcp_instance = ProtocolInstance("tcp", "TCP", "modbus_tcp", InstanceRole.CLIENT, True,{})
        adapter = ModbusClientAdapter([
            DeviceConfig("remote", "tcp", "Remote", 1, "127.0.0.1", 502, 100, 100)
        ], factory=BadFactory())
        try:
            with self.assertRaisesRegex(ModbusBundleError, "Bundled transport"):
                await adapter.start(tcp_instance, ())
            self.assertEqual(adapter.device_statuses(), ())
            self.assertFalse(adapter._running)
            self.assertEqual(adapter._tasks, [])
        finally:
            await adapter.stop()


class BundleTests(unittest.TestCase):
    def test_exact_pinned_bundle_and_version_mismatch(self):
        try:
            import pymodbus
        except ImportError:
            self.skipTest("Run on Windows/Linux CI with embedded PyModbus")
        verify_embedded_transport("modbus_tcp")
        verify_embedded_transport("modbus_rtu")
        with patch.object(pymodbus, "__version__", "999.0.0"):
            with self.assertRaisesRegex(ModbusBundleError, "Incompatible bundled"):
                verify_embedded_transport("modbus_tcp")

    def test_invalid_transport_does_not_initialize_dependencies(self):
        with self.assertRaises(ValueError):
            verify_embedded_transport("modbus_udp")
