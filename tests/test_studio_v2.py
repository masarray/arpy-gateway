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

    def test_assign_arbitrary_noncontiguous_catalog_signal_ids(self):
        target = self.project.stations("scada104")[0].id
        chosen = ("meter01:signal:0000501", "meter01:signal:0000720",
                  "meter01:signal:0000899")
        created = self.project.assign_selected("meter01", "meter01:ied:2",
                                               chosen, "scada104", target, 4001, 13)
        self.assertEqual(len(created), 3)
        self.assertEqual(tuple(m.source_signal_id for m in self.demo.mappings()), chosen)
        self.assertEqual(tuple(m.destination_signal_id for m in self.demo.mappings()), created)
        self.assertEqual(self.demo._get(self.demo.instance("scada104"), 1001, "ioa"), 4002)
        with self.assertRaises(ValueError):
            self.project.assign_selected("meter01", "meter01:ied:1", chosen,
                                         "scada104", target, 7001, 13)
        with self.assertRaises(ValueError):
            self.project.assign_selected("meter01", "meter01:ied:2", chosen,
                                         "scada104", target, 4002, 13)
        self.assertEqual(self.demo.mappings_count(), 3)

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

    def test_compact_top_tabs_and_drawer_catalog_search_assignment(self):
        from arpy_gateway.studio.catalog_drawer import CatalogSource, MAX_CATALOG_PAGES
        from PySide6.QtCore import QItemSelection, QItemSelectionModel
        self.assertEqual(self.win.navigation.count(), 6)
        self.assertEqual(self.win.navigation.objectName(), "WorkspaceTabs")
        self.assertIsNone(self.win.findChild(type(self.win.navigation), "Sidebar"))
        self.win.navigation.setCurrentRow(2)
        self.win.tree.setCurrentIndex(self.win.tree_model.find("scada104:ca:1"))
        self.win.signals.assign_button.click()
        drawer = self.win.catalog_drawer
        await_ui(lambda: drawer.maximumWidth() >= 400)
        self.assertTrue(drawer.isVisible())
        self.assertEqual(drawer.table.model().columnCount(), 4)
        drawer.connection.setCurrentIndex(drawer.connection.findData("meter01:ied:2"))
        drawer.search.setText("addr:1000")
        drawer.refresh_search()
        await_ui(lambda: drawer.model.rowCount() == 1)
        await_ui(lambda: 0 in drawer.model._pages)
        self.assertEqual(drawer.model.data(drawer.model.index(0, 2)), "1000  ·  HOLDING")
        self.assertEqual(drawer.model.data(drawer.model.index(0, 0)),
                         "Meter02 / Voltage 000501")
        drawer.search.setText("voltage 000720")
        drawer.refresh_search()
        await_ui(lambda: drawer.model.rowCount() == 1)
        drawer.search.clear()
        drawer.refresh_search()
        await_ui(lambda: drawer.model.rowCount() == 500)
        selection = QItemSelection()
        selection.select(drawer.model.index(0, 0), drawer.model.index(0, 3))
        selection.select(drawer.model.index(219, 0), drawer.model.index(219, 3))
        selection.select(drawer.model.index(398, 0), drawer.model.index(398, 3))
        drawer.table.selectionModel().select(
            selection, QItemSelectionModel.SelectionFlag.ClearAndSelect)
        await_ui(lambda: drawer.preview.rowCount() == 3)
        self.assertEqual(drawer.preview.data(drawer.preview.index(1, 2)), "1002")
        drawer.ioa.setValue(4501)
        await_ui(lambda: drawer.preview.data(drawer.preview.index(2, 2)) == "4503")
        drawer.commit.click()
        await_ui(lambda: self.win.project.demo.mappings_count() == 3)
        routes = self.win.project.demo.mappings()
        self.assertEqual(tuple(x.source_signal_id for x in routes),
                         ("meter01:signal:0000501", "meter01:signal:0000720",
                          "meter01:signal:0000899"))
        await_ui(lambda: drawer.maximumWidth() == 0)
        self.assertLessEqual(len(drawer.model._pages), MAX_CATALOG_PAGES)

    def test_catalog_large_search_does_not_materialize_dataset(self):
        from arpy_gateway.studio.catalog_drawer import CatalogSource
        self.win.project.demo.resize_demo(500_000)
        source = CatalogSource(self.win.project, "meter01", "meter01:ied:2")
        start = time.monotonic()
        view = source.lookup("addr:1000")
        self.assertEqual(view.total, 8)
        self.assertLess(time.monotonic() - start, 1.5)
        self.assertEqual(len(source.page(0, 8, view)), 8)
        self.assertFalse(hasattr(self.win.project.demo, "_signals"))

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
        await_ui(lambda: self.win.simulation.model.rowCount() == 0)
        self.assertFalse(self.win.simulation.toggle.isEnabled())
        # Destination catalog is empty until signals are assigned from source.
        self.assertEqual(self.win.simulation.provider.page("scada104", 0, 1), [])
