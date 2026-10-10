"""GUI workstream regression tests. QT_QPA_PLATFORM=offscreen in CI."""
from __future__ import annotations

import os
import time
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from arpy_gateway.domain.models import InstanceRole
from arpy_gateway.studio.demo import DemoProvider, MAX_DEMO_ROWS
from arpy_gateway.studio.workers import WorkerHub
from arpy_gateway.studio.grid import PAGE, MAX_PAGES, VirtualSignals, SignalGrid
from arpy_gateway.studio.window import StudioWindow
from PySide6.QtCore import QItemSelection, QItemSelectionModel, Qt
from PySide6.QtWidgets import QApplication, QTableView


app = QApplication.instance() or QApplication([])


def until(predicate, timeout=8):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        app.processEvents()
        if predicate():
            return
        time.sleep(0.008)
    raise AssertionError("Qt asynchronous operation did not complete before timeout")


class ProviderTests(unittest.TestCase):
    def test_protocol_specific_columns(self):
        d = DemoProvider()
        modbus = {f.key for f in d.fields("meter01")}
        iec = {f.key for f in d.fields("scada104")}
        self.assertIn("byte_order", modbus)
        self.assertIn("native_address", modbus)
        self.assertNotIn("ioa", modbus)
        self.assertIn("ioa", iec)
        self.assertIn("ti", iec)
        self.assertNotIn("byte_order", iec)
        self.assertEqual(d.instance("meter01").role, InstanceRole.CLIENT)
        self.assertEqual(d.instance("scada104").role, InstanceRole.SERVER)

    def test_lazy_500k_end_access_does_not_materialize_rows(self):
        d = DemoProvider()
        d.resize_demo(MAX_DEMO_ROWS)
        t = time.monotonic()
        page = d.page("scada104", 499_968, 32)
        elapsed = time.monotonic() - t
        self.assertEqual(len(page), 32)
        self.assertEqual(page[-1][0], "scada104:signal:0500000")
        self.assertFalse(hasattr(d, "_signals"))
        self.assertEqual(len(d._overrides), 0)
        self.assertLess(elapsed, 2.0)

    def test_numeric_modbus_address_sort(self):
        d = DemoProvider()
        d.resize_demo(100_000)
        # PDU 0 addresses across Unit IDs are adjacent, not string sorted 1,10,2.
        page = d.page("meter01", 0, 4, sort_key="native_address")
        ix = [f.key for f in d.fields("meter01")].index("native_address") + 2
        self.assertEqual([x[ix] for x in page], [0, 0, 0, 0])
        page2 = d.page("meter01", 4, 2, sort_key="native_address")
        self.assertEqual([x[ix] for x in page2], [2, 2])

    def test_bulk_transaction_undo_redo_and_invalid_rollback(self):
        d = DemoProvider()
        sig1 = d.signal_id("scada104", 0)
        sig2 = d.signal_id("scada104", 1)
        self.assertEqual(d.edit("scada104", [(sig1, "ioa", 201), (sig2, "ioa", 202)]), 2)
        self.assertEqual(d.history(False), 2)
        self.assertEqual(d.history(True), 2)
        self.assertEqual(d.page("scada104", 0, 1)[0][
            [f.key for f in d.fields("scada104")].index("ioa") + 2], 201)
        with self.assertRaises(ValueError):
            d.edit("scada104", [(sig1, "ioa", 500), (sig2, "ioa", "NaN")])
        self.assertEqual(d.page("scada104", 0, 1)[0][
            [f.key for f in d.fields("scada104")].index("ioa") + 2], 201)

    def test_multi_destination_id_based_mapping(self):
        d = DemoProvider()
        self.assertEqual(d.map_range("meter01", "scada104", 0, 10), 10)
        self.assertEqual(d.map_range("meter01", "mirror", 0, 10), 10)
        self.assertEqual(d.mappings_count(), 20)
        self.assertEqual(d.map_range("meter01", "scada104", 0, 10), 0)
        self.assertNotEqual(d.mapping_page(0, 1)[0].destination_signal_id,
                            d.mapping_page(10, 1)[0].destination_signal_id)
        with self.assertRaises(ValueError):
            d.map_range("meter01", "meter01", 0, 10)

    def test_offline_monitoring_does_not_fabricate_good_or_packets(self):
        d = DemoProvider()
        message = d.diagnostics()
        self.assertIn("Runtime connected: NO", message)
        self.assertIn("unavailable", message)
        self.assertIn("No field devices accessed", message)


