"""Independent Modbus TCP/RTU instance supervisors share one virtual IPC view.

This is a pure-Python orchestration test; real Modbus TCP socket protocol is
exercised separately by test_modbus_runtime_integration.py on Windows/Linux.
"""
from __future__ import annotations

import asyncio
import unittest
from datetime import datetime, timezone

from arpy_gateway.domain.models import (
    InstanceRole, ProtocolInstance, ProtocolSignal, SignalSample, Quality,
    SignalOrigin, SignalFamily, SignalDirection, ValueType,
)
from arpy_gateway.runtime.ports import ProtocolAdapter
from arpy_gateway.runtime.service.adapter_runtime import AdapterRuntime
from arpy_gateway.runtime.service.fleet import RuntimeFleet
from arpy_gateway.ipc.server import LocalReadOnlyServer
from arpy_gateway.ipc.client import request

TOKEN = "fleet-monitor-credential-0123456789abcdef123"


class Port(ProtocolAdapter):
    def __init__(self, family="modbus_tcp", *, fail=False):
        self.family, self.fail = family, fail
        self.queue = asyncio.Queue(maxsize=16)
        self.stopped = False

    @property
    def protocol_id(self):
        return self.family

    async def start(self, instance, signals):
        if self.fail:
            raise OSError("injected unavailable serial link")

    async def stop(self):
        self.stopped = True
        await self.queue.put(None)

    async def samples(self):
        while True:
            item = await self.queue.get()
            if item is None:
                return
            yield item

    async def publish(self, signal, sample):
        raise PermissionError("No field control")

    def device_page(self, *, offset: int, limit: int):
        rows = [{
            "device_id": self.family + "-device",
            "state": "online", "reads_ok": 3, "read_errors": 0,
            "reconnects": 0, "last_error": "", "last_rx_utc": None,
        }]
        return {"total": 1, "offset": offset, "items": rows[offset:offset + limit]}


def sample(sid, val, seq):
    return SignalSample(sid, val, Quality.GOOD, SignalOrigin.PHYSICAL,
                        datetime.now(timezone.utc), seq)


def child(family, suffix, count, port):
    inst = ProtocolInstance(suffix, suffix, family, InstanceRole.CLIENT, True, {})
    signals = tuple(ProtocolSignal(
        f"{suffix}:signal:{i}", suffix, str(i), SignalFamily.ANALOG,
        ValueType.UINT16, SignalDirection.INBOUND) for i in range(count))
    return AdapterRuntime(inst, signals, port)


class RuntimeFleetTests(unittest.IsolatedAsyncioTestCase):
    async def test_cross_instance_virtual_pages_delta_quality_and_device_health(self):
        tcp, rtu = Port("modbus_tcp"), Port("modbus_rtu")
        fleet = RuntimeFleet((child("modbus_tcp", "tcpA", 2, tcp),
                              child("modbus_rtu", "rtuB", 2, rtu)))
        ipc = LocalReadOnlyServer(fleet, token=TOKEN)
        await fleet.start()
        address = await ipc.start()
        try:
            baseline = await request(address, TOKEN, "signals.page",
                                     {"offset": 1, "limit": 3})
            self.assertEqual(baseline["total"], 4)
            self.assertEqual([r["signal_id"] for r in baseline["items"]],
                             ["tcpA:signal:1", "rtuB:signal:0", "rtuB:signal:1"])
            self.assertEqual(baseline["source"], "INJECTED_ADAPTER_FLEET")
            await tcp.queue.put(sample("tcpA:signal:1", 111, 1))
            await rtu.queue.put(sample("rtuB:signal:0", 222, 1))
            await asyncio.sleep(.04)
            changed = await request(address, TOKEN, "signals.delta", {
                "generation": baseline["generation"], "cursor": baseline["cursor"],
                "offset": 1, "limit": 3})
            self.assertFalse(changed["resync_required"])
            self.assertEqual([r["value"] for r in changed["items"]], [111, 222])
            devices = await request(address, TOKEN, "devices.page",
                                    {"offset": 1, "limit": 1})
            self.assertEqual(devices["total"], 2)
            self.assertEqual(devices["items"][0]["instance_id"], "rtuB")
            self.assertEqual(devices["items"][0]["device_id"], "modbus_rtu-device")
            status = await request(address, TOKEN, "health")
            self.assertEqual(status["instances_total"], 2)
            self.assertEqual(status["state"], "RUNNING")
            self.assertEqual(status["signals_configured"], 4)
            await rtu.queue.put(None)  # unexpected stream failure for one bus
            await asyncio.sleep(.04)
            status = await request(address, TOKEN, "health")
            self.assertEqual(status["state"], "DEGRADED")
            self.assertEqual(status["instances"][0]["state"], "RUNNING")
            self.assertEqual(status["instances"][1]["state"], "DEGRADED")
            stale = await request(address, TOKEN, "signals.delta", {
                "generation": changed["generation"], "cursor": changed["cursor"],
                "offset": 1, "limit": 3})
            self.assertTrue(stale["resync_required"])
            snap = await request(address, TOKEN, "signals.page",
                                 {"offset": 1, "limit": 3})
            self.assertEqual(snap["items"][0]["quality"], "good")
            self.assertEqual(snap["items"][1]["quality"], "communication_lost")
            self.assertIsNone(snap["items"][1]["value"])
        finally:
            await ipc.stop()
            await fleet.stop()
        self.assertTrue(tcp.stopped and rtu.stopped)

    async def test_one_failed_start_preserves_healthy_protocol_instance(self):
        tcp, rtu = Port("modbus_tcp"), Port("modbus_rtu", fail=True)
        fleet = RuntimeFleet((child("modbus_tcp", "a", 1, tcp),
                              child("modbus_rtu", "b", 1, rtu)))
        await fleet.start()
        try:
            status = fleet.health()
            self.assertEqual(status["state"], "DEGRADED")
            self.assertEqual(status["instances"][1]["start_error"], "START_OSERROR")
            await tcp.queue.put(sample("a:signal:0", 99, 1))
            await asyncio.sleep(.03)
            self.assertEqual(fleet.page(offset=0, limit=1)["items"][0]["value"], 99)
        finally:
            await fleet.stop()

    def test_reject_duplicate_cross_instance_signal_id(self):
        a, b = child("modbus_tcp", "same", 1, Port()), child(
            "modbus_tcp", "same", 1, Port())
        with self.assertRaisesRegex(ValueError, "Same stable signal"):
            RuntimeFleet((a, b))

    def test_do_not_materialize_per_signal_qt_objects(self):
        instances = [child("modbus_tcp", str(i), 1, Port()) for i in range(64)]
        fleet = RuntimeFleet(instances)
        self.assertEqual(fleet._offsets[-1], 64)
        self.assertEqual(fleet._changes.depth, 0)
