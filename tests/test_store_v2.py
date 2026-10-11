"""RFC #13 v2 Store migration and ownership integration (Windows-first).

No Qt/Modbus or IPC mutations. Requires pinned ARCH draft #22 as test-time
overlay; the original v1 Store and v1 wire contract are not overwritten.
"""
from __future__ import annotations

import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from arpy_gateway.domain.models import (
    InstanceRole, ProjectSnapshot, ProtocolInstance, ProtocolSignal,
    MappingDefinition, SignalDirection, SignalFamily, ValueType,
)
from arpy_gateway.domain.v2 import CommunicationPoint, DataStation, SignalBinding
from arpy_gateway.engineering import EngineeringStore, EditConflict, StageRejected
from arpy_gateway.engineering.store_v2 import EngineeringStoreV2


def mb_instance():
    return ProtocolInstance("mb", "Modbus TCP Master", "modbus_tcp",
                            InstanceRole.CLIENT, True, {})


def iec_instance():
    return ProtocolInstance("iec", "IEC104 Server", "iec104",
                            InstanceRole.SERVER, True, {})


def source(sid, addr):
    return ProtocolSignal(sid, "mb", sid, SignalFamily.ANALOG, ValueType.FLOAT32,
                          SignalDirection.INBOUND, True, "V",
                          {"register_table": "holding", "native_address": addr})


def destination():
    return ProtocolSignal("out", "iec", "IEC104 Out", SignalFamily.ANALOG,
                          ValueType.FLOAT32, SignalDirection.OUTBOUND, True,
                          "V", {"ioa": 1001, "ti": 13})


def peer(sid, host):
    return CommunicationPoint(sid, "mb", sid, "remote_device", True,
                              {"host": host, "port": 502, "unit_id": 1})


def master(sid, ip):
    return CommunicationPoint(sid, "iec", sid, "authorized_master", True,
                              {"allowed_master_ip": ip})