class QtStudioTests(unittest.TestCase):
    def setUp(self):
        self.demo = DemoProvider()
        self.win = StudioWindow(self.demo)
        self.win.show()
        until(lambda: self.win.signals.model.total == 1000)

    def tearDown(self):
        self.win.close()
        app.processEvents()

    def test_six_separate_workspaces_and_protocol_schema(self):
        self.assertEqual(self.win.navigation.count(), 6)
        self.assertEqual(self.win.pages.count(), 6)
        self.win.navigation.setCurrentRow(2)
        self.win.signals.instances.setCurrentIndex(
            self.win.signals.instances.findData("scada104"))
        until(lambda: self.win.signals.model.total == 1000)
        self.assertIn("ioa", [f.key for f in self.win.signals.model.fields])
        self.assertNotIn("byte_order", [f.key for f in self.win.signals.model.fields])
        self.win.navigation.setCurrentRow(5)
        self.assertFalse(self.win.simulation.findChildren(type(self.win.simulation.preview_status)) == [])
        self.win.simulation.preview_value()
        self.assertIn("delivered: NO", self.win.simulation.preview_status.text())

    def test_window_virtual_page_cache_and_stale_generation(self):
        self.win.signals.sizes.setCurrentIndex(2)
        until(lambda: self.win.signals.model.total == 500000, 15)
        model = self.win.signals.model
        self.assertEqual(model.rowCount(), 500000)
        # Force an out-of-view page; the page loads without 500k table widgets.
        tail = model.index(499999, 0)
        model.data(tail)
        until(lambda: (499999 // PAGE) in model._cached, 12)
        self.assertLessEqual(len(model._cached), MAX_PAGES)
        self.assertIsInstance(self.win.signals.grid, QTableView)
        old_generation = model.generation
        model.reload(search="#54321")
        until(lambda: model.total == 1)
        self.assertGreater(model.generation, old_generation)
        until(lambda: 0 in model._cached)
        self.assertEqual(model.data(model.index(0, 0), Qt.ItemDataRole.ToolTipRole),
                         "meter01:signal:0054321")

    def test_excel_copy_paste_fill_and_identity_correct_after_reverse_sort(self):
        self.win.navigation.setCurrentRow(2)
        model = self.win.signals.model
        grid = self.win.signals.grid
        until(lambda: 0 in model._cached)
        name_col = [x.key for x in model.fields].index("name")
        sel = QItemSelection(model.index(0, name_col), model.index(0, name_col))
        grid.selectionModel().select(sel, QItemSelectionModel.SelectionFlag.ClearAndSelect)
        grid.copy_cells()
        until(lambda: "METER01" in QApplication.clipboard().text())
        grid.sortByColumn(0, Qt.SortOrder.DescendingOrder)
        until(lambda: model.total == 1000)
        until(lambda: 0 in model._cached)
        sid = model.view_signal_id(0)
        self.assertEqual(sid, "meter01:signal:0001000")
        grid.setCurrentIndex(model.index(0, name_col))
        QApplication.clipboard().setText("AFTER-SORT-ID")
        grid.paste_cells()
        until(lambda: self.demo.page("meter01", 999, 1)[0][name_col + 2] == "AFTER-SORT-ID")
        grid.undo()
        until(lambda: self.demo.page("meter01", 999, 1)[0][name_col + 2] != "AFTER-SORT-ID")
        grid.redo()
        until(lambda: self.demo.page("meter01", 999, 1)[0][name_col + 2] == "AFTER-SORT-ID")
