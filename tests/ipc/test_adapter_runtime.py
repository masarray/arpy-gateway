"""Adapter port → runtime state → IPC projection tests without wire drivers."""
import asyncio
import unittest
from datetime import datetime, timezone

from arpy_gateway.domain.models import (
    ProtocolInstance, ProtocolSignal, InstanceRole, SignalFamily, SignalDirection,
    ValueType, SignalSample, Quality, SignalOrigin,
)
from arpy_gateway.runtime.ports import ProtocolAdapter
from arpy_gateway.runtime.service.adapter_runtime import AdapterRuntime
from arpy_gateway.ipc.server import LocalReadOnlyServer
from arpy_gateway.ipc.client import request

TOKEN = "headless-bridge-credential-0123456789abcdef"


def make_instance():
    return ProtocolInstance("tcp1", "Test", "modbus_tcp", InstanceRole.CLIENT, True, {})


def signal(sid):
    return ProtocolSignal(sid, "tcp1", sid, SignalFamily.ANALOG,
                          ValueType.UINT16, SignalDirection.INBOUND)


class FakePort(ProtocolAdapter):
    def __init__(self):
        self.q = asyncio.Queue(maxsize=12)
        self.started = False
        self.stopped = False

    @property
    def protocol_id(self):
        return "modbus_tcp"

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
        raise PermissionError("No physical controls")

    async def sample(self, sid, val, seq):
        await self.q.put(SignalSample(sid, val, Quality.GOOD, SignalOrigin.PHYSICAL,
                                      datetime.now(timezone.utc), seq))


class AdapterBridgeTests(unittest.IsolatedAsyncioTestCase):
    async def test_value_page_and_sequence_guard_over_authenticated_ipc(self):
        fake = FakePort()
        runtime = AdapterRuntime(make_instance(), (signal("a"), signal("b")), fake)
        server = LocalReadOnlyServer(runtime, token=TOKEN)
        await runtime.start()
        port = await server.start()
        try:
            await fake.sample("a", 123, 1)
            await fake.sample("a", 99, 1)   # stale sequence must be discarded
            await fake.sample("unknown", 0, 2)
            await fake.sample("b", 456, 2)
            await asyncio.sleep(.08)
            page = await request(port, TOKEN, "signals.page", {"offset": 0, "limit": 2})
            self.assertEqual([p["value"] for p in page["items"]], [123, 456])
            health = await request(port, TOKEN, "health")
            self.assertEqual(health["frames_generated"], 2)
            self.assertEqual(health["samples_ignored"], 2)
            self.assertEqual(health["protocol"], "modbus_tcp")
            self.assertIsNone(health["active_revision"])
        finally:
            await server.stop()
            await runtime.stop()
        self.assertTrue(fake.stopped)

    async def test_reader_termination_invalidates_last_good_instead_of_freezing(self):
        fake = FakePort()
        runtime = AdapterRuntime(make_instance(), (signal("a"),), fake)
        await runtime.start()
        try:
            await fake.sample("a", 212, 1)
            await asyncio.sleep(.03)
            await fake.q.put(None)  # unexpected sample stream termination
            await asyncio.sleep(.04)
            self.assertEqual(runtime.health()["state"], "DEGRADED")
            record = runtime.page(offset=0, limit=1)["items"][0]
            self.assertIsNone(record["value"])
            self.assertEqual(record["quality"], "communication_lost")
            self.assertEqual(record["sequence"], 2)
        finally:
            await runtime.stop()

    async def test_cancelled_partial_start_releases_open_resources(self):
        class WaitingPort(FakePort):
            async def start(self, instance, signals):
                self.started = True
                await asyncio.Event().wait()
        adapter = WaitingPort()
        runtime = AdapterRuntime(make_instance(), (signal("a"),), adapter)
        pending = asyncio.create_task(runtime.start())
        await asyncio.sleep(.03)
        pending.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await pending
        self.assertTrue(adapter.stopped)
        self.assertEqual(runtime.health()["state"], "FAILED_TO_START")

    async def test_unimplemented_device_status_not_advertised_by_stub_adapter(self):
        fake = FakePort()
        runtime = AdapterRuntime(make_instance(), (signal("a"),), fake)
        server = LocalReadOnlyServer(runtime, token=TOKEN)
        await runtime.start()
        port = await server.start()
        try:
            hello = await request(port, TOKEN, "hello")
            self.assertNotIn("devices.page", hello["capabilities"])
            from arpy_gateway.ipc.client import IPCRemoteError
            with self.assertRaisesRegex(IPCRemoteError, "OPERATION_NOT_SUPPORTED"):
                await request(port, TOKEN, "devices.page", {"offset": 0, "limit": 1})
        finally:
            await server.stop()
            await runtime.stop()

    async def test_delta_invalidated_on_stream_failure_and_no_fake_good(self):
        fake = FakePort()
        runtime = AdapterRuntime(make_instance(), (signal("a"),), fake)
        server = LocalReadOnlyServer(runtime, token=TOKEN)
        await runtime.start()
        port = await server.start()
        try:
            await fake.sample("a", 10, 1)
            await asyncio.sleep(.03)
            initial = await request(port, TOKEN, "signals.page",
                                    {"offset": 0, "limit": 1})
            self.assertEqual(initial["items"][0]["quality"], "good")
            assert initial["items"][0]["value"] == 10
            await fake.q.put(None)
            await asyncio.sleep(.04)
            invalid = await request(port, TOKEN, "signals.delta", {
                "offset": 0, "limit": 1,
                "cursor": initial["cursor"],
                "generation": initial["generation"],
            })
            self.assertTrue(invalid["resync_required"])
            self.assertEqual(invalid["items"], [])
            latest = await request(port, TOKEN, "signals.page",
                                   {"offset": 0, "limit": 1})
            self.assertEqual(latest["items"][0]["quality"], "communication_lost")
            self.assertIsNone(latest["items"][0]["value"])
            follow = await request(port, TOKEN, "signals.delta", {
                "offset": 0, "limit": 1,
                "cursor": latest["cursor"], "generation": latest["generation"],
            })
            self.assertFalse(follow["resync_required"])
            self.assertEqual(follow["items"], [])
        finally:
            await server.stop()
            await runtime.stop()

    async def test_partial_adapter_start_failure_still_closes_resources(self):
        class HalfStarted(FakePort):
            async def start(self, instance, signals):
                self.started = True
                raise OSError("port busy after open")
        fake = HalfStarted()
        runtime = AdapterRuntime(make_instance(), (signal("a"),), fake)
        with self.assertRaisesRegex(OSError, "port busy"):
            await runtime.start()
        self.assertTrue(fake.stopped)
        self.assertEqual(runtime.health()["state"], "FAILED_TO_START")

    async def test_reject_wrong_family_and_duplicate_ids_before_connect(self):
        wrong = FakePort()
        class Bad(FakePort):
            @property
            def protocol_id(self):
                return "iec104"
        with self.assertRaises(ValueError):
            AdapterRuntime(make_instance(), (signal("a"),), Bad())
        with self.assertRaises(ValueError):
            AdapterRuntime(make_instance(), (signal("a"), signal("a")), wrong)
        self.assertFalse(wrong.started)
