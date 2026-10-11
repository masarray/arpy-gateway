"""P8.3: independent Modbus TCP wire → headless runtime → value IPC.

The code under adapters/modbus is supplied by pinned draft PR #15 in CI.
No imported Qt, database or installed OS service. The fake slave is an actual
TCP server responding with Modbus MBAP/PDU, not a fabricated SignalSample.
"""
from __future__ import annotations

import asyncio
import os
from pathlib import Path
import struct
import sys
import unittest

from arpy_gateway.domain.models import (
    InstanceRole, ProtocolInstance, ProtocolSignal, SignalFamily,
    SignalDirection, ValueType,
)
from arpy_gateway.ipc.client import ViewportSession, request, IPCRemoteError
from arpy_gateway.ipc.server import LocalReadOnlyServer
from arpy_gateway.runtime.service.modbus_bridge import (
    BoundModbusAdapter, build_modbus_runtime,
)
from arpy_gateway.runtime.service.runner import serve_runtime

try:
    from arpy_gateway.adapters.modbus import DeviceConfig
except ImportError:
    DeviceConfig = None

TOKEN = "p8-3-test-token-0123456789abcdef-0123456"


def make_instance():
    return ProtocolInstance("m", "Modbus Test Master", "modbus_tcp",
                            InstanceRole.CLIENT, True, {})


def make_signal(sid, device, *, is_coil=False):
    return ProtocolSignal(sid, "m", sid,
                          SignalFamily.BINARY if is_coil else SignalFamily.ANALOG,
                          ValueType.BOOL if is_coil else ValueType.FLOAT32,
                          SignalDirection.INBOUND, True,
                          "" if is_coil else "V",
                          {"device_id": device,
                           "register_table": "coil" if is_coil else "holding",
                           "native_address": 5 if is_coil else 10})


class MbapSlave:
    """Tiny independent read-only Modbus TCP slave for deterministic wire QA."""
    def __init__(self):
        self.server = None
        self.port = 0
        self.requests: list[tuple[int, bytes]] = []
        self.float_value = 123.0
        self.coil = True

    async def start(self):
        self.server = await asyncio.start_server(self._serve, "127.0.0.1", 0)
        self.port = self.server.sockets[0].getsockname()[1]

    async def stop(self):
        self.server.close()
        await self.server.wait_closed()

    async def _serve(self, reader, writer):
        try:
            while True:
                txn, proto, length, unit = struct.unpack(
                    ">HHHB", await reader.readexactly(7))
                if proto != 0 or not 2 <= length <= 254:
                    return
                req = await reader.readexactly(length - 1)
                self.requests.append((unit, req))
                if req == bytes((3, 0, 10, 0, 2)):
                    pdu = bytes((3, 4)) + struct.pack(">f", self.float_value)
                elif req == bytes((1, 0, 5, 0, 1)):
                    pdu = bytes((1, 1, int(self.coil)))
                else:
                    pdu = bytes((req[0] | 0x80, 2))
                writer.write(struct.pack(">HHHB", txn, 0, len(pdu) + 1, unit) + pdu)
                await writer.drain()
        except (asyncio.IncompleteReadError, ConnectionError, OSError):
            pass
        finally:
            writer.close()
            await writer.wait_closed()


