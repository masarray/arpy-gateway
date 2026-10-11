"""Modbus bridge contract tests that do not require PyModbus or serial hardware.

Proves the dependency-injected bridge and RTU bus identity are testable even
when protocol adapter PR #15 has not been overlaid into the source checkout.
"""
import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace
import unittest

from arpy_gateway.domain.models import (
    ProtocolInstance, ProtocolSignal, SignalSample, InstanceRole,
    SignalFamily, ValueType, SignalDirection, Quality, SignalOrigin,
)
from arpy_gateway.runtime.service.modbus_bridge import BoundModbusAdapter
from arpy_gateway.runtime.service.adapter_runtime import AdapterRuntime
from arpy_gateway.ipc.client import request, IPCRemoteError
from arpy_gateway.ipc.server import LocalReadOnlyServer

TOKEN = "modbus-adapter-port-contract-test-0123456789"


class FakeModbusPort:
    def __init__(self):
        self.started = False
        self.stopped = False
        self.q = asyncio.Queue(maxsize=4)

    async def start(self, instance, signals):
        self.started = True

    async def stop(self):
        self.stopped = True
        await self.q.put(None)

    async def samples(self):
        while True:
            item = await self.q.get()
            if item is None:
                return
            yield item

    async def publish(self, signal, sample):
        raise PermissionError("Physical Modbus controls disabled")

    def device_statuses(self):
        return (
            SimpleNamespace(device_id="slave-1", state="online", reads_ok=3,
                            read_errors=0, reconnects=1, last_error="",
                            last_rx_utc=datetime.now(timezone.utc)),
            SimpleNamespace(device_id="slave-2", state="offline", reads_ok=0,
                            read_errors=4, reconnects=0,
                            last_error="SLAVE_NO_RESPONSE", last_rx_utc=None),
        )


class ModbusPortUnitTests(unittest.IsolatedAsyncioTestCase):
    async def test_rtu_family_binding_value_and_scoped_device_status(self):
        inst = ProtocolInstance("rtu", "RTU Bus", "modbus_rtu",
                                InstanceRole.CLIENT, True, {"serial_port": "COM9"})
        sig = ProtocolSignal("rtu:signal:1", "rtu", "RTU Value",
                             SignalFamily.ANALOG, ValueType.UINT16,
                             SignalDirection.INBOUND)
        port = FakeModbusPort()
        bound = BoundModbusAdapter(inst, (), inner=port)
        self.assertEqual(bound.protocol_id, "modbus_rtu")
        runtime = AdapterRuntime(inst, (sig,), bound)
        ipc = LocalReadOnlyServer(runtime, token=TOKEN)
        await runtime.start()
        address = await ipc.start()
        try:
            await port.q.put(SignalSample(sig.id, 234, Quality.GOOD,
                             SignalOrigin.PHYSICAL, datetime.now(timezone.utc), 1))
            await asyncio.sleep(.02)
            values = await request(address, TOKEN, "signals.page",
                                   {"offset": 0, "limit": 1})
            self.assertEqual(values["items"][0]["value"], 234)
            peer_page = await request(address, TOKEN, "devices.page",
                                      {"offset": 0, "limit": 2})
            self.assertEqual(peer_page["total"], 2)
            self.assertEqual(peer_page["items"][0]["device_id"], "slave-1")
            self.assertEqual(peer_page["items"][1]["last_error"], "SLAVE_NO_RESPONSE")
            self.assertIsNone(peer_page["items"][1]["last_rx_utc"])
            with self.assertRaisesRegex(IPCRemoteError, "BAD_PARAMETERS"):
                await request(address, TOKEN, "devices.page",
                              {"offset": 0, "limit": 129})
            with self.assertRaises(PermissionError):
                await bound.publish(sig, SignalSample(
                    sig.id, 55, Quality.GOOD, SignalOrigin.SIMULATED,
                    datetime.now(timezone.utc), 2))
        finally:
            await ipc.stop()
            await runtime.stop()
        self.assertTrue(port.started and port.stopped)

    def test_never_bind_wrong_protocol_or_server_role(self):
        for inst in (
            ProtocolInstance("x", "Wrong", "iec104", InstanceRole.CLIENT, True, {}),
            ProtocolInstance("x", "Not Client", "modbus_tcp",
                             InstanceRole.SERVER, True, {}),
        ):
            with self.subTest(protocol=inst.protocol, role=inst.role):
                with self.assertRaises(ValueError):
                    BoundModbusAdapter(inst, (), inner=FakeModbusPort())

    async def test_refuse_start_with_unexpected_reconfigured_instance(self):
        original = ProtocolInstance("a", "Bus A", "modbus_tcp",
                                    InstanceRole.CLIENT, True, {})
        modified = ProtocolInstance("a", "Bus A", "modbus_tcp",
                                    InstanceRole.CLIENT, False, {})
        port = FakeModbusPort()
        bound = BoundModbusAdapter(original, (), inner=port)
        with self.assertRaises(ValueError):
            await bound.start(modified, ())
        self.assertFalse(port.started)
