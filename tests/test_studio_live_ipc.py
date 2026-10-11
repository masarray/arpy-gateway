"""PySide6 live runtime value projection, running against a separate IPC daemon.

No Modbus adapter is imported into Studio. The OS child process is a
deterministic runtime fixture from pinned IPC PR #19; its values are NEVER
presented as real IED data.
"""
from __future__ import annotations

import asyncio
import os
from pathlib import Path
import queue
import subprocess
import sys
import threading
import time
import unittest
import json

from PySide6.QtWidgets import QApplication

from arpy_gateway.studio.v2_window import StudioV2Window

TOKEN = "qt-integration-fixture-0123456789abcdef123"


def wait_ui(predicate, timeout=10):
    app = QApplication.instance()
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        app.processEvents()
        if predicate():
            return
        time.sleep(.025)
    raise AssertionError("Qt runtime viewer failed to reach expected state")


class LiveRuntimeQtTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        env = os.environ.copy()
        env["ARPY_IPC_TEST_TOKEN"] = TOKEN
        env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1] / "src")
        cls.child = subprocess.Popen(
            [sys.executable, "-u", "-m", "arpy_gateway.runtime.service",
             "--period-ms", "20"],
            env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        output = queue.Queue(maxsize=1)
        threading.Thread(target=lambda: output.put(cls.child.stdout.readline()),
                         daemon=True).start()
        try:
            line = output.get(timeout=10)
            if not line:
                raise AssertionError("Runtime child exited before IPC listener started")
            cls.port = json.loads(line)["port"]
        except Exception:
            cls.child.terminate()
            cls.child.communicate(timeout=5)
            raise

    @classmethod
    def tearDownClass(cls):
        if cls.child.poll() is None:
            cls.child.terminate()
        try:
            cls.child.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            cls.child.kill()
            cls.child.communicate(timeout=5)

    def setUp(self):
        self.window = StudioV2Window(runtime_endpoint=(self.port, TOKEN))
        self.window.show()
        self.window.navigation.setCurrentRow(4)
        wait_ui(lambda: (self.window.monitor.live_panel.current_state == "CONNECTED"
                         and len(self.window.monitor.live_panel.values.rows) > 0))

    def tearDown(self):
        self.window.close()
        self.app.processEvents()

    def test_live_viewer_attaches_to_runtime_values_and_coalesces_cursor(self):
        panel = self.window.monitor.live_panel
        self.assertEqual(panel.values.rows[0]["signal_id"], "fixture:signal:000000")
        self.assertIn(panel.values.rows[0]["quality"], ("good", "unavailable"))
        self.assertTrue(all(row["signal_id"].startswith("fixture:")
                            for row in panel.values.rows))
        self.assertEqual(panel.values.rowCount(), 8)
        self.assertFalse(panel.next.isEnabled() and panel._last_page_size == 128)
        self.assertIn("Global", panel.info.text())
        seq = sum(row["sequence"] for row in panel.values.rows)
        wait_ui(lambda: sum(row["sequence"] for row in panel.values.rows) > seq)
        # Qt view displays only service-provided IDs; selecting a demo IED must
        # not attribute those service values to unrelated mock meter signals.
        index = self.window.tree_model.find("meter01:ied:2")
        if index.isValid():
            self.window.tree.setCurrentIndex(index)
        self.assertIn("NOT a signal filter",
                      self.window.monitor.context_label.text())
        self.assertEqual(panel.values.rows[0]["signal_id"], "fixture:signal:000000")

    def test_closing_studio_does_not_stop_headless_daemon(self):
        from arpy_gateway.ipc.client import request
        first = asyncio.run(request(self.port, TOKEN, "health"))
        self.window.close()
        self.app.processEvents()
        self.assertIsNone(self.child.poll(), "GUI closing unexpectedly killed the service")
        time.sleep(.18)
        second = asyncio.run(request(self.port, TOKEN, "health"))
        self.assertGreater(second["frames_generated"], first["frames_generated"])

    def test_switching_workspace_pauses_gui_polling_not_runtime(self):
        panel = self.window.monitor.live_panel
        self.assertTrue(panel.active)
        self.window.navigation.setCurrentRow(2)
        wait_ui(lambda: not panel.active)
        self.assertFalse(panel._timer.isActive())
        from arpy_gateway.ipc.client import request
        a = asyncio.run(request(self.port, TOKEN, "health"))
        time.sleep(.16)
        b = asyncio.run(request(self.port, TOKEN, "health"))
        self.assertGreater(b["frames_generated"], a["frames_generated"])
        self.window.navigation.setCurrentRow(4)
        wait_ui(lambda: panel.active and panel.current_state == "CONNECTED")


class LiveDisconnectQtTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_disconnected_ipc_never_preserves_previous_good_values(self):
        # A deliberately unused loopback port is sufficient: no fake Good.
        import socket
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        window = StudioV2Window(runtime_endpoint=(port, TOKEN))
        try:
            window.show()
            window.navigation.setCurrentRow(4)
            panel = window.monitor.live_panel
            wait_ui(lambda: panel.current_state == "DISCONNECTED"
                    and panel.page.text() not in ("No runtime signal snapshot",))
            self.assertEqual(panel.values.rowCount(), 0)
            self.assertFalse(panel.busy)
            self.assertIn("DISCONNECTED", panel.connection.text())
        finally:
            window.close()
            self.app.processEvents()
