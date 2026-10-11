"""Protocol-independent headless IPC and process lifecycle tests."""
import asyncio
import json
import os
from pathlib import Path
import struct
import subprocess
import sys
import time
import unittest
from datetime import datetime, timezone
from uuid import uuid4

from arpy_gateway.ipc.client import request, IPCRemoteError
from arpy_gateway.ipc.framing import MAX_FRAME_BYTES, receive, send
from arpy_gateway.ipc.server import LocalReadOnlyServer
from arpy_gateway.runtime.service.fixture import FixtureRuntime

TOKEN = "ipc-test-credential-0123456789abcdef-012345"


def envelope(method="health"):
    return {"api_version": "1.0", "message_id": str(uuid4()), "kind": "request",
            "sent_at": datetime.now(timezone.utc).isoformat(),
            "correlation_id": "",
            "payload": {"auth": TOKEN, "method": method, "params": {}}}


class LocalIPCTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.runtime = FixtureRuntime(period=.015, num_signals=12)
        await self.runtime.start()
        self.server = LocalReadOnlyServer(self.runtime, token=TOKEN)
        self.port = await self.server.start()

    async def asyncTearDown(self):
        await self.server.stop()
        await self.runtime.stop()

    async def send_raw(self, msg):
        r, w = await asyncio.open_connection("127.0.0.1", self.port)
        try:
            await send(w, msg)
            return await receive(r)
        finally:
            w.close()
            await w.wait_closed()

    async def test_hello_health_paging_and_client_disconnect(self):
        h = await request(self.port, TOKEN, "hello")
        self.assertEqual(h["api_version"], "1.0")
        self.assertFalse(h["mutable_operations"])
        self.assertEqual(h["source"], "DETERMINISTIC_FIXTURE_ONLY")
        a = await request(self.port, TOKEN, "health")
        await asyncio.sleep(.13)
        b = await request(self.port, TOKEN, "health")
        self.assertEqual(b["started_utc"], a["started_utc"])
        self.assertGreater(b["frames_generated"], a["frames_generated"])
        self.assertIsNone(b["active_revision"])
        page = await request(self.port, TOKEN, "signals.page", {"offset": 0, "limit": 4})
        self.assertEqual((page["total"], len(page["items"])), (12, 4))
        self.assertEqual(page["source"], "DETERMINISTIC_FIXTURE_ONLY")
        self.assertIn(page["items"][0]["quality"], ("good", "unavailable"))
        self.assertFalse(self.server._clients)
        # This test module may also run in an OPTIONAL protocol-interoperability
        # process that has loaded PyModbus. Headless module independence itself
        # is tested in a separate clean-interpreter CI step.
        self.assertFalse(any(x.startswith(("PySide6", "PyQt"))
                             for x in sys.modules))

    async def test_fail_closed_auth_mutations_and_page_bounds(self):
        with self.assertRaisesRegex(IPCRemoteError, "UNAUTHORIZED"):
            await request(self.port, TOKEN + "bad", "health")
        for method in ("apply", "device.write", "simulation.enable", "service.stop"):
            with self.subTest(method=method):
                with self.assertRaisesRegex(IPCRemoteError, "OPERATION_NOT_SUPPORTED"):
                    await request(self.port, TOKEN, method)
        for params in ({"offset": -1, "limit": 2}, {"offset": 0, "limit": 129},
                       {"offset": True, "limit": 1},
                       {"offset": 0, "limit": 1, "extra": 9}):
            with self.subTest(params=params):
                with self.assertRaisesRegex(IPCRemoteError, "BAD_PARAMETERS"):
                    await request(self.port, TOKEN, "signals.page", params)
        incompatible = envelope()
        incompatible["api_version"] = "2.0"
        result = await self.send_raw(incompatible)
        self.assertEqual(result["payload"]["code"], "UNSUPPORTED_API_VERSION")
        self.assertEqual(result["correlation_id"], incompatible["message_id"])
        incompatible.pop("sent_at")
        self.assertEqual((await self.send_raw(incompatible))["payload"]["code"],
                         "MALFORMED_ENVELOPE")

    async def test_reject_oversized_and_duplicate_key_json(self):
        reader, writer = await asyncio.open_connection("127.0.0.1", self.port)
        try:
            writer.write(struct.pack(">I", MAX_FRAME_BYTES + 100))
            await writer.drain()
            self.assertEqual((await receive(reader))["payload"]["code"], "INVALID_FRAME")
        finally:
            writer.close()
            await writer.wait_closed()
        reader, writer = await asyncio.open_connection("127.0.0.1", self.port)
        try:
            duplicate = b'{"api_version":"1.0","api_version":"2.0"}'
            writer.write(struct.pack(">I", len(duplicate)) + duplicate)
            await writer.drain()
            self.assertEqual((await receive(reader))["payload"]["code"], "INVALID_FRAME")
        finally:
            writer.close()
            await writer.wait_closed()


class IndependentProcessTests(unittest.TestCase):
    def test_client_process_exit_does_not_end_daemon(self):
        env = os.environ.copy()
        env["ARPY_IPC_TEST_TOKEN"] = TOKEN
        env["PYTHONPATH"] = str(Path(__file__).resolve().parents[2] / "src")
        daemon = subprocess.Popen(
            [sys.executable, "-m", "arpy_gateway.runtime.service", "--period-ms", "15"],
            env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
                line = executor.submit(daemon.stdout.readline).result(timeout=8)
            port = json.loads(line)["port"]
            code = ("import asyncio,os,sys;"
                    "from arpy_gateway.ipc.client import request;"
                    "x=asyncio.run(request(int(sys.argv[1]),os.environ['ARPY_IPC_TEST_TOKEN'],'health'));"
                    "print(x['frames_generated'])")
            def run_client():
                result = subprocess.run([sys.executable, "-c", code, str(port)],
                                        env=env, capture_output=True, text=True,
                                        timeout=8, check=True)
                return int(result.stdout.strip())
            a = run_client()
            time.sleep(.2)
            b = run_client()
            self.assertGreater(b, a)
            self.assertIsNone(daemon.poll())
        finally:
            daemon.terminate()
            try:
                daemon.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                daemon.kill()
                daemon.communicate(timeout=5)

    def test_missing_auth_fails_before_listener(self):
        env = os.environ.copy()
        env.pop("ARPY_IPC_TEST_TOKEN", None)
        env["PYTHONPATH"] = str(Path(__file__).resolve().parents[2] / "src")
        proc = subprocess.run([sys.executable, "-m", "arpy_gateway.runtime.service"],
                              env=env, capture_output=True, text=True, timeout=8)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("refusing unsecured runtime", proc.stderr)