class V2StoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.file = Path(self.temp.name) / "project.sqlite3"
        self.store = EngineeringStoreV2(self.file)
        self.store.create_project("plant")

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def test_no_automatic_migration_and_legacy_records_preserved(self):
        gen = self.store.apply("plant", expected_generation=0,
                               instances=(mb_instance(), iec_instance()))
        gen = self.store.apply("plant", expected_generation=gen,
                               signals=(source("s1",10),source("s2",20),destination()))
        prior = self.store.load_draft("plant")
        with self.assertRaisesRegex(RuntimeError,"migrate_v2"):
            self.store.apply_v2("plant", expected_generation=gen,
                                communication_points=(peer("a","127.0.0.1"),))
        self.assertEqual(self.store.load_draft("plant"),prior)
        self.assertTrue(self.store.migrate_v2())
        self.assertFalse(self.store.migrate_v2())
        self.assertEqual(self.store.load_draft_v2("plant").base,prior)
        with self.assertRaisesRegex(RuntimeError,"stage_v2"):
            self.store.stage("plant",expected_generation=gen)
        with self.assertRaises(StageRejected) as caught:
            self.store.stage_v2("plant",expected_generation=gen)
        self.assertIn("BINDING_REQUIRED",{i.code for i in caught.exception.issues})
        self.assertEqual(self.store.generation("plant"),gen)
        self.assertEqual(self.store._writer.execute("PRAGMA user_version").fetchone()[0],2)

    def test_real_ied_and_station_save_undo_redo_reopen_and_stage(self):
        self.store.migrate_v2()
        gen=self.store.apply_v2(
            "plant",expected_generation=0,
            instances=(mb_instance(),iec_instance()),
            communication_points=(
                peer("meter1","127.0.0.1"),peer("meter2","127.0.0.2"),
                master("scada-a","192.0.2.1"),master("scada-b","192.0.2.2")),
            data_stations=(DataStation("station","iec","CA1",True,
                                      {"common_address":1}),),
            signals=(source("s1",10),source("s2",20),destination()),
            signal_bindings=(
                SignalBinding("s1",communication_point_id="meter1"),
                SignalBinding("s2",communication_point_id="meter2"),
                SignalBinding("out",data_station_id="station")),
            mappings=(MappingDefinition("map","s1","out"),))
        snapshot=self.store.load_draft_v2("plant")
        self.assertEqual(len(snapshot.communication_points),4)
        by_signal = {binding.signal_id: binding for binding in snapshot.signal_bindings}
        self.assertEqual(by_signal["s1"].communication_point_id,"meter1")
        self.assertEqual(by_signal["out"].data_station_id,"station")
        revision=self.store.stage_v2("plant",expected_generation=gen)
        self.assertEqual(self.store.revision_v2("plant",revision.digest),snapshot)
        self.store.close()
        with EngineeringStoreV2(self.file) as reopened:
            self.assertEqual(reopened.load_draft_v2("plant"),snapshot)
            self.assertEqual(reopened.generation("plant"),gen)
            gen=reopened.apply_v2("plant",expected_generation=gen,
                                  communication_points=(peer("meter1","127.0.0.55"),))
            self.assertEqual(reopened.revision_v2("plant",revision.digest),snapshot)
            self.assertEqual(reopened.load_draft_v2("plant").communication_points[0].settings["host"],
                             "127.0.0.55")
            gen=reopened.undo("plant",expected_generation=gen)
            self.assertEqual(reopened.load_draft_v2("plant"),snapshot)
            gen=reopened.redo("plant",expected_generation=gen)
            self.assertEqual(reopened.load_draft_v2("plant").communication_points[0].settings["host"],
                             "127.0.0.55")
            with self.assertRaises(EditConflict):
                reopened.apply_v2("plant",expected_generation=gen-1,
                                  data_stations=(DataStation("x","iec","Bad",True,{}),))

    def test_staged_revision_unchanged_by_new_draft_or_failed_batch(self):
        self.store.migrate_v2()
        gen=self.store.apply_v2(
            "plant",expected_generation=0,instances=(mb_instance(),),
            communication_points=(peer("p","127.0.0.1"),),
            signals=(source("s",10),),
            signal_bindings=(SignalBinding("s",communication_point_id="p"),))
        staged=self.store.stage_v2("plant",expected_generation=gen)
        previous=self.store.revision_v2("plant",staged.digest)
        with self.assertRaises(sqlite3.IntegrityError):
            self.store.apply_v2("plant",expected_generation=gen,
                                signals=(source("s2",11),),
                                signal_bindings=(SignalBinding("s2",communication_point_id="missing"),))
        self.assertEqual(self.store.generation("plant"),gen)
        self.assertEqual(self.store.load_draft_v2("plant"),previous)
        gen=self.store.apply_v2("plant",expected_generation=gen,
                                 communication_points=(peer("p","127.0.0.8"),))
        self.assertEqual(self.store.revision_v2("plant",staged.digest),previous)
        self.assertNotEqual(self.store.stage_v2("plant",expected_generation=gen).digest,staged.digest)

    def test_disabled_peer_persists_as_draft_but_stage_fails(self):
        self.store.migrate_v2()
        gen=self.store.apply_v2(
            "plant",expected_generation=0,
            instances=(mb_instance(),),
            communication_points=(CommunicationPoint(
                "p","mb","Disabled","remote_device",False,
                {"host":"127.0.0.1","port":502,"unit_id":1}),),
            signals=(source("s",10),),
            signal_bindings=(SignalBinding("s",communication_point_id="p"),))
        self.assertEqual(len(self.store.load_draft_v2("plant").signal_bindings),1)
        with self.assertRaises(StageRejected) as caught:
            self.store.stage_v2("plant",expected_generation=gen)
        self.assertIn("BINDING_DISABLED",{e.code for e in caught.exception.issues})

    def test_large_peer_keyset_queries_require_no_gui_widgets(self):
        self.store.migrate_v2()
        gen=self.store.apply_v2("plant",expected_generation=0,instances=(mb_instance(),))
        # Indexed store migration must persist distinct IDs and support large
        # batches without materializing full Qt row/widget models.
        peers=(peer(f"device:{i:05d}",f"meter{i}.example") for i in range(1000))
        gen=self.store.apply_v2("plant",expected_generation=gen,
                                communication_points=peers)
        self.assertEqual(len(self.store.load_draft_v2("plant").communication_points),1000)
        self.assertEqual([p.id for p in self.store.communication_point_page(
            "plant","mb",after_id="device:00997",limit=2)],
            ["device:00998","device:00999"])
        with self.assertRaises(ValueError):
            self.store.communication_point_page("plant","mb",limit=257)
        self.store.close()
        with EngineeringStoreV2(self.file) as reopened:
            self.assertEqual(reopened.generation("plant"),gen)
            row=reopened._writer.execute(
                "SELECT id FROM communication_points WHERE project_id=? "
                "AND instance_id=? AND id>? ORDER BY id LIMIT 1",
                ("plant","mb","device:00998")).fetchone()
            self.assertEqual(row[0],"device:00999")


if __name__=="__main__":
    unittest.main()
