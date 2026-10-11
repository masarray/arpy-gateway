"""RFC #13 proposed SQLite v2 upgrade and device/station draft persistence.

STORE lane only. Compatible with existing v1 file layout, explicit additive
migration, no protocol socket/Qt import or automatic runtime deployment.
The canonical ARCH v2 model is proposed in unmerged PR #22.
"""
from __future__ import annotations

import json
from typing import Iterable
import zlib

from arpy_gateway.domain.models import to_mutable

from .store import (
    EngineeringStore, EditConflict, RevisionRecord, StageRejected,
    _TABLES, _as_payload, _columns, _chunks, _json, _time,
)

# Uses a separate journal-row table to preserve the original v1 CHECK schema.
# Stable identity stays (project_id,id), NEVER display name/address/row index.
_EXTRA = {
    "communication_point": (
        "communication_points",
        ("id", "record", "instance_id", "kind", "enabled"),
    ),
    "data_station": (
        "data_stations", ("id", "record", "instance_id", "enabled"),
    ),
    "binding": (
        "signal_bindings", ("id", "record", "communication_point_id",
                            "data_station_id"),
    ),
}


def _extra_payload(kind, obj):
    from arpy_gateway.domain.v2 import CommunicationPoint, DataStation, SignalBinding
    if kind == "communication_point" and type(obj) is CommunicationPoint:
        return {"id": obj.id, "instance_id": obj.instance_id, "name": obj.name,
                "kind": obj.kind, "enabled": obj.enabled, "settings": to_mutable(obj.settings)}
    if kind == "data_station" and type(obj) is DataStation:
        return {"id": obj.id, "instance_id": obj.instance_id, "name": obj.name,
                "enabled": obj.enabled, "settings": to_mutable(obj.settings)}
    if kind == "binding" and type(obj) is SignalBinding:
        return {"id": obj.signal_id,
                "communication_point_id": obj.communication_point_id,
                "data_station_id": obj.data_station_id}
    raise TypeError("Incorrect proposed v2 entity type")


def _extra_columns(kind, obj):
    if kind == "communication_point":
        return (obj["id"], _json(obj), obj["instance_id"], obj["kind"], int(obj["enabled"]))
    if kind == "data_station":
        return (obj["id"], _json(obj), obj["instance_id"], int(obj["enabled"]))
    return (obj["id"], _json(obj), obj["communication_point_id"], obj["data_station_id"])


