"""Viewport delta, gap recovery and bounded IPC overload regression.

No Studio, protocol hardware, or PyModbus imported. These are the read-only
projection semantics needed for an eventual Qt Value Viewer.
"""
from __future__ import annotations

import asyncio
import unittest

from arpy_gateway.ipc.client import ViewportSession, request
from arpy_gateway.ipc.server import LocalReadOnlyServer
from arpy_gateway.runtime.service.fixture import FixtureRuntime
from arpy_gateway.runtime.service.viewport import ViewportChanges

TOKEN = "viewport-test-credential-0123456789abcdef"


class CursorUnitTests(unittest.TestCase):
    def test_coalesce_visible_changes_and_no_work_when_unmodified(self):
        delta = ViewportChanges(capacity=128)
        initial = delta.changes(generation=delta.generation, cursor=0, offset=8, limit=5)
        self.assertEqual(initial["indices"], ())
        self.assertFalse(initial["resync_required"])
        delta.mark(2)
        delta.mark(9)
        delta.mark(9)
        delta.mark(12)
        delta.mark(13)
        current = delta.changes(generation=delta.generation, cursor=0, offset=8, limit=5)
        self.assertEqual(current["indices"], (9, 12))
        self.assertEqual(current["cursor"], 5)
        same = delta.changes(generation=delta.generation, cursor=5, offset=8, limit=5)
        self.assertEqual(same["indices"], ())
        self.assertFalse(same["resync_required"])

    def test_overflow_generation_restart_and_runtime_invalidation_force_snapshot(self):
        delta = ViewportChanges(capacity=128)
        for n in range(170):
            delta.mark(n % 5)
        self.assertLessEqual(delta.depth, 128)
        self.assertEqual(delta.dropped_events, 42)
        gap = delta.changes(generation=delta.generation, cursor=0, offset=0, limit=5)
        self.assertTrue(gap["resync_required"])
        fresh = delta.changes(generation=delta.generation, cursor=170, offset=0, limit=5)
        self.assertFalse(fresh["resync_required"])
        delta.invalidate()
        self.assertEqual(delta.depth, 0)
        self.assertTrue(delta.changes(generation=delta.generation, cursor=170,
                                      offset=0, limit=5)["resync_required"])
        self.assertFalse(delta.changes(generation=delta.generation, cursor=171,
                                       offset=0, limit=5)["resync_required"])
        self.assertTrue(delta.changes(generation="another-generation", cursor=171,
                                      offset=0, limit=5)["resync_required"])
        self.assertTrue(delta.changes(generation=delta.generation, cursor=172,
                                      offset=0, limit=5)["resync_required"])

    def test_strict_bool_and_invalid_input_rejected(self):
        change = ViewportChanges()
        for kwargs in ({"generation": change.generation, "cursor": True,
                         "offset": 0, "limit": 1},
                       {"generation": change.generation, "cursor": 0,
                        "offset": -1, "limit": 1},
                       {"generation": change.generation, "cursor": 0,
                        "offset": 0, "limit": 129},
                       {"generation": ["bad"], "cursor": 0,
                        "offset": 0, "limit": 1}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                change.changes(**kwargs)


class ViewerIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.runtime = FixtureRuntime(period=0.012, num_signals=8)
        await self.runtime.start()
        self.server = LocalReadOnlyServer(self.runtime, token=TOKEN, max_clients=3)
        self.port = await self.server.start()

    async def asyncTearDown(self):
        await self.server.stop()
        await self.runtime.stop()

    async def test_snapshot_delta_reconnect_and_viewport_switch(self):
        session = ViewportSession(0, 4)
        first = await session.refresh(self.port, TOKEN)
        self.assertEqual(first["state"], "CONNECTED")
        self.assertTrue(first["full_snapshot"])
        self.assertEqual(len(first["items"]), 4)
        cursor = first["cursor"]
        # Source continuously generates samples but Studio does not own it.
        await asyncio.sleep(0.11)
        incremental = await session.refresh(self.port, TOKEN)
        self.assertFalse(incremental["full_snapshot"])
        self.assertGreater(incremental["cursor"], cursor)
        self.assertEqual(len(incremental["items"]), 4)
        self.assertTrue(any(r["sequence"] for r in incremental["items"]))
        # A change in selected IED/viewport must not reuse stale signal rows.
        session.set_viewport(4, 4)
        selected = await session.refresh(self.port, TOKEN)
        self.assertTrue(selected["full_snapshot"])
        self.assertTrue(all(r["signal_id"].endswith(f"{i:06d}")
                            for i, r in zip(range(4, 8), selected["items"])))
        # Simulate runtime listener disappearing: previously Good data must not
        # continue to be presented as fresh in this client read model.
        await self.server.stop()
        disconnected = await session.refresh(self.port, TOKEN)
        self.assertEqual(disconnected["state"], "DISCONNECTED")
        self.assertEqual(disconnected["items"], [])
        self.assertEqual(session.items, {})
        self.server = LocalReadOnlyServer(self.runtime, token=TOKEN)
        self.port = await self.server.start()
        restored = await session.refresh(self.port, TOKEN)
        self.assertTrue(restored["full_snapshot"])
        self.assertEqual(restored["state"], "CONNECTED")

    async def test_resync_when_history_overflowed_and_wrong_generation(self):
        snapshot = await request(self.port, TOKEN, "signals.page",
                                 {"offset": 0, "limit": 4})
        a = await request(self.port, TOKEN, "signals.delta",
                          {"offset": 0, "limit": 4,
                           "generation": snapshot["generation"],
                           "cursor": snapshot["cursor"]})
        self.assertFalse(a["resync_required"])
        wrong = await request(self.port, TOKEN, "signals.delta",
                              {"offset": 0, "limit": 4,
                               "generation": "old-daemon",
                               "cursor": snapshot["cursor"]})
        self.assertTrue(wrong["resync_required"])
        self.runtime._changes.invalidate()
        invalidated = await request(self.port, TOKEN, "signals.delta",
                                    {"offset": 0, "limit": 4,
                                     "generation": snapshot["generation"],
                                     "cursor": snapshot["cursor"]})
        self.assertTrue(invalidated["resync_required"])
        for bad in ({"offset": 0, "limit": 4, "cursor": 0},
                    {"offset": False, "limit": 1, "cursor": 0,
                     "generation": snapshot["generation"]}):
            from arpy_gateway.ipc.client import IPCRemoteError
            with self.subTest(bad=bad), self.assertRaisesRegex(
                    IPCRemoteError, "BAD_PARAMETERS"):
                await request(self.port, TOKEN, "signals.delta", bad)

    async def test_client_admission_never_queues_more_handlers_than_cap(self):
        slow = []
        for _ in range(3):
            reader, writer = await asyncio.open_connection("127.0.0.1", self.port)
            slow.append((reader, writer))
        try:
            # 3 slow readers own 3 hard slots and are waiting for header bytes.
            await asyncio.sleep(0.04)
            self.assertEqual(self.server._active_clients, 3)
            reader, writer = await asyncio.open_connection("127.0.0.1", self.port)
            try:
                await asyncio.sleep(0.04)
                self.assertLessEqual(self.server._active_clients, 3)
                self.assertGreaterEqual(self.server._overloaded, 1)
                self.assertEqual(await asyncio.wait_for(reader.read(1), 1.0), b"")
            finally:
                writer.close()
                await writer.wait_closed()
        finally:
            for _reader, writer in slow:
                writer.close()
                await writer.wait_closed()
        await asyncio.sleep(.05)
        self.assertEqual(self.server._active_clients, 0)
        self.assertEqual((await request(self.port, TOKEN, "health"))["state"], "RUNNING")
