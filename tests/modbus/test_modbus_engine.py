"""Pure unit + real loopback Modbus TCP wire tests (no Qt, no field hardware)."""
from __future__ import annotations
import asyncio
import math
import struct
import unittest

from arpy_gateway.domain.models import (ProtocolInstance, ProtocolSignal,
    InstanceRole, SignalFamily, SignalDirection, ValueType, SignalSample,
    Quality, SignalOrigin)
from arpy_gateway.adapters.modbus import (DeviceConfig, ModbusClientAdapter,
    ModbusTcpServerAdapter, compile_reads, parse_signal, decode_value)
from arpy_gateway.adapters.modbus.configuration import validate_serial
from arpy_gateway.adapters.modbus.transport import ModbusReadError
from datetime import datetime, timezone


def instance(role=InstanceRole.CLIENT, transport="modbus_tcp", enabled=True, **settings):
    return ProtocolInstance("mb-main", "Modbus Channel", transport, role, enabled,
        settings or ({"serial_port": "loop://", "baudrate": 9600, "parity": "N", "stopbits": 1}
                     if transport == "modbus_rtu" else {}))


def sig(id, address, dtype=ValueType.UINT16, table="holding", device="meter01",
        direction=SignalDirection.INBOUND, **params):
    return ProtocolSignal(id, "mb-main", id, SignalFamily.BINARY if dtype is ValueType.BOOL
        else SignalFamily.ANALOG, dtype, direction, True, "",
        {"device_id": device, "register_table": table, "native_address": address, **params})


def device(id="meter01", unit=1, poll_ms=80):
    return DeviceConfig(id, "mb-main", id, unit, "127.0.0.1", 502, poll_ms, 100)


class FakeClient:
    def __init__(self, *, fail_connect=False, response=None):
        self.fail_connect, self.response = fail_connect, response
        self.calls = []
        self.closed = False
        self.connected = False

    async def connect(self):
        self.connected = not self.fail_connect
        if self.fail_connect:
            raise ConnectionError("test lost")

    async def read(self, req, unit):
        self.calls.append((unit, req.function, req.address, req.count))
        if self.response is not None:
            return self.response(req, unit)
        return tuple([1 if req.function in (1, 2) else 123] * req.count)

    def close(self):
        self.closed = True


class FakeFactory:
    def __init__(self):
        self.clients = []
        self.tcp_connects = []
        self.rtu_connects = 0
    def tcp(self, d):
        self.tcp_connects.append(d.id)
        cli = FakeClient()
        self.clients.append(cli)
        return cli
    def serial(self, instance, timeout):
        self.rtu_connects += 1
        cli = FakeClient()
        self.clients.append(cli)
        return cli


class AdapterUnitTests(unittest.IsolatedAsyncioTestCase):
    def test_typed_decode_swap_scale_and_width(self):
        p = parse_signal(sig("voltage", 0, ValueType.FLOAT32), {"meter01"})
        self.assertAlmostEqual(decode_value(p, [0x42F6, 0x0000]), 123.0)
        p2 = parse_signal(sig("float_swapped", 0, ValueType.FLOAT32,
                               byte_order="little", word_order="little"), {"meter01"})
        self.assertAlmostEqual(decode_value(p2, [0x0000, 0xF642]), 123.0)
        p3 = parse_signal(sig("negative", 10, ValueType.INT16,
                              scale=0.1, offset=5.0), {"meter01"})
        self.assertAlmostEqual(decode_value(p3, [65526]), 4.0)
        p4 = parse_signal(sig("u64", 11, ValueType.UINT64), {"meter01"})
        self.assertEqual(decode_value(p4, [0xFFFF, 0xFFFF, 0xFFFF, 0xFFFF]), 2**64-1)
        with self.assertRaises(ValueError):
            decode_value(p, [0x7f80, 0x0000])  # IEEE +infinity

    def test_address_native_fc_and_grouping_does_not_cross_gaps(self):
        points = [parse_signal(x, {"meter01"}) for x in (
            sig("a", 0, ValueType.FLOAT32), sig("b", 2),
            sig("c", 4), sig("d", 20),
            sig("coil", 2, ValueType.BOOL, "coil"),
            sig("input", 0, ValueType.UINT16, "input"))]
        plan = compile_reads(points)
        self.assertEqual([(x.function, x.address, x.count) for x in plan],
                         [(1, 2, 1), (3, 0, 3), (3, 4, 1), (3, 20, 1), (4, 0, 1)])
        bulk = [parse_signal(sig(f"x{i}", i), {"meter01"}) for i in range(250)]
        plans = compile_reads(bulk)
        self.assertEqual([r.count for r in plans], [125, 125])
        self.assertEqual(plans[-1].address, 125)

    def test_reject_invalid_parameters_before_connections(self):
        with self.assertRaises(ValueError):
            parse_signal(sig("bad", 65535, ValueType.FLOAT32), {"meter01"})
        with self.assertRaises(ValueError):
            parse_signal(sig("wrong", 0, device="missing"), {"meter01"})
        with self.assertRaises(ValueError):
            parse_signal(sig("badfc", 0, ValueType.FLOAT32, "coil"), {"meter01"})
        with self.assertRaises(ValueError):
            parse_signal(sig("wrong_count", 0, ValueType.FLOAT32, register_count=1), {"meter01"})
        with self.assertRaises(ValueError):
            DeviceConfig("slave0", "mb-main", "bad", 0, "127.0.0.1").validate(instance())
        self.assertEqual(validate_serial(instance(transport="modbus_rtu"))["baudrate"], 9600)

    async def test_many_tcp_devices_are_isolated_and_offline_instance_does_not_connect(self):
        f = FakeFactory()
        a = ModbusClientAdapter([device("meter01"), device("meter02", 2)], factory=f)
        signals = (sig("voltage1", 0), sig("voltage2", 1, device="meter02"))
        await a.start(instance(enabled=False), signals)
        self.assertEqual(len(f.tcp_connects), 0)
        await a.stop()
        await a.start(instance(), signals)
        await asyncio.sleep(0.15)
        self.assertEqual(set(f.tcp_connects), {"meter01", "meter02"})
        snap = a.latest_snapshot()
        self.assertEqual(snap["voltage1"].value, 123)
        self.assertEqual(snap["voltage2"].quality, Quality.GOOD)
        self.assertGreaterEqual(a.device_statuses()[0].reads_ok, 1)
        with self.assertRaises(PermissionError):
            await a.publish(signals[0], snap["voltage1"])
        await a.stop()
        self.assertTrue(all(client.closed for client in f.clients))

    async def test_rtu_bus_has_one_serial_client_and_serialized_requests(self):
        f = FakeFactory()
        d1 = DeviceConfig("meter01", "mb-main", "Slave1", 1, "", poll_ms=70)
        d2 = DeviceConfig("meter02", "mb-main", "Slave2", 2, "", poll_ms=70)
        a = ModbusClientAdapter([d1, d2], factory=f)
        await a.start(instance(transport="modbus_rtu"),
                      (sig("a", 0), sig("b", 0, device="meter02")))
        await asyncio.sleep(0.1)
        self.assertEqual(f.rtu_connects, 1)
        self.assertEqual(f.clients[0].calls[:2], [(1, 3, 0, 1), (2, 3, 0, 1)])
        await a.stop()

    async def test_read_exception_is_invalid_not_good_and_bad_link_never_fabricates_zero(self):
        f = FakeFactory()
        a = ModbusClientAdapter([device()], factory=f)
        s = sig("x", 0)
        await a.start(instance(), (s,))
        await asyncio.sleep(0.08)
        self.assertEqual(a.latest_snapshot()["x"].quality, Quality.GOOD)
        f.clients[0].response = lambda req, unit: (_ for _ in ()).throw(ModbusReadError("illegal address"))
        await asyncio.sleep(0.13)
        bad = a.latest_snapshot()["x"]
        self.assertIsNone(bad.value)
        self.assertEqual(bad.quality, Quality.INVALID)
        await a.stop()


class ServerWireTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.s = ModbusTcpServerAdapter()
        self.signal = sig("voltage", 10, ValueType.FLOAT32, direction=SignalDirection.OUTBOUND)
        self.bit = sig("running", 17, ValueType.BOOL, "coil", direction=SignalDirection.OUTBOUND)
        await self.s.start(instance(InstanceRole.SERVER, bind="127.0.0.1", port=0),
                           (self.signal, self.bit))
        self.host, self.port = self.s.listening_address[:2]
        self.reader, self.writer = await asyncio.open_connection(self.host, self.port)

    async def asyncTearDown(self):
        self.writer.close()
        await self.writer.wait_closed()
        await self.s.stop()

    async def request(self, function, start=10, qty=2, unit=1, payload=None, tid=7):
        body = payload if payload is not None else struct.pack(">HH", start, qty)
        pdu = bytes([function]) + body
        self.writer.write(struct.pack(">HHHB", tid, 0, len(pdu)+1, unit) + pdu)
        await self.writer.drain()
        head = await asyncio.wait_for(self.reader.readexactly(7), 2)
        txn, proto, length, r_unit = struct.unpack(">HHHB", head)
        data = await asyncio.wait_for(self.reader.readexactly(length-1), 2)
        self.assertEqual((txn, proto, r_unit), (tid, 0, unit))
        return data

    async def test_real_loopback_tcp_0_based_registers_no_fake_good(self):
        self.assertEqual(await self.request(3), bytes([0x83, 4]))
        sample = SignalSample("voltage", 123.0, Quality.GOOD, SignalOrigin.DERIVED,
                              datetime.now(timezone.utc), 1)
        await self.s.publish(self.signal, sample)
        self.assertEqual(await self.request(3), bytes([3, 4, 0x42, 0xF6, 0, 0]))
        self.assertEqual(await self.request(4), bytes([0x84, 2]))
        self.assertEqual(await self.request(3, start=11, qty=3), bytes([0x83, 2]))
        self.assertEqual(await self.request(6, payload=struct.pack(">HH", 10, 55)), bytes([0x86, 1]))
        self.assertEqual(await self.request(3), bytes([3, 4, 0x42, 0xF6, 0, 0]))
        invalid = SignalSample("voltage", None, Quality.COMMUNICATION_LOST, SignalOrigin.PHYSICAL,
                               datetime.now(timezone.utc), 2)
        await self.s.publish(self.signal, invalid)
        self.assertEqual(await self.request(3), bytes([0x83, 4]))

    async def test_coil_bit_packing_and_framing_bounds(self):
        sample = SignalSample("running", True, Quality.GOOD, SignalOrigin.DERIVED,
                              datetime.now(timezone.utc), 1)
        await self.s.publish(self.bit, sample)
        self.assertEqual(await self.request(1, start=17, qty=1), bytes([1, 1, 1]))
        self.assertEqual(await self.request(1, start=17, qty=2001), bytes([0x81, 3]))
        self.assertEqual(await self.request(3, start=10, qty=126), bytes([0x83, 3]))
        self.assertEqual(await self.request(99), bytes([0xe3, 1]))

    async def test_stop_releases_listen_port(self):
        await self.s.stop()
        self.assertIsNone(self.s.listening_address)


if __name__ == "__main__":
    unittest.main()