class EngineeringStoreV2(EngineeringStore):
    """Explicitly-upgraded v2 Store; legacy v1 Store remains unchanged."""

    def _initialize(self):
        version = self._writer.execute("PRAGMA user_version").fetchone()[0]
        if version == 0:
            super()._initialize()
            version = 1
        if version not in (1, 2):
            raise RuntimeError("Unknown Store schema; reviewed migration required")
        self._db_version = version
        if version == 2:
            self._require_tables()

    def _require_tables(self):
        expected = set(("communication_points", "data_stations", "signal_bindings",
                        "edit_journal_rows_v2", "revisions_v2"))
        actual = {row[0] for row in self._writer.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        if not expected <= actual:
            raise RuntimeError("Incomplete v2 migration; refusing unsafe Store opening")

    def migrate_v2(self):
        """Explicit, additive and transactional migration; no guessed IED IDs.

        Requires an exclusive Store writer. A production installer should take
        a verified backup before invoking this reviewed migration.
        """
        if self._db_version == 2:
            return False
        with self._write() as db:
            if db.execute("PRAGMA user_version").fetchone()[0] != 1:
                raise RuntimeError("Unexpected Store schema during upgrade")
            statements = (
                """CREATE TABLE communication_points(
                    project_id TEXT NOT NULL, id TEXT NOT NULL,
                    record TEXT NOT NULL, instance_id TEXT NOT NULL,
                    kind TEXT NOT NULL, enabled INTEGER NOT NULL CHECK(enabled IN (0,1)),
                    PRIMARY KEY(project_id,id),
                    FOREIGN KEY(project_id,instance_id) REFERENCES instances(project_id,id)
                      ON DELETE RESTRICT
                ) WITHOUT ROWID""",
                """CREATE TABLE data_stations(
                    project_id TEXT NOT NULL, id TEXT NOT NULL,
                    record TEXT NOT NULL, instance_id TEXT NOT NULL,
                    enabled INTEGER NOT NULL CHECK(enabled IN (0,1)),
                    PRIMARY KEY(project_id,id),
                    FOREIGN KEY(project_id,instance_id) REFERENCES instances(project_id,id)
                      ON DELETE RESTRICT
                ) WITHOUT ROWID""",
                """CREATE TABLE signal_bindings(
                    project_id TEXT NOT NULL, id TEXT NOT NULL,
                    record TEXT NOT NULL,
                    communication_point_id TEXT,
                    data_station_id TEXT,
                    PRIMARY KEY(project_id,id),
                    FOREIGN KEY(project_id,id) REFERENCES signals(project_id,id)
                      ON DELETE RESTRICT,
                    FOREIGN KEY(project_id,communication_point_id)
                      REFERENCES communication_points(project_id,id) ON DELETE RESTRICT,
                    FOREIGN KEY(project_id,data_station_id)
                      REFERENCES data_stations(project_id,id) ON DELETE RESTRICT,
                    CHECK(communication_point_id IS NULL OR data_station_id IS NULL)
                ) WITHOUT ROWID""",
                """CREATE TABLE edit_journal_rows_v2(
                    event_id INTEGER NOT NULL REFERENCES edit_journal(event_id) ON DELETE CASCADE,
                    kind TEXT NOT NULL CHECK(kind IN ('communication_point','data_station','binding')),
                    entity_id TEXT NOT NULL, before_json TEXT, after_json TEXT,
                    PRIMARY KEY(event_id,kind,entity_id)
                ) WITHOUT ROWID""",
                """CREATE TABLE revisions_v2(
                    project_id TEXT NOT NULL REFERENCES projects(project_id),
                    digest TEXT NOT NULL, draft_generation INTEGER NOT NULL,
                    at_utc TEXT NOT NULL, body_zlib BLOB NOT NULL,
                    PRIMARY KEY(project_id,digest)
                ) WITHOUT ROWID""",
                "CREATE INDEX idx_point_instance ON communication_points(project_id,instance_id,id)",
                "CREATE INDEX idx_station_instance ON data_stations(project_id,instance_id,id)",
                "CREATE INDEX idx_binding_point ON signal_bindings(project_id,communication_point_id,id)",
                "CREATE INDEX idx_binding_station ON signal_bindings(project_id,data_station_id,id)",
            )
            for statement in statements:
                db.execute(statement)
            db.execute("PRAGMA user_version=2")
        self._db_version = 2
        return True

    def _require_v2(self):
        if self._db_version != 2:
            raise RuntimeError("Explicit migrate_v2() required; no automatic conversion")

    @staticmethod
    def _insert_statement(table, columns):
        return (f"INSERT INTO {table}(project_id,{','.join(columns)}) VALUES("
                + ",".join("?" for _ in range(len(columns)+1)) + ") "
                "ON CONFLICT(project_id,id) DO UPDATE SET "
                + ",".join(f"{col}=excluded.{col}" for col in columns[1:]))

    def _upsert_extra(self, con, project_id, kind, entries, event_id, seen):
        table, columns = _EXTRA[kind]
        statement = self._insert_statement(table, columns)
        total = 0
        for chunk in _chunks(entries):
            values = []
            for item in chunk:
                value = _extra_payload(kind, item)
                sid = value["id"]
                if type(sid) is not str or not sid or (kind,sid) in seen:
                    raise ValueError("Duplicate or empty v2 entity stable ID")
                seen.add((kind,sid))
                values.append(value)
            qs = ",".join("?" for _ in values)
            previous = dict(con.execute(
                f"SELECT id,record FROM {table} WHERE project_id=? AND id IN ({qs})",
                (project_id, *(v["id"] for v in values))))
            con.executemany(statement,
                            [(project_id,*_extra_columns(kind,v)) for v in values])
            con.executemany(
                "INSERT INTO edit_journal_rows_v2"
                "(event_id,kind,entity_id,before_json,after_json) VALUES(?,?,?,?,?)",
                [(event_id,kind,v["id"],previous.get(v["id"]),_json(v)) for v in values])
            total += len(values)
        return total

    def _delete_extra(self, con, project_id, kind, ids, event_id, seen):
        table = _EXTRA[kind][0]
        count = 0
        for batch in _chunks(ids):
            if len(set(batch)) != len(batch) or any(
                    type(s) is not str or not s or (kind,s) in seen for s in batch):
                raise ValueError("Duplicate or invalid v2 entity deletion")
            seen.update((kind,s) for s in batch)
            qs = ",".join("?" for _ in batch)
            previous = dict(con.execute(
                f"SELECT id,record FROM {table} WHERE project_id=? AND id IN ({qs})",
                (project_id,*batch)))
            if len(previous) != len(batch):
                raise KeyError("V2 entity not present")
            con.execute(f"DELETE FROM {table} WHERE project_id=? AND id IN ({qs})",
                        (project_id,*batch))
            con.executemany(
                "INSERT INTO edit_journal_rows_v2"
                "(event_id,kind,entity_id,before_json,after_json) VALUES(?,?,?,?,NULL)",
                [(event_id,kind,sid,previous[sid]) for sid in batch])
            count += len(batch)
        return count

    def apply_v2(self, project_id, *, expected_generation,
                 instances=(), communication_points=(), data_stations=(),
                 signals=(), signal_bindings=(), mappings=(),
                 delete_instances=(), delete_communication_points=(),
                 delete_data_stations=(), delete_signals=(), delete_signal_bindings=(),
                 delete_mappings=(), actor="engineer", reason="Save v2 Draft"):
        """One atomic v1+v2 incremental Save Draft and shared CAS/journal event."""
        self._require_v2()
        if type(expected_generation) is not int or expected_generation < 0:
            raise ValueError("Invalid expected_generation")
        if not actor or not reason:
            raise ValueError("Edit actor and reason required")
        with self._write() as con:
            generation = self._generation(con,project_id)
            if generation != expected_generation:
                raise EditConflict("Stale proposed v2 draft generation")
            con.execute("DELETE FROM edit_journal_rows_v2 WHERE event_id IN "
                        "(SELECT event_id FROM edit_journal WHERE project_id=? AND applied=0)",
                        (project_id,))
            con.execute("DELETE FROM edit_journal_rows WHERE event_id IN "
                        "(SELECT event_id FROM edit_journal WHERE project_id=? AND applied=0)",
                        (project_id,))
            con.execute("DELETE FROM edit_journal WHERE project_id=? AND applied=0",
                        (project_id,))
            event = con.execute(
                "INSERT INTO edit_journal(project_id,generation,actor,reason,at_utc)"
                " VALUES(?,?,?,?,?)",
                (project_id,generation+1,actor,reason,_time())).lastrowid
            seen = set()
            changes = 0
            for kind, ids in (
                ("mapping",delete_mappings), ("binding",delete_signal_bindings),
                ("signal",delete_signals),
                ("communication_point",delete_communication_points),
                ("data_station",delete_data_stations),
                ("instance",delete_instances),
            ):
                if kind in _EXTRA:
                    changes += self._delete_extra(con,project_id,kind,ids,event,seen)
                else:
                    changes += self._delete(con,project_id,kind,ids,event,seen=seen)
            for kind, items in (
                ("instance",instances),
                ("communication_point",communication_points),
                ("data_station",data_stations),
                ("signal",signals), ("binding",signal_bindings),
                ("mapping",mappings),
            ):
                if kind in _EXTRA:
                    changes += self._upsert_extra(con,project_id,kind,items,event,seen)
                else:
                    changes += self._upsert(con,project_id,kind,items,event,seen=seen)
            if changes == 0:
                raise ValueError("Empty v2 draft edit")
            con.execute("UPDATE projects SET draft_generation=? WHERE project_id=?",
                        (generation+1,project_id))
        return generation+1

    def _replay(self, con, event_id, project_id, column):
        """Override v1 replay to keep peer/binding rows atomic with signal edits."""
        if self._db_version != 2:
            return super()._replay(con,event_id,project_id,column)
        if column not in ("before_json","after_json"):
            raise ValueError("Invalid journal replay direction")
        before = con.execute(
            f"SELECT kind,entity_id,{column} FROM edit_journal_rows WHERE event_id=?",
            (event_id,)).fetchall()
        after = con.execute(
            f"SELECT kind,entity_id,{column} FROM edit_journal_rows_v2 WHERE event_id=?",
            (event_id,)).fetchall()
        rows = before + after
        drop_order = ("mapping","binding","signal","communication_point",
                      "data_station","instance")
        restore_order = tuple(reversed(drop_order))
        for kind in drop_order:
            table = (_EXTRA if kind in _EXTRA else _TABLES)[kind][0]
            for k, sid, payload in rows:
                if k == kind and payload is None:
                    con.execute(f"DELETE FROM {table} WHERE project_id=? AND id=?",
                                (project_id,sid))
        for kind in restore_order:
            entry = (_EXTRA if kind in _EXTRA else _TABLES)[kind]
            statement = self._insert_statement(*entry)
            for k, sid, payload in rows:
                if k == kind and payload is not None:
                    obj = json.loads(payload)
                    values = (_extra_columns(kind,obj) if kind in _EXTRA
                              else _columns(kind,obj))
                    con.execute(statement,(project_id,*values))

    @staticmethod
    def _snapshot_v2(con, project_id):
        from arpy_gateway.domain.v2 import (
            CommunicationPoint, DataStation, EngineeringProjectV2, SignalBinding
        )
        base = EngineeringStore._snapshot(con,project_id)
        points = [CommunicationPoint(**json.loads(r[0])) for r in con.execute(
            "SELECT record FROM communication_points WHERE project_id=? ORDER BY id",
            (project_id,))]
        stations = [DataStation(**json.loads(r[0])) for r in con.execute(
            "SELECT record FROM data_stations WHERE project_id=? ORDER BY id",
            (project_id,))]
        bindings = []
        for row in con.execute(
                "SELECT record FROM signal_bindings WHERE project_id=? ORDER BY id",
                (project_id,)):
            data=json.loads(row[0])
            bindings.append(SignalBinding(
                data["id"],data["communication_point_id"],data["data_station_id"]))
        return EngineeringProjectV2(base,tuple(points),tuple(stations),tuple(bindings))

    def load_draft_v2(self, project_id):
        self._require_v2()
        with self._read() as con:
            return self._snapshot_v2(con,project_id)

    def stage(self,*args,**kwargs):
        if self._db_version == 2:
            raise RuntimeError("Cannot stage incomplete v1 subset from v2 database; use stage_v2")
        return super().stage(*args,**kwargs)

    def stage_v2(self, project_id, *, expected_generation):
        from arpy_gateway.contracts.project_v2 import (
            canonical_project_digest_v2,encode_project_v2)
        from arpy_gateway.domain.validation_v2 import validate_project_v2
        self._require_v2()
        with self._write() as con:
            gen = self._generation(con,project_id)
            if gen != expected_generation:
                raise EditConflict("Draft changed during v2 validation")
            snapshot = self._snapshot_v2(con,project_id)
            problems = validate_project_v2(snapshot)
            if problems:
                raise StageRejected(problems)
            digest = canonical_project_digest_v2(snapshot)
            data = zlib.compress(_json(encode_project_v2(snapshot)).encode("utf-8"),3)
            con.execute(
                "INSERT OR IGNORE INTO revisions_v2"
                "(project_id,digest,draft_generation,at_utc,body_zlib) VALUES(?,?,?,?,?)",
                (project_id,digest,gen,_time(),data))
            row=con.execute(
                "SELECT draft_generation,at_utc,LENGTH(body_zlib) "
                "FROM revisions_v2 WHERE project_id=? AND digest=?",
                (project_id,digest)).fetchone()
            return RevisionRecord(digest,project_id,row[0],row[1],row[2])

    def revision_v2(self,project_id,digest):
        from arpy_gateway.contracts.project_v2 import (
            canonical_project_digest_v2,decode_project_v2)
        self._require_v2()
        with self._read() as con:
            row=con.execute(
                "SELECT body_zlib FROM revisions_v2 WHERE project_id=? AND digest=?",
                (project_id,digest)).fetchone()
            if row is None:
                raise KeyError("Unknown immutable v2 revision")
        snapshot=decode_project_v2(json.loads(zlib.decompress(row[0]).decode("utf-8")))
        if canonical_project_digest_v2(snapshot) != digest:
            raise ValueError("Staged v2 digest mismatch")
        return snapshot
