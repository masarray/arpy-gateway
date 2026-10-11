"""Independent external PyModbus 3.15 TCP client → ARPY read-only TCP server."""
import asyncio
import unittest
from datetime import datetime, timezone
from arpy_gateway.adapters.modbus.server import ModbusTcpServerAdapter
from arpy_gateway.domain.models import (ProtocolInstance,ProtocolSignal,InstanceRole,SignalFamily,
 SignalDirection,ValueType,SignalSample,Quality,SignalOrigin)

try:
    from pymodbus.client import AsyncModbusTcpClient
except ImportError:
    AsyncModbusTcpClient = None


@unittest.skipUnless(AsyncModbusTcpClient is not None, "Optional pinned PyModbus transport unavailable")
class IndependentTCPInterop(unittest.IsolatedAsyncioTestCase):
    async def test_independent_client_reads_float32_and_rejects_writes(self):
        server = ModbusTcpServerAdapter()
        signal = ProtocolSignal("p1","srv1","Power",SignalFamily.ANALOG,
            ValueType.FLOAT32,SignalDirection.OUTBOUND,True,"kW",
            {"register_table":"holding","native_address":0,"unit_id":1})
        instance = ProtocolInstance("srv1","Read only","modbus_tcp",InstanceRole.SERVER,
                                     True,{"bind":"127.0.0.1","port":0})
        await server.start(instance,(signal,))
        host,port = server.listening_address[:2]
        client = AsyncModbusTcpClient(host,port=port,retries=0,timeout=1.5)
        try:
            await client.connect()
            assert client.connected
            sample = SignalSample("p1",120.5,Quality.GOOD,SignalOrigin.DERIVED,
                                   datetime.now(timezone.utc),1)
            await server.publish(signal,sample)
            result=await client.read_holding_registers(address=0,count=2,device_id=1)
            self.assertFalse(result.isError(),str(result))
            self.assertEqual(list(result.registers),[0x42F1,0])
            write=await client.write_register(address=0,value=1,device_id=1)
            self.assertTrue(write.isError())
            result=await client.read_holding_registers(address=0,count=2,device_id=1)
            self.assertEqual(list(result.registers),[0x42F1,0])
        finally:
            client.close()
            await server.stop()


@unittest.skipUnless(AsyncModbusTcpClient is not None, "Optional pinned PyModbus transport unavailable")
class RealTcpMasterInterop(unittest.IsolatedAsyncioTestCase):
    async def test_arpy_master_polls_independent_tcp_fixture_over_socket(self):
        from arpy_gateway.adapters.modbus import DeviceConfig, ModbusClientAdapter
        captured = []
        async def external_slave(reader,writer):
            try:
                while True:
                    header = await reader.readexactly(7)
                    import struct
                    txn, proto, length, unit = struct.unpack(">HHHB",header)
                    request = await reader.readexactly(length-1)
                    captured.append((unit,request))
                    self.assertEqual((proto,unit), (0,1))
                    # FC03 address=10 count=2 -> 42F60000 = Float32 123.0
                    result = bytes([3,4,0x42,0xF6,0,0])
                    writer.write(struct.pack(">HHHB",txn,0,len(result)+1,unit)+result)
                    await writer.drain()
            except (asyncio.IncompleteReadError, ConnectionError):
                pass
            finally:
                writer.close()
                await writer.wait_closed()
        slave = await asyncio.start_server(external_slave,"127.0.0.1",0)
        port = slave.sockets[0].getsockname()[1]
        inst = ProtocolInstance("master","Master","modbus_tcp",InstanceRole.CLIENT,True,{})
        dev = DeviceConfig("iec-meter","master","IED",1,"127.0.0.1",port,80,200)
        sig = ProtocolSignal("voltage","master","Voltage",SignalFamily.ANALOG,
            ValueType.FLOAT32,SignalDirection.INBOUND,True,"V",
            {"device_id":"iec-meter","register_table":"holding","native_address":10})
        a = ModbusClientAdapter([dev],retry_limit_seconds=1)
        try:
            await a.start(inst,(sig,))
            for _ in range(80):
                if a.latest_snapshot().get("voltage") is not None:
                    if a.latest_snapshot()["voltage"].quality is Quality.GOOD:
                        break
                await asyncio.sleep(.03)
            self.assertIn("voltage",a.latest_snapshot())
            sample = a.latest_snapshot()["voltage"]
            self.assertEqual(sample.quality,Quality.GOOD)
            self.assertEqual(sample.value,123.0)
            self.assertIn((1,bytes([3,0,10,0,2])),captured)
        finally:
            await a.stop()
            slave.close()
            await slave.wait_closed()
