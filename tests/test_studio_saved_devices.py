"""Windows-first real SQLite Device Save/Reopen Qt integration.

The Protocol Config demo catalog remains independent; these tests exercise
v2 Store persisted stable IED identity without GUI-owned Modbus sockets.
"""
from __future__ import annotations

import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pathlib import Path
import sqlite3
import tempfile
import time
import unittest

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from arpy_gateway.studio.persisted_devices import (
    TCPDeviceRepository, TCPDeviceEdit, TCPDeviceDialog,
    default_engineering_database,
)
from arpy_gateway.studio.v2_window import StudioV2Window
from arpy_gateway.engineering import EngineeringStore, EditConflict

APP = QApplication.instance() or QApplication([])


def await_ui(predicate, seconds=10):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        APP.processEvents()
        if predicate():
            return
        time.sleep(.02)
    raise AssertionError("Saved Modbus devices GUI did not reach expected state")


class PersistedDeviceRepositoryTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.path = Path(self.folder.name) / "engineering.sqlite3"
        self.repo = TCPDeviceRepository(self.path)

    def tearDown(self):
        self.folder.cleanup()

    def test_add_two_ieds_save_close_reopen_and_edit_same_stable_id(self):
        g = self.repo.prepare()
        self.assertEqual(g,1)
        a = self.repo.save(TCPDeviceEdit("Meter A","127.0.0.1",1502,1,250),
                           expected_generation=g)
        b = self.repo.save(TCPDeviceEdit("Meter B","127.0.0.2",1502,2,300),
                           expected_generation=a["generation"])
        page = self.repo.page()
        self.assertEqual(page["generation"],b["generation"])
        self.assertEqual([row.name for row in page["rows"]], ["Meter A", "Meter B"])
        self.assertTrue(all(row.id.startswith("tcp-") for row in page["rows"]))
        self.assertEqual(len({row.id for row in page["rows"]}),2)
        # Create a brand-new repo object, just like a Studio process restart.
        reopened = TCPDeviceRepository(self.path)
        self.assertEqual(reopened.prepare(),b["generation"])
        original = reopened.page()["rows"][0]
        updated = reopened.save(
            TCPDeviceEdit("Meter A Renamed","127.0.0.99",1502,1,250,False),
            original=original,expected_generation=b["generation"])
        current = reopened.page()["rows"][0]
        self.assertEqual(current.id,original.id)
        self.assertEqual(current.name,"Meter A Renamed")
        self.assertEqual(current.settings["host"],"127.0.0.99")
        self.assertFalse(current.enabled)
        self.assertEqual(updated["generation"],b["generation"]+1)
        self.assertEqual(reopened.page()["rows"][1].id,page["rows"][1].id)
        with self.assertRaises(EditConflict):
            self.repo.save(TCPDeviceEdit("Stale","127.0.0.4"),
                           expected_generation=b["generation"])

    def test_validation_no_network_and_staging_with_no_signal_denied(self):
        gen=self.repo.prepare()
        for detail in (
            TCPDeviceEdit("", "127.0.0.1"),
            TCPDeviceEdit("Bad", "host with whitespace"),
            TCPDeviceEdit("Bad", "192.0.2.1", port=True),
            TCPDeviceEdit("Bad", "192.0.2.1", unit_id=248),
        ):
            with self.subTest(detail=detail), self.assertRaises(ValueError):
                self.repo.save(detail,expected_generation=gen)
        self.assertEqual(self.repo.page()["generation"],gen)
        created=self.repo.save(TCPDeviceEdit("IED","192.0.2.30"),
                               expected_generation=gen)
        with self.assertRaisesRegex(ValueError,"No configured signals"):
            self.repo.stage(expected_generation=created["generation"])
        self.assertEqual(len(self.repo.page()["rows"]),1)

    def test_existing_v1_file_never_auto_migrates(self):
        with EngineeringStore(self.path) as store:
            store.create_project("previous-v1")
        db=sqlite3.connect(self.path)
        try:
            version_before=db.execute("PRAGMA user_version").fetchone()[0]
        finally:
            db.close()  # sqlite3 context managers do not close connections.
        self.assertEqual(version_before,1)
        with self.assertRaisesRegex(RuntimeError,"backup and migration"):
            self.repo.prepare()
        db=sqlite3.connect(self.path)
        try:
            self.assertEqual(db.execute("PRAGMA user_version").fetchone()[0],1)
        finally:
            db.close()  # Critical on Windows, otherwise TemporaryDirectory fails.

    def test_platform_resolves_local_data_directory_without_commands(self):
        loc=default_engineering_database()
        self.assertEqual(loc.name,"engineering.sqlite3")
        self.assertIn("ARPY",loc.parts)


