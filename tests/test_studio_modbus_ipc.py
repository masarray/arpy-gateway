"""Real Modbus TCP frame → independent gateway daemon → Studio QTableView.

Test-only subprocesses and a pinned CI source overlay. No field hardware,
no Qt-owned protocol sockets, no deployed configuration or production IPC.
"""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import queue
import subprocess
import sys
import threading
import time
import unittest

from PySide6.QtWidgets import QApplication
from arpy_gateway.studio.v2_window import StudioV2Window

try:
    from arpy_gateway.adapters.modbus import ModbusClientAdapter
except ImportError:
    ModbusClientAdapter = None

TOKEN = "gui-modbus-live-integration-0123456789abcdef"


def subprocess_ready(code, args=(), *, env=None):
    proc = subprocess.Popen(
        [sys.executable, "-u", "-c", code, *map(str, args)],
        env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True)
    out = queue.Queue(maxsize=1)
    threading.Thread(target=lambda: out.put(proc.stdout.readline()),
                     daemon=True).start()
    try:
        line = out.get(timeout=12)
        if not line:
            raise RuntimeError("OS process failed before readiness")
        return proc, json.loads(line)
    except Exception:
        proc.terminate()
        proc.communicate(timeout=5)
        raise


def terminate(proc):
    if proc is None:
        return
    if proc.poll() is None:
        proc.terminate()
    try:
        proc.communicate(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.communicate(timeout=5)


def wait_ui(predicate, seconds=8):
    deadline = time.monotonic() + seconds
    app = QApplication.instance()
    while time.monotonic() < deadline:
        app.processEvents()
        if predicate():
            return
        time.sleep(.035)
    raise AssertionError("Actual Modbus value did not reach Qt viewer")


SLAVE = r'''
import asyncio,json,struct
async def serve(rd,wr):
    try:
        while True:
            tx,pid,length,unit=struct.unpack(">HHHB",await rd.readexactly(7))
            request=await rd.readexactly(length-1)
            if (pid,unit,request)!=(0,1,bytes([3,0,10,0,2])):
                pdu=bytes([request[0]|0x80,2])
            else:
                pdu=bytes([3,4])+struct.pack(">f",228.5)
            wr.write(struct.pack(">HHHB",tx,0,len(pdu)+1,unit)+pdu)
            await wr.drain()
    except (asyncio.IncompleteReadError,OSError,ConnectionError):
        pass
    finally:
        wr.close()
        await wr.wait_closed()
async def main():
    server=await asyncio.start_server(serve,"127.0.0.1",0)
    print(json.dumps({"port":server.sockets[0].getsockname()[1]}),flush=True)
    async with server:
        await server.serve_forever()
asyncio.run(main())
'''

DAEMON = r'''
import asyncio,json,os,sys
from arpy_gateway.domain.models import ProtocolInstance,ProtocolSignal,InstanceRole,SignalFamily,ValueType,SignalDirection
from arpy_gateway.adapters.modbus import DeviceConfig
from arpy_gateway.runtime.service.modbus_bridge import build_modbus_runtime
from arpy_gateway.runtime.service.runner import serve_runtime
async def main():
    instance=ProtocolInstance("master","Modbus Master","modbus_tcp",InstanceRole.CLIENT,True,{})
    device=DeviceConfig("meter:ied:1","master","IED 01",1,"127.0.0.1",int(sys.argv[1]),90,200)
    sig=ProtocolSignal("meter:signal:voltage","master","Line Voltage",SignalFamily.ANALOG,
        ValueType.FLOAT32,SignalDirection.INBOUND,True,"V",
        {"device_id":"meter:ied:1","register_table":"holding","native_address":10})
    runtime=build_modbus_runtime(instance,(device,),(sig,))
    await serve_runtime(runtime,token=os.environ["ARPY_IPC_TEST_TOKEN"],
        stop=asyncio.Event(),port=0,ready=lambda p:print(json.dumps({"port":p}),flush=True))
asyncio.run(main())
'''


@unittest.skipUnless(ModbusClientAdapter is not None,
                     "Real protocol adapter is included in pinned integration CI only")
class ModbusToQtEndToEndTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app=QApplication.instance() or QApplication([])
        cls.slave=None
        cls.daemon=None
        env=os.environ.copy()
        env["ARPY_IPC_TEST_TOKEN"]=TOKEN
        env["PYTHONPATH"]=str(Path(__file__).resolve().parents[1]/"src")
        try:
            cls.slave, slave_info=subprocess_ready(SLAVE,env=env)
            cls.daemon, daemon_info=subprocess_ready(
                DAEMON,(slave_info["port"],),env=env)
            cls.port=daemon_info["port"]
        except Exception:
            terminate(cls.daemon)
            terminate(cls.slave)
            raise

    @classmethod
    def tearDownClass(cls):
        terminate(cls.daemon)
        terminate(cls.slave)

    def test_real_fc03_value_device_health_and_independent_lifecycle(self):
        window=StudioV2Window(runtime_endpoint=(self.port,TOKEN))
        try:
            window.show()
            window.navigation.setCurrentRow(4)
            panel=window.monitor.live_panel
            wait_ui(lambda: (panel.current_state=="CONNECTED" and
                             bool(panel.values.rows) and
                             panel.values.rows[0].get("quality")=="good" and
                             panel.values.rows[0].get("value")==228.5),seconds=10)
            sample=panel.values.rows[0]
            self.assertEqual(sample["signal_id"],"meter:signal:voltage")
            self.assertEqual(sample["origin"],"physical")
            self.assertEqual(sample["value"],228.5)
            self.assertEqual(sample["quality"],"good")
            self.assertTrue(sample.get("observed_at"))
            wait_ui(lambda: bool(panel.devices.rows) and
                    panel.devices.rows[0].get("reads_ok",0)>=1)
            self.assertEqual(panel.devices.rows[0]["device_id"],"meter:ied:1")
            self.assertEqual(panel.devices.rows[0]["state"],"online")
            self.assertIn("TEST IPC",panel.connection.text())
            self.assertNotIn("fixture",panel.page.text().lower())
            from arpy_gateway.ipc.client import request
            before=asyncio.run(request(self.port,TOKEN,"health"))
            window.close()
            self.app.processEvents()
            self.assertIsNone(self.daemon.poll())
            time.sleep(.24)
            after=asyncio.run(request(self.port,TOKEN,"health"))
            self.assertGreater(after["frames_generated"],before["frames_generated"])
        finally:
            window.close()
            self.app.processEvents()
