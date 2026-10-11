"""Regression for user's screenshot: saved IED MUST be in normal Explorer.

A single v2 SQLite database owns every displayed engineering record.
No DemoProvider meter rows, no separate Saved button, no fake signal values.
Windows CI runs with proposed ARCH/STORE source pinned in checkout.
"""
from __future__ import annotations

import os
os.environ.setdefault("QT_QPA_PLATFORM","offscreen")
from pathlib import Path
import tempfile
import time
import unittest
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from arpy_gateway.studio.sqlite_project import SQLiteEngineeringProject
from arpy_gateway.studio.persisted_devices import TCPDeviceRepository,TCPDeviceEdit
from arpy_gateway.studio.sqlite_window import SQLiteStudioWindow,SignalEditDialog
from arpy_gateway.studio.app import main

APP=QApplication.instance() or QApplication([])


def wait_for(check,timeout=10):
    deadline=time.monotonic()+timeout
    while time.monotonic()<deadline:
        APP.processEvents()
        if check():return
        time.sleep(.015)
    raise AssertionError("SQLite engineering GUI failed to settle")


class CanonicalExplorerTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.db=Path(self.tmp.name)/"engineering.sqlite3"
        legacy=TCPDeviceRepository(self.db)
        gen=legacy.prepare()
        self.old=legacy.save(
            TCPDeviceEdit("Power Meter A","192.168.1.12",502,1,1000),
            expected_generation=gen)
        self.win=SQLiteStudioWindow(self.db)
        self.win.show()
        wait_for(lambda:self.win.loaded and self.win.devices_model.rowCount()==1
                 and not self.win.busy)

    def tearDown(self):
        self.win.close()
        APP.processEvents()
        self.tmp.cleanup()

    def test_reopen_existing_record_is_visible_without_secondary_button(self):
        self.assertEqual(self.win.navigation.currentRow(),1)
        names=tuple(p.name for p in self.win.topology.point_rows)
        self.assertEqual(names,("Power Meter A",))
        self.assertEqual(self.win.devices_model.rowCount(),1)
        self.assertEqual(self.win.devices_model.rows[0].settings["host"],"192.168.1.12")
        self.assertEqual(self.win.devices_model.rows[0].id,self.old["id"])
        self.assertNotIn("Meter 01",names)
        self.assertNotIn("Meter 02",names)
        self.assertIsNone(self.win.findChild(type(self.win.add_ied),
                                              "Saved Modbus Devices (SQLite)"))
        self.assertIsNotNone(self.win.tree_model.find(self.old["id"]))
        self.assertTrue(self.win.tree_model.find("peers:arpy-modbus-tcp-master").isValid())
        self.assertFalse(self.win.tree_model.find("meter01:ied:1").isValid())

    def test_inline_ied_edit_keeps_id_and_auto_refreshes_tree(self):
        idx=self.win.tree_model.find("peers:arpy-modbus-tcp-master")
        self.win.tree.setCurrentIndex(idx)
        wait_for(lambda:self.win.devices_model.rowCount()==1 and not self.win.busy)
        self.win.devices.selectRow(0)
        def edit_dialog():
            from arpy_gateway.studio.persisted_devices import TCPDeviceDialog
            dialog=APP.activeModalWidget()
            if not isinstance(dialog,TCPDeviceDialog):
                QTimer.singleShot(20,edit_dialog)
                return
            dialog.name.setText("Power Meter A Updated")
            dialog.host.setText("192.168.1.13")
            dialog.accept()
        QTimer.singleShot(30,edit_dialog)
        self.win.edit_device()
        wait_for(lambda:not self.win.busy and
                 self.win.devices_model.rowCount()==1 and
                 self.win.devices_model.rows[0].name=="Power Meter A Updated")
        item=self.win.devices_model.rows[0]
        self.assertEqual(item.id,self.old["id"])
        self.assertEqual(item.settings["host"],"192.168.1.13")
        self.win.close()
        APP.processEvents()
        fresh=SQLiteStudioWindow(self.db)
        try:
            fresh.show()
            wait_for(lambda:fresh.loaded and
                     fresh.devices_model.rowCount()==1 and not fresh.busy)
            self.assertEqual(fresh.devices_model.rows[0].id,item.id)
            self.assertEqual(fresh.devices_model.rows[0].name,item.name)
            self.assertTrue(fresh.tree_model.find(item.id).isValid())
        finally:
            fresh.close();APP.processEvents()

    def test_signals_are_saved_with_explicit_ied_id_not_row_number(self):
        self.win.navigation.setCurrentRow(2)
        point_id=self.old["id"]
        idx=self.win.tree_model.find(point_id)
        self.assertTrue(idx.isValid())
        self.win.tree.setCurrentIndex(idx)
        wait_for(lambda:not self.win.busy and self.win.scope_id==point_id)
        self.assertEqual(self.win.signal_model.rowCount(),0)
        def new_signal_dialog():
            dialog=APP.activeModalWidget()
            if not isinstance(dialog,SignalEditDialog):
                QTimer.singleShot(20,new_signal_dialog)
                return
            dialog.name.setText("Phase A Voltage")
            dialog.address.setValue(100)
            dialog.accept()
        QTimer.singleShot(30,new_signal_dialog)
        self.win.add_signal_dialog()
        wait_for(lambda:not self.win.busy and
                 self.win.signal_model.rowCount()==1)
        sig,peer,station=self.win.signal_model.rows[0]
        self.assertEqual(peer,point_id)
        self.assertIsNone(station)
        self.assertEqual(sig.name,"Phase A Voltage")
        self.assertEqual(sig.parameters["native_address"],100)
        self.win.close()
        APP.processEvents()
        fresh=SQLiteStudioWindow(self.db)
        try:
            fresh.show()
            wait_for(lambda:fresh.loaded and not fresh.busy)
            fresh.navigation.setCurrentRow(2)
            wait_for(lambda:not fresh.busy)
            iid=fresh.repo.devices.instance_id
            new=fresh.tree_model.find(point_id)
            fresh.tree.setCurrentIndex(new)
            wait_for(lambda:fresh.signal_model.rowCount()==1 and not fresh.busy)
            self.assertEqual(fresh.signal_model.rows[0][0].id,sig.id)
            self.assertEqual(fresh.signal_model.rows[0][1],peer)
            self.assertEqual(len(fresh.topology.point_rows),1)
        finally:
            fresh.close();APP.processEvents()

    def test_protocol_mapping_is_sqlite_not_a_demo_catalog(self):
        self.win.navigation.setCurrentRow(3)
        wait_for(lambda:not self.win.busy)
        self.assertEqual(self.win.mapping_model.rowCount(),0)
        self.assertNotIn("DemoProvider",type(self.win.repo).__name__)
        self.assertEqual(len(self.win.topology.instance_rows),1)


if __name__=="__main__":
    unittest.main()
