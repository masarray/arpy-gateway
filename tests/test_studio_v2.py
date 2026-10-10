"""Issue #4 v2 gateway-context GUI acceptance, without live protocol I/O."""
import os
import time
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication
from PySide6.QtCore import Qt
from arpy_gateway.studio.demo import DemoProvider
from arpy_gateway.studio.project_view import (
    EngineeringView, nth_numeric_address)
from arpy_gateway.studio.explorer import ProtocolExplorer
from arpy_gateway.studio.v2_window import StudioV2Window
from arpy_gateway.studio.design_tokens import LIGHT, DARK, apply_theme

APP = QApplication.instance() or QApplication([])


def await_ui(predicate, seconds=12):
    limit = time.monotonic() + seconds
    while time.monotonic() < limit:
        APP.processEvents()
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError("Worker-driven GUI did not finish in time")


class ProtocolTopologyTests(unittest.TestCase):
    def setUp(self):
        self.demo = DemoProvider()
        self.project = EngineeringView(self.demo)

    def test_distinct_iec104_master_peers_and_asdu_stations(self):
        peers = self.project.points("scada104")
        stations = self.project.stations("scada104")
        self.assertEqual(len(peers), 2)
        self.assertTrue(all(p.kind == "allowed_master" for p in peers))
        self.assertEqual(len(stations), 1)
        self.assertEqual(stations[0].address, 1)
        self.assertNotIn(stations[0].id, {p.id for p in peers})

    def test_explorer_shows_only_enabled_protocols_then_opt_in_disabled(self):
        e = ProtocolExplorer(self.project)
        e.rebuild(1)
        self.assertTrue(e.find("peers:meter01").isValid())
        self.assertTrue(e.find("stations:scada104").isValid())
        self.assertTrue(e.find("peers:scada104").isValid())
        self.assertFalse(e.find("protocol:mirror").isValid())
        e.rebuild(1, include_disabled=True)
        self.assertTrue(e.find("protocol:mirror").isValid())
        e.rebuild(2)
        self.assertTrue(e.find("meter01:ied:1").isValid())
        self.assertTrue(e.find("scada104:ca:1").isValid())
        self.assertFalse(e.find("scada104:peer:1").isValid())

    def test_ied_selection_filters_by_identity_not_row_name(self):
        first = self.project.source("meter01", "meter01:ied:1")
        second = self.project.source("meter01", "meter01:ied:2")
        self.assertEqual(first.row_count(), 500)
        self.assertEqual(second.row_count(), 500)
        self.assertEqual(first.page("meter01", 0, 1)[0][0], "meter01:signal:0000001")
        self.assertEqual(second.page("meter01", 0, 1)[0][0], "meter01:signal:0000501")
        sid = self.project.append_signal("meter01", "meter01:ied:2", "New Meter 02 Signal")
        self.assertEqual(second.row_count(), 501)
        self.assertEqual(first.row_count(), 500)
        self.assertEqual(self.project.owner_for_signal_id("meter01", sid), "meter01:ied:2")

    def test_assign_from_catalog_creates_destination_signals_and_mapping(self):
        dest = self.project.stations("scada104")[0].id
        signals = self.project.assign_catalog("meter01", "meter01:ied:2",
                                              0, "scada104", dest, 2001, 25, 13)
        self.assertEqual(len(signals), 25)
        self.assertEqual(self.demo.mappings_count(), 25)
        self.assertEqual(self.project.owner_for_signal_id("scada104", signals[0]), dest)
        mapping = self.demo.mapping_page(0, 1)[0]
        self.assertEqual(mapping.source_signal_id, "meter01:signal:0000501")
        self.assertEqual(mapping.destination_signal_id, signals[0])
        row = self.demo.page("scada104", 1000, 1)[0]
        keys = [col.key for col in self.demo.fields("scada104")]
        self.assertEqual(row[keys.index("ioa")+2], 2001)
        self.assertEqual(row[keys.index("ti")+2], "13")
        with self.assertRaises(ValueError):
            self.project.assign_catalog("meter01", "meter01:ied:1", 0,
                                        "scada104", dest, 2001, 1, 13)
        self.assertEqual(self.demo.mappings_count(), 25)
        with self.assertRaises(ValueError):
            self.project.assign_catalog("meter01", "", 0, "scada104", dest, 2400, 1, 45)

    def test_modbus_native_address_sort_uses_numeric_order_without_materializing(self):
        low, high = 29900, 30125
        sorted_indexes = sorted(range(low, high), key=lambda i: (i % 30000, i//30000))
        self.assertEqual([nth_numeric_address(low, high, i) for i in range(high-low)],
                         sorted_indexes)
        self.demo.resize_demo(500_000)
        second = self.project.source("meter01", "meter01:ied:2")
        self.assertEqual(second.row_count(), 250_000)
        t = time.monotonic()
        records = second.page("meter01", 249_744, 256,
                              sort_key="native_address")
        self.assertEqual(len(records), 256)
        self.assertLess(time.monotonic() - t, 2.5)
        self.assertFalse(hasattr(self.demo, "_signals"))


class StudioV2UiTests(unittest.TestCase):
    def setUp(self):
        self.win = StudioV2Window()
        self.win.show()
        await_ui(lambda: self.win.navigation.currentRow() == 0)

    def tearDown(self):
        self.win.close()
        APP.processEvents()

    def test_six_workspace_explorer_and_global_theme(self):
        self.assertEqual(self.win.navigation.count(), 6)
        self.assertEqual(self.win.pages.count(), 6)
        self.win.navigation.setCurrentRow(1)
        idx = self.win.tree_model.find("peers:meter01")
        self.assertTrue(idx.isValid())
        self.win.tree.setCurrentIndex(idx)
        self.assertEqual(self.win.config.peers_model.rowCount(), 2)
        idx = self.win.tree_model.find("stations:scada104")
        self.win.tree.setCurrentIndex(idx)
        self.assertEqual(self.win.config.stations_model.rowCount(), 1)
        self.win.theme.setCurrentIndex(1)
        self.assertIn(DARK.surface, APP.styleSheet())
        self.win.theme.setCurrentIndex(0)
        self.assertIn(LIGHT.canvas, APP.styleSheet())

    def test_protocol_signal_editor_follows_selected_device(self):
        self.win.navigation.setCurrentRow(2)
        idx = self.win.tree_model.find("meter01:ied:2")
        self.win.tree.setCurrentIndex(idx)
        await_ui(lambda: self.win.signals.model.rowCount() == 500)
        m = self.win.signals.model
        await_ui(lambda: 0 in m._cached)
        self.assertEqual(m.data(m.index(0, 0), Qt.ItemDataRole.ToolTipRole),
                         "meter01:signal:0000501")
        idx = self.win.tree_model.find("scada104:ca:1")
        self.win.tree.setCurrentIndex(idx)
        await_ui(lambda: self.win.signals.instance_id == "scada104")
        self.assertIn("ioa", [f.key for f in self.win.signals.model.fields])
        self.assertNotIn("native_address", [f.key for f in self.win.signals.model.fields])
        self.assertTrue(self.win.signals.assign_button.isEnabled())

    def test_mapping_doubleclick_navigates_signal_and_returns(self):
        dest = self.win.project.stations("scada104")[0].id
        self.win.project.assign_catalog("meter01", "meter01:ied:2", 0,
                                        "scada104", dest, 3001, 1, 13)
        self.win.navigation.setCurrentRow(3)
        self.win.mapping.model.refresh()
        await_ui(lambda: self.win.mapping.model.rowCount() == 1)
        await_ui(lambda: 0 in self.win.mapping.model.cache)
        self.win.mapping.open_mapping_signal(self.win.mapping.model.index(0, 2))
        self.assertEqual(self.win.navigation.currentRow(), 2)
        self.assertEqual(self.win.signals.instance_id, "scada104")
        self.assertEqual(self.win.signals.scope_id, dest)
        await_ui(lambda: self.win.signals.model.rowCount() == 1)
        self.win.go_back()
        self.assertEqual(self.win.navigation.currentRow(), 3)

    def test_monitoring_and_simulator_use_selected_protocol_catalog(self):
        self.win.navigation.setCurrentRow(4)
        idx = self.win.tree_model.find("meter01:ied:1")
        self.win.tree.setCurrentIndex(idx)
        await_ui(lambda: self.win.monitor.model.rowCount() == 500)
        await_ui(lambda: 0 in self.win.monitor.model._cached)
        monitor = self.win.monitor.model
        self.assertIn("quality", [f.key for f in monitor.fields])
        self.assertEqual(monitor.data(monitor.index(0, len(monitor.fields)-4)), "Unavailable")
        self.win.navigation.setCurrentRow(5)
        idx = self.win.tree_model.find("scada104:ca:1")
        self.win.tree.setCurrentIndex(idx)
        await_ui(lambda: self.win.simulation.model.rowCount() == 1000)
        self.assertFalse(self.win.simulation.toggle.isEnabled())
        self.assertIn("NO", self.win.simulation.provider.page("scada104", 0, 1)[0][-1])