@unittest.skipUnless(DeviceConfig is not None, "Modbus PR #15 not overlaid in contract-only CI")
class RealModbusIPCIntegration(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.slave = MbapSlave()
        await self.slave.start()

    async def asyncTearDown(self):
        await self.slave.stop()

    async def _wait_for(self, predicate, *, seconds=3):
        deadline = asyncio.get_running_loop().time() + seconds
        while asyncio.get_running_loop().time() < deadline:
            if predicate():
                return
            await asyncio.sleep(.03)
        self.fail("Timed out waiting for actual Modbus wire data/status")

    async def test_two_ieds_real_tcp_values_and_unreachable_ied_isolated(self):
        dead = asyncio.start_server(lambda _r, _w: None, "127.0.0.1", 0)
        empty = await dead
        absent_port = empty.sockets[0].getsockname()[1]
        empty.close()
        await empty.wait_closed()

        devices = [
            DeviceConfig("healthy", "m", "Healthy IED", 1, "127.0.0.1",
                         self.slave.port, 75, 180),
            DeviceConfig("dead", "m", "Disconnected IED", 1, "127.0.0.1",
                         absent_port, 75, 180),
        ]
        signals = [make_signal("meter.voltage", "healthy"),
                   make_signal("meter.breaker", "healthy", is_coil=True),
                   make_signal("offline.voltage", "dead")]
        runtime = build_modbus_runtime(make_instance(), devices, signals)
        server = LocalReadOnlyServer(runtime, token=TOKEN)
        await runtime.start()
        port = await server.start()
        try:
            await self._wait_for(lambda: runtime.health()["frames_generated"] >= 4)
            values = await request(port, TOKEN, "signals.page", {"offset": 0, "limit": 3})
            self.assertEqual(values["source"], "INJECTED_ADAPTER")
            by_id = {row["signal_id"]: row for row in values["items"]}
            self.assertEqual(by_id["meter.voltage"]["value"], 123.0)
            self.assertEqual(by_id["meter.voltage"]["quality"], "good")
            self.assertIs(by_id["meter.breaker"]["value"], True)
            self.assertEqual(by_id["meter.breaker"]["quality"], "good")
            self.assertIsNone(by_id["offline.voltage"]["value"])
            self.assertIn(by_id["offline.voltage"]["quality"],
                          ("unavailable", "communication_lost"))
            devices_page = await request(port, TOKEN, "devices.page",
                                         {"offset": 0, "limit": 2})
            state = {entry["device_id"]: entry for entry in devices_page["items"]}
            self.assertEqual(state["healthy"]["state"], "online")
            self.assertGreaterEqual(state["healthy"]["reads_ok"], 2)
            self.assertIn(state["dead"]["state"], ("connecting", "offline"))
            self.assertIn((1, bytes((3, 0, 10, 0, 2))), self.slave.requests)
            self.assertIn((1, bytes((1, 0, 5, 0, 1))), self.slave.requests)
            self.assertIn("devices.page", (await request(port, TOKEN, "hello"))["capabilities"])
            # Adapter never drops all connections when a separate IED is down.
            initial_rx = state["healthy"]["reads_ok"]
            await asyncio.sleep(.25)
            updated = await request(port, TOKEN, "devices.page",
                                    {"offset": 0, "limit": 2})
            self.assertGreater(updated["items"][0]["reads_ok"], initial_rx)
            # Mutation is not a secret backdoor through the monitoring IPC.
            with self.assertRaisesRegex(IPCRemoteError, "OPERATION_NOT_SUPPORTED"):
                await request(port, TOKEN, "device.write", {"id": "healthy"})
        finally:
            await server.stop()
            await runtime.stop()

    async def test_viewport_updates_follow_actual_register_changes(self):
        dev = DeviceConfig("a", "m", "IED", 1, "127.0.0.1",
                           self.slave.port, 60, 180)
        runtime = build_modbus_runtime(make_instance(), [dev],
                                       [make_signal("a.voltage", "a")])
        server = LocalReadOnlyServer(runtime, token=TOKEN)
        await runtime.start()
        port = await server.start()
        try:
            await self._wait_for(lambda: runtime.health()["frames_generated"] >= 1)
            session = ViewportSession(0, 1)
            first = await session.refresh(port, TOKEN)
            self.assertTrue(first["full_snapshot"])
            self.assertEqual(first["items"][0]["value"], 123.0)
            self.slave.float_value = 226.25
            await self._wait_for(lambda: runtime._latest["a.voltage"].value == 226.25)
            changed = await session.refresh(port, TOKEN)
            self.assertFalse(changed["full_snapshot"])
            self.assertEqual(changed["items"][0]["value"], 226.25)
            self.assertEqual(changed["items"][0]["quality"], "good")
            # A closed Studio client does not control adapter sockets.
            initial = runtime.health()["frames_generated"]
            await asyncio.sleep(.15)
            self.assertGreater(runtime.health()["frames_generated"], initial)
        finally:
            await server.stop()
            await runtime.stop()

    async def test_two_real_modbus_instance_pollers_share_one_fleet_view(self):
        from arpy_gateway.runtime.service.fleet import RuntimeFleet
        instances = []
        for iid in ("meter_a", "meter_b"):
            inst = ProtocolInstance(iid, iid, "modbus_tcp",
                                    InstanceRole.CLIENT, True, {})
            device = DeviceConfig(iid + ":ied", iid, "Meter", 1,
                                  "127.0.0.1", self.slave.port, 60, 180)
            signal = ProtocolSignal(iid + ":voltage", iid, "Voltage",
                                    SignalFamily.ANALOG, ValueType.FLOAT32,
                                    SignalDirection.INBOUND, True, "V",
                                    {"device_id": device.id, "register_table": "holding",
                                     "native_address": 10})
            instances.append(build_modbus_runtime(inst, (device,), (signal,)))
        fleet = RuntimeFleet(instances)
        server = LocalReadOnlyServer(fleet, token=TOKEN)
        await fleet.start()
        port = await server.start()
        try:
            await self._wait_for(lambda: fleet.health()["frames_generated"] >= 2)
            baseline = await request(port, TOKEN, "signals.page",
                                     {"offset": 0, "limit": 2})
            self.assertEqual(baseline["total"], 2)
            self.assertEqual([r["value"] for r in baseline["items"]],
                             [123.0, 123.0])
            self.slave.float_value = 87.75
            await self._wait_for(
                lambda: fleet.page(offset=0, limit=2)["items"][0]["value"] == 87.75
                and fleet.page(offset=0, limit=2)["items"][1]["value"] == 87.75)
            delta = await request(port, TOKEN, "signals.delta", {
                "generation": baseline["generation"], "cursor": baseline["cursor"],
                "offset": 0, "limit": 2})
            self.assertEqual([r["value"] for r in delta["items"]], [87.75, 87.75])
            states = await request(port, TOKEN, "devices.page",
                                   {"offset": 0, "limit": 2})
            self.assertEqual([r["instance_id"] for r in states["items"]],
                             ["meter_a", "meter_b"])
            self.assertTrue(all(r["state"] == "online" for r in states["items"]))
        finally:
            await server.stop()
            await fleet.stop()

    async def test_daemon_process_survives_independent_monitor_clients(self):
        """Real loopback Modbus slave and daemon must be distinct OS processes."""
        env = os.environ.copy()
        env["ARPY_IPC_TEST_TOKEN"] = TOKEN
        env["PYTHONPATH"] = str(Path(__file__).resolve().parents[2] / "src")
        child = r'''import asyncio, os, sys, json
from arpy_gateway.domain.models import ProtocolInstance, ProtocolSignal, InstanceRole, SignalFamily, ValueType, SignalDirection
from arpy_gateway.adapters.modbus import DeviceConfig
from arpy_gateway.runtime.service.modbus_bridge import build_modbus_runtime
from arpy_gateway.runtime.service.runner import serve_runtime

async def main():
    instance = ProtocolInstance("m", "Test", "modbus_tcp", InstanceRole.CLIENT, True, {})
    dev = DeviceConfig("device", "m", "Live TCP", 1, "127.0.0.1", int(sys.argv[1]), 60, 180)
    sig = ProtocolSignal("measured", "m", "Measured Voltage", SignalFamily.ANALOG,
                         ValueType.FLOAT32, SignalDirection.INBOUND, True, "V",
                         {"device_id":"device","register_table":"holding","native_address":10})
    runtime = build_modbus_runtime(instance, (dev,), (sig,))
    await serve_runtime(runtime, token=os.environ["ARPY_IPC_TEST_TOKEN"],
                        stop=asyncio.Event(), ready=lambda p: print(json.dumps({"port":p}),flush=True))
asyncio.run(main())'''
        proc = await asyncio.create_subprocess_exec(
            sys.executable, "-u", "-c", child, str(self.slave.port),
            env=env, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE)
        try:
            line = await asyncio.wait_for(proc.stdout.readline(), 10)
            if not line:
                details = await asyncio.wait_for(proc.stderr.read(), 2)
                self.fail("Modbus child daemon startup failed: " + details.decode()[:600])
            import json
            port = json.loads(line)["port"]
            deadline = asyncio.get_running_loop().time() + 4
            observed = None
            while asyncio.get_running_loop().time() < deadline:
                observed = await request(port, TOKEN, "signals.page",
                                         {"offset": 0, "limit": 1})
                if observed["items"][0]["quality"] == "good":
                    break
                await asyncio.sleep(.04)
            self.assertEqual(observed["items"][0]["value"], 123.0)
            before = await request(port, TOKEN, "health")
            await asyncio.sleep(.22)  # no monitoring client attached
            after = await request(port, TOKEN, "health")
            self.assertGreater(after["frames_generated"], before["frames_generated"])
            self.assertIsNone(proc.returncode)
            self.assertGreaterEqual(len(self.slave.requests), 2)
        finally:
            if proc.returncode is None:
                proc.terminate()
                try:
                    await asyncio.wait_for(proc.wait(), 5)
                except asyncio.TimeoutError:
                    proc.kill()
                    await proc.wait()
