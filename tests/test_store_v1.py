"""SQLite engineering Store: real persistence, CAS, journal, stage and capacity."""
import os
import sqlite3
import tempfile
import time
import unittest
from dataclasses import replace
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from arpy_gateway.domain.models import (
    ProtocolInstance, ProtocolSignal, MappingDefinition,
    InstanceRole, SignalFamily, ValueType, SignalDirection,
)
from arpy_gateway.engineering import EngineeringStore, EditConflict, StageRejected
from arpy_gateway.contracts.codec import canonical_project_digest

def inst(i="mb", role=InstanceRole.CLIENT):
    return ProtocolInstance(i,i,"modbus_tcp",role,True,{"host":"127.0.0.1","port":502})

def sig(i="s1", owner="mb", address=10, outbound=False):
    return ProtocolSignal(i,owner,i,SignalFamily.ANALOG,ValueType.FLOAT32,
                          SignalDirection.OUTBOUND if outbound else SignalDirection.INBOUND,
                          True,"V",{"register_table":"holding","native_address":address})

class StoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.db=Path(self.tmp.name)/"project.sqlite3"
        self.store=EngineeringStore(self.db)
        self.store.create_project("plant")
    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def test_reopen_snapshot_and_undo_redo(self):
        g=self.store.apply("plant",expected_generation=0,instances=(inst(),))
        g=self.store.apply("plant",expected_generation=g,
                           signals=(sig(),sig("s2",address=12)))
        prior=self.store.load_draft("plant")
        self.store.close()
        with EngineeringStore(self.db) as store:
            self.assertEqual(store.load_draft("plant"),prior)
            g=store.undo("plant",expected_generation=g)
            self.assertEqual(store.count_signals("plant","mb"),0)
            g=store.redo("plant",expected_generation=g)
            self.assertEqual(store.load_draft("plant"),prior)
            g=store.apply("plant",expected_generation=g,
                          signals=(sig(address=55),))
            with self.assertRaises(ValueError):
                store.redo("plant",expected_generation=g)
            self.assertEqual(store.signal_page("plant","mb")[0].parameters["native_address"],55)

    def test_transaction_rollback_stale_generation_and_fk(self):
        g=self.store.apply("plant",expected_generation=0,instances=(inst(),))
        with self.assertRaises(sqlite3.IntegrityError):
            self.store.apply("plant",expected_generation=g,
                             signals=(sig(),sig("broken","not-an-instance")))
        self.assertEqual(self.store.count_signals("plant","mb"),0)
        self.assertEqual(self.store.generation("plant"),g)
        with self.assertRaises(EditConflict):
            self.store.apply("plant",expected_generation=0,signals=(sig(),))
        g=self.store.apply("plant",expected_generation=g,
                           instances=(inst("server",InstanceRole.SERVER),))
        g=self.store.apply("plant",expected_generation=g,
                           signals=(sig(),sig("dst","server",100,True)))
        g=self.store.apply("plant",expected_generation=g,
                           mappings=(MappingDefinition("m1","s1","dst"),))
        with self.assertRaises(sqlite3.IntegrityError):
            self.store.apply("plant",expected_generation=g,delete_signals=("s1",))
        self.assertEqual(self.store.generation("plant"),g)
        g=self.store.apply("plant",expected_generation=g,
                           delete_mappings=("m1",),delete_signals=("s1",))
        self.assertEqual(len(self.store.load_draft("plant").mappings),0)
        g=self.store.undo("plant",expected_generation=g)
        self.assertEqual(len(self.store.load_draft("plant").mappings),1)

    def test_staging_immutable_content_hash_and_invalid_draft(self):
        g=self.store.apply("plant",expected_generation=0,instances=(inst(),))
        g=self.store.apply("plant",expected_generation=g,signals=(sig(),))
        a=self.store.stage("plant",expected_generation=g)
        self.assertEqual(len(a.digest),64)
        self.assertEqual(canonical_project_digest(self.store.load_draft("plant")),a.digest)
        self.assertEqual(self.store.stage("plant",expected_generation=g),a)
        g=self.store.apply("plant",expected_generation=g,signals=(sig(address=33),))
        self.assertEqual(self.store.revision("plant",a.digest).signals[0].parameters["native_address"],10)
        with self.assertRaises(EditConflict):
            self.store.stage("plant",expected_generation=g-1)
        self.assertNotEqual(self.store.stage("plant",expected_generation=g).digest,a.digest)
        g=self.store.apply("plant",expected_generation=g,
                           signals=(replace(sig(),name=""),))
        with self.assertRaises(StageRejected) as rejected:
            self.store.stage("plant",expected_generation=g)
        self.assertIn("SIGNAL_NAME",{x.code for x in rejected.exception.issues})
        self.assertEqual(self.store.load_draft("plant").signals[0].name,"")

    def test_concurrent_readers_and_ordered_writer(self):
        g=self.store.apply("plant",expected_generation=0,instances=(inst(),))
        g=self.store.apply("plant",expected_generation=g,signals=(sig(),))
        def reader():
            return all(len(self.store.signal_page("plant","mb",limit=3))==1
                       for _ in range(50))
        with ThreadPoolExecutor(max_workers=4) as pool:
            futures=[pool.submit(reader) for _ in range(3)]
            for i in range(12):
                g=self.store.apply("plant",expected_generation=g,
                                   signals=(sig(address=15+i),))
            self.assertTrue(all(f.result(timeout=10) for f in futures))
        self.assertEqual(self.store.generation("plant"),g)

    def test_unknown_future_schema_rejected(self):
        self.store.close()
        with sqlite3.connect(self.db) as db: db.execute("PRAGMA user_version=99")
        with self.assertRaisesRegex(RuntimeError,"migration required"):
            EngineeringStore(self.db)

class CapacityTests(unittest.TestCase):
    def test_large_batch_and_last_keyset_page(self):
        n=500_000 if os.getenv("ARPY_STORE_BENCH_500K")=="1" else 100_000
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/"capacity.sqlite3"
            with EngineeringStore(path) as store:
                store.create_project("capacity")
                gen=store.apply("capacity",expected_generation=0,instances=(inst(),))
                t=time.monotonic()
                gen=store.apply("capacity",expected_generation=gen,
                    signals=(sig(f"reg:{i:08d}",address=i%30000) for i in range(n)))
                elapsed=time.monotonic()-t
                self.assertEqual(store.count_signals("capacity","mb"),n)
                self.assertEqual([s.id for s in store.signal_page(
                    "capacity","mb",after_id=f"reg:{n-3:08d}",limit=4)],
                    [f"reg:{n-2:08d}",f"reg:{n-1:08d}"])
                print(f"STORE_BENCHMARK count={n} time={elapsed:.2f}s "
                      f"db_size={path.stat().st_size}",flush=True)