class SavedDeviceWidgetTests(unittest.TestCase):
    def setUp(self):
        self.folder=tempfile.TemporaryDirectory()
        self.path=Path(self.folder.name)/"gui.sqlite3"
        self.win=StudioV2Window(engineering_db_path=self.path)
        self.win.show()
        self.win.navigation.setCurrentRow(1)
        self.win.config.show_saved_devices()
        self.panel=self.win.config.saved_devices
        await_ui(lambda: (self.panel._initialized and not self.panel._busy
                          and self.panel.generation is not None))

    def tearDown(self):
        self.win.close()
        APP.processEvents()
        self.folder.cleanup()

    def _complete_dialog(self, *, name, host):
        def set_fields():
            modal=APP.activeModalWidget()
            if not isinstance(modal,TCPDeviceDialog):
                QTimer.singleShot(35,set_fields)
                return
            modal.name.setText(name)
            modal.host.setText(host)
            modal.port.setValue(1502)
            modal.unit_id.setValue(2)
            modal.poll.setValue(250)
            modal.accept()
        QTimer.singleShot(60,set_fields)

    def test_ied_connections_reads_only_sqlite_and_reappears_on_startup(self):
        """Regression for actual field-user report: no separate Saved button."""
        from arpy_gateway.studio.persisted_devices import TCPDeviceRepository, TCPDeviceEdit
        repo=TCPDeviceRepository(self.path)
        panel=self.panel
        self.assertFalse(hasattr(self.win.config, "saved_devices_button"))
        self.assertEqual(self.win.project.points("meter01"), ())
        before=panel.generation
        result=repo.save(TCPDeviceEdit("Power Meter A","192.168.1.12",502,1,1000),
                         expected_generation=before)
        panel.load()
        await_ui(lambda: (not panel._busy and panel.generation==result["generation"]
                          and len(self.win.project.points("meter01"))==1))
        recorded=self.win.project.points("meter01")[0]
        self.assertEqual(recorded.name,"Power Meter A")
        self.assertEqual(recorded.params()["host"],"192.168.1.12")
        self.win.refresh_explorer("peers:meter01")
        node=self.win.tree_model.find("peers:meter01")
        self.assertTrue(node.isValid())
        self.win.tree.setCurrentIndex(node)
        self.assertIs(self.win.config.stack.currentWidget(),panel)
        self.assertTrue(self.win.tree_model.find(recorded.id).isValid())
        # Reopening a brand-new Studio instance must populate Explorer/IED
        # Connections without clicking an auxiliary "Saved Modbus" button.
        self.win.close()
        APP.processEvents()
        reopened=StudioV2Window(engineering_db_path=self.path)
        try:
            reopened.show()
            await_ui(lambda: (len(reopened.project.points("meter01"))==1 and
                              not reopened.config.saved_devices._busy))
            target=reopened.project.points("meter01")[0]
            self.assertEqual((target.id,target.name, target.params()["host"]),
                             (recorded.id,"Power Meter A","192.168.1.12"))
            reopened.navigation.setCurrentRow(1)
            await_ui(lambda: reopened.tree_model.find(target.id).isValid())
            container=reopened.tree_model.find("peers:meter01")
            reopened.tree.setCurrentIndex(container)
            self.assertIs(reopened.config.stack.currentWidget(),
                          reopened.config.saved_devices)
            self.assertEqual(reopened.config.saved_devices.model.rows[0].id,target.id)
            # Changing an IED name must retain the stable ID in both views.
            repo2=TCPDeviceRepository(self.path)
            edited=repo2.save(TCPDeviceEdit("Power Meter Renamed","192.168.1.25"),
                              original=repo2.page()["rows"][0],
                              expected_generation=repo2.page()["generation"])
            reopened.config.saved_devices.load()
            await_ui(lambda: (reopened.config.saved_devices.generation==
                              edited["generation"] and
                              reopened.project.points("meter01")[0].name==
                              "Power Meter Renamed"))
            self.assertEqual(reopened.project.points("meter01")[0].id,target.id)
        finally:
            reopened.close()
            APP.processEvents()

    def test_add_edit_and_reopen_without_deploying(self):
        self.assertIsNone(self.win.monitor.live_panel)
        self._complete_dialog(name="Station Meter 01",host="127.0.0.11")
        self.panel.add.click()
        await_ui(lambda: (not self.panel._busy and self.panel.model.rowCount()==1))
        first=self.panel.model.rows[0]
        self.assertEqual(first.name,"Station Meter 01")
        self.assertEqual(first.settings["port"],1502)
        self.assertIn("NOT DEPLOYED",self.panel.status.text())
        self.panel.table.selectRow(0)
        await_ui(lambda: self.panel.edit.isEnabled())
        self._complete_dialog(name="Station Meter 01 Updated",host="127.0.0.12")
        self.panel.edit.click()
        await_ui(lambda: (not self.panel._busy and self.panel.model.rowCount()==1
                          and self.panel.model.rows[0].name.endswith("Updated")))
        self.assertEqual(self.panel.model.rows[0].id,first.id)
        # Simulator and offline source table remain separate; data was not
        # artificially copied into the old demo EngineeringView.
        self.assertEqual(len(self.win.project.points("meter01")),2)
        self.win.close()
        APP.processEvents()
        new=StudioV2Window(engineering_db_path=self.path)
        try:
            new.show()
            new.navigation.setCurrentRow(1)
            new.config.show_saved_devices()
            view=new.config.saved_devices
            await_ui(lambda: not view._busy and view.model.rowCount()==1)
            self.assertEqual(view.model.rows[0].id,first.id)
            self.assertEqual(view.model.rows[0].settings["host"],"127.0.0.12")
            view.stage.click()
            await_ui(lambda: (not view._busy
                              and "No configured signals" in view.status.text()))
            self.assertEqual(view.model.rowCount(),1)
        finally:
            new.close()
            APP.processEvents()

if __name__=="__main__":
    unittest.main()
