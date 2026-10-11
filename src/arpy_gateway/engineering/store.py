"""Durable v1 engineering project repository, with independent revision staging.

Owned by STORE lane #3. SQLite WAL, one serialized writer, bounded readers,
immutable SHA-256 validated revisions, batch stable-ID edits and durable
undo/redo. No Qt, runtime or Modbus dependency. Never auto-deploy.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import threading
from typing import Iterable, Iterator
import zlib

from arpy_gateway.contracts.codec import (
    canonical_project_digest, decode_project, encode_project,
)
from arpy_gateway.domain.models import (
    MappingDefinition, ProjectSnapshot, ProtocolInstance, ProtocolSignal,
)
from arpy_gateway.domain.validation import validate_project


class EditConflict(RuntimeError):
    """Draft generation did not match the caller's last observed generation."""


class StageRejected(ValueError):
    def __init__(self, issues):
        self.issues = tuple(issues)
        super().__init__("Project cannot stage: " +
                         ", ".join(x.code for x in self.issues[:8]))


@dataclass(frozen=True)
class RevisionRecord:
    digest: str
    project_id: str
    draft_generation: int
    created_at: str
    bytes_compressed: int


SCHEMA_VERSION = 1
LIMIT = 256
CHUNK = 500


def _json(obj):
    return json.dumps(obj, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, allow_nan=False)


def _time():
    return datetime.now(timezone.utc).isoformat()


def _as_payload(kind, obj):
    if kind == "instance":
        if type(obj) is not ProtocolInstance:
            raise TypeError("Expected ProtocolInstance")
        return encode_project(ProjectSnapshot("", instances=(obj,)))["instances"][0]
    if kind == "signal":
        if type(obj) is not ProtocolSignal:
            raise TypeError("Expected ProtocolSignal")
        return encode_project(ProjectSnapshot("", signals=(obj,)))["signals"][0]
    if kind == "mapping":
        if type(obj) is not MappingDefinition:
            raise TypeError("Expected MappingDefinition")
        return encode_project(ProjectSnapshot("", mappings=(obj,)))["mappings"][0]
    raise ValueError("Invalid entity kind")


# SQL column order equals primary identity, canonical record and indexes.
_TABLES = {
    "instance": (
        "instances", ("id", "record", "protocol", "role", "enabled"),
    ),
    "signal": (
        "signals", ("id", "record", "instance_id", "name", "family", "enabled"),
    ),
    "mapping": (
        "mappings", ("id", "record", "source_signal_id",
                     "destination_signal_id", "enabled"),
    ),
}


def _columns(kind, item):
    if kind == "instance":
        return (item["id"], _json(item), item["protocol"],
                item["role"], int(item["enabled"]))
    if kind == "signal":
        return (item["id"], _json(item), item["instance_id"],
                item["name"], item["family"], int(item["enabled"]))
    return (item["id"], _json(item), item["source_signal_id"],
            item["destination_signal_id"], int(item["enabled"]))


def _chunks(values: Iterable, length: int = CHUNK) -> Iterator[list]:
    buf = []
    for item in values:
        buf.append(item)
        if len(buf) >= length:
            yield buf
            buf = []
    if buf:
        yield buf


class EngineeringStore:
    """One ordered writer (RLock); short-lived bounded independent readers.

    Call methods from an IO/engineering worker, NEVER the Qt event thread.
    Opening a database never starts field I/O; staged revisions are artifacts
    awaiting separately authorized runtime Apply (not available here).
    """

    def __init__(self, path: str | Path, *, max_readers: int = 2):
        if type(max_readers) is not int or not 1 <= max_readers <= 8:
            raise ValueError("max_readers must be 1..8")
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._writer_lock = threading.RLock()
        self._reader_slots = threading.BoundedSemaphore(max_readers)
        self._closed = False
        self._writer = sqlite3.connect(str(self.path), timeout=5,
                                        isolation_level=None,
                                        check_same_thread=False)
        self._writer.execute("PRAGMA foreign_keys=ON")
        self._writer.execute("PRAGMA busy_timeout=5000")
        self._writer.execute("PRAGMA journal_mode=WAL")
        try:
            self._writer.execute("PRAGMA synchronous=FULL")
            self._initialize()
        except BaseException:
            # Windows keeps an open SQLite handle locked on constructor failure.
            # Unsupported schema/failed migration must never leak the file.
            self._writer.close()
            self._closed = True
            raise

    def _initialize(self):
        version = self._writer.execute("PRAGMA user_version").fetchone()[0]
        if version not in (0, SCHEMA_VERSION):
            raise RuntimeError(
                f"Database schema {version} is unsupported; migration required")
        if version == SCHEMA_VERSION:
            return
        self._writer.executescript("""
            BEGIN IMMEDIATE;
            CREATE TABLE projects(
              project_id TEXT PRIMARY KEY NOT NULL,
              draft_generation INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE instances(
              project_id TEXT NOT NULL, id TEXT NOT NULL,
              record TEXT NOT NULL, protocol TEXT NOT NULL,
              role TEXT NOT NULL, enabled INTEGER NOT NULL CHECK(enabled IN (0,1)),
              PRIMARY KEY(project_id,id),
              FOREIGN KEY(project_id) REFERENCES projects(project_id) ON DELETE RESTRICT
            ) WITHOUT ROWID;
            CREATE TABLE signals(
              project_id TEXT NOT NULL, id TEXT NOT NULL,
              record TEXT NOT NULL, instance_id TEXT NOT NULL,
              name TEXT NOT NULL, family TEXT NOT NULL,
              enabled INTEGER NOT NULL CHECK(enabled IN (0,1)),
              PRIMARY KEY(project_id,id),
              FOREIGN KEY(project_id,instance_id) REFERENCES instances(project_id,id)
                 ON DELETE RESTRICT
            ) WITHOUT ROWID;
            CREATE TABLE mappings(
              project_id TEXT NOT NULL, id TEXT NOT NULL,
              record TEXT NOT NULL, source_signal_id TEXT NOT NULL,
              destination_signal_id TEXT NOT NULL,
              enabled INTEGER NOT NULL CHECK(enabled IN (0,1)),
              PRIMARY KEY(project_id,id),
              FOREIGN KEY(project_id,source_signal_id) REFERENCES signals(project_id,id)
                 ON DELETE RESTRICT,
              FOREIGN KEY(project_id,destination_signal_id) REFERENCES signals(project_id,id)
                 ON DELETE RESTRICT
            ) WITHOUT ROWID;
            CREATE INDEX idx_instance_proto ON instances(project_id,protocol,id);
            CREATE INDEX idx_signal_owner ON signals(project_id,instance_id,id);
            CREATE INDEX idx_signal_name ON signals(project_id,instance_id,name,id);
            CREATE INDEX idx_mapping_source ON mappings(project_id,source_signal_id,id);
            CREATE INDEX idx_mapping_destination ON mappings(project_id,destination_signal_id,id);
            CREATE TABLE edit_journal(
              event_id INTEGER PRIMARY KEY AUTOINCREMENT,
              project_id TEXT NOT NULL REFERENCES projects(project_id),
              generation INTEGER NOT NULL,
              actor TEXT NOT NULL, reason TEXT NOT NULL, at_utc TEXT NOT NULL,
              applied INTEGER NOT NULL DEFAULT 1 CHECK(applied IN (0,1))
            );
            CREATE INDEX idx_journal_project ON edit_journal(project_id,event_id DESC);
            CREATE TABLE edit_journal_rows(
              event_id INTEGER NOT NULL REFERENCES edit_journal(event_id) ON DELETE CASCADE,
              kind TEXT NOT NULL CHECK(kind IN ('instance','signal','mapping')),
              entity_id TEXT NOT NULL, before_json TEXT, after_json TEXT,
              PRIMARY KEY(event_id,kind,entity_id)
            ) WITHOUT ROWID;
            CREATE TABLE revisions(
              project_id TEXT NOT NULL REFERENCES projects(project_id),
              digest TEXT NOT NULL, draft_generation INTEGER NOT NULL,
              at_utc TEXT NOT NULL, body_zlib BLOB NOT NULL,
              PRIMARY KEY(project_id,digest)
            ) WITHOUT ROWID;
            PRAGMA user_version=1;
            COMMIT;
        """)

    def _ensure_open(self):
        if self._closed:
            raise RuntimeError("EngineeringStore closed")

    @contextmanager
    def _write(self):
        with self._writer_lock:
            self._ensure_open()
            self._writer.execute("BEGIN IMMEDIATE")
            try:
                yield self._writer
            except BaseException:
                self._writer.execute("ROLLBACK")
                raise
            else:
                self._writer.execute("COMMIT")

    @contextmanager
    def _read(self):
        self._ensure_open()
        with self._reader_slots:
            connection = sqlite3.connect(
                str(self.path), isolation_level=None, timeout=5)
            connection.execute("PRAGMA query_only=ON")
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute("PRAGMA busy_timeout=5000")
            try:
                connection.execute("BEGIN")
                yield connection
            finally:
                connection.execute("ROLLBACK")
                connection.close()

    def create_project(self, project_id: str):
        if not isinstance(project_id, str) or not project_id.strip():
            raise ValueError("Project ID required")
        with self._write() as con:
            con.execute("INSERT OR IGNORE INTO projects(project_id) VALUES(?)",
                        (project_id,))

    @staticmethod
    def _generation(con, project_id):
        item = con.execute("SELECT draft_generation FROM projects WHERE project_id=?",
                           (project_id,)).fetchone()
        if item is None:
            raise KeyError("Unknown project ID")
        return item[0]

    def generation(self, project_id):
        with self._read() as con:
            return self._generation(con, project_id)

    def _upsert(self, con, project_id, kind, entries, journal_id, *, seen):
        table, columns = _TABLES[kind]
        sql = (f"INSERT INTO {table}(project_id,{','.join(columns)}) "
               f"VALUES({','.join('?' for _ in range(len(columns)+1))}) "
               f"ON CONFLICT(project_id,id) DO UPDATE SET " +
               ",".join(f"{col}=excluded.{col}" for col in columns[1:]))
        count = 0
        for batch in _chunks(entries):
            encoded = []
            for obj in batch:
                payload = _as_payload(kind, obj)
                sid = payload["id"]
                if not sid or (kind,sid) in seen:
                    raise ValueError("Duplicate/empty stable ID in batch")
                seen.add((kind,sid))
                encoded.append(payload)
            qs = ",".join("?" for _ in encoded)
            old = dict(con.execute(
                f"SELECT id,record FROM {table} WHERE project_id=? AND id IN ({qs})",
                (project_id, *(p["id"] for p in encoded))))
            rows = [(project_id, *_columns(kind,p)) for p in encoded]
            con.executemany(sql, rows)
            con.executemany(
                "INSERT INTO edit_journal_rows(event_id,kind,entity_id,before_json,after_json)"
                " VALUES(?,?,?,?,?)",
                [(journal_id, kind, p["id"], old.get(p["id"]), _json(p)) for p in encoded])
            count += len(encoded)
        return count

    def _delete(self, con, project_id, kind, ids, journal_id, *, seen):
        table = _TABLES[kind][0]
        count = 0
        for batch in _chunks(ids):
            if any(type(x) is not str or not x for x in batch):
                raise ValueError("Invalid stable ID for deletion")
            if any((kind,x) in seen for x in batch) or len(batch) != len(set(batch)):
                raise ValueError("Duplicate edit to same ID")
            seen.update((kind,x) for x in batch)
            qs = ",".join("?" for _ in batch)
            old = dict(con.execute(
                f"SELECT id,record FROM {table} WHERE project_id=? AND id IN ({qs})",
                (project_id,*batch)))
            if len(old) != len(batch):
                raise KeyError("Cannot delete missing stable ID")
            con.execute(
                f"DELETE FROM {table} WHERE project_id=? AND id IN ({qs})",
                (project_id,*batch))
            con.executemany(
                "INSERT INTO edit_journal_rows(event_id,kind,entity_id,before_json,after_json)"
                " VALUES(?,?,?,?,NULL)",
                [(journal_id,kind,sid,old[sid]) for sid in batch])
            count += len(batch)
        return count

    def apply(self, project_id: str, *, instances=(), signals=(), mappings=(),
              delete_instances=(), delete_signals=(), delete_mappings=(),
              expected_generation: int, actor="engineer", reason="Save Draft"):
        """Atomic incremental edits, O(changed rows), no per-edit full snapshot.

        Does not validate missing protocol address semantics until stage.
        Foreign-key ownership remains strict even for an incomplete draft.
        Returns increased durable draft generation; never deploys.
        """
        if type(expected_generation) is not int or expected_generation < 0:
            raise ValueError("expected_generation must be nonnegative integer")
        if not actor or not reason:
            raise ValueError("actor and reason required")
        with self._write() as con:
            old_gen = self._generation(con, project_id)
            if old_gen != expected_generation:
                raise EditConflict(f"Stale draft: expected {expected_generation}; "
                                   f"actual {old_gen}")
            con.execute("DELETE FROM edit_journal_rows WHERE event_id IN "
                        "(SELECT event_id FROM edit_journal WHERE project_id=? AND applied=0)",
                        (project_id,))
            con.execute("DELETE FROM edit_journal WHERE project_id=? AND applied=0",
                        (project_id,))
            event = con.execute(
                "INSERT INTO edit_journal(project_id,generation,actor,reason,at_utc)"
                " VALUES(?,?,?,?,?)",
                (project_id,old_gen+1,actor,reason,_time())).lastrowid
            seen = set()
            changes = 0
            # Deletes in reverse dependency order; UPSERTs in dependency order.
            for kind, keys in (("mapping",delete_mappings),
                               ("signal",delete_signals),
                               ("instance",delete_instances)):
                changes += self._delete(con,project_id,kind,keys,event,seen=seen)
            for kind, entries in (("instance",instances),("signal",signals),
                                   ("mapping",mappings)):
                changes += self._upsert(con,project_id,kind,entries,event,seen=seen)
            if not changes:
                raise ValueError("Empty draft edit")
            con.execute("UPDATE projects SET draft_generation=? WHERE project_id=?",
                        (old_gen+1,project_id))
        return old_gen+1

    def _replay(self, con, event_id, project_id, column):
        if column not in ("before_json", "after_json"):
            raise ValueError("Bad replay direction")
        rows = con.execute(
            f"SELECT kind,entity_id,{column} FROM edit_journal_rows WHERE event_id=?",
            (event_id,)).fetchall()
        # Remove mapping→signal→instance; then create instance→signal→mapping.
        for kind in ("mapping","signal","instance"):
            table = _TABLES[kind][0]
            for _, sid, payload in (r for r in rows if r[0]==kind and r[2] is None):
                con.execute(f"DELETE FROM {table} WHERE project_id=? AND id=?",
                            (project_id,sid))
        for kind in ("instance","signal","mapping"):
            table, fields = _TABLES[kind]
            statement = (f"INSERT INTO {table}(project_id,{','.join(fields)}) VALUES("
                         + ",".join("?" for _ in range(len(fields)+1)) + ") "
                         "ON CONFLICT(project_id,id) DO UPDATE SET "
                         + ",".join(f"{col}=excluded.{col}" for col in fields[1:]))
            for _, sid, payload in (r for r in rows if r[0]==kind and r[2] is not None):
                con.execute(statement,
                            (project_id,*_columns(kind,json.loads(payload))))

    def undo(self, project_id: str, *, expected_generation: int):
        with self._write() as con:
            current = self._generation(con,project_id)
            if current != expected_generation:
                raise EditConflict("Draft changed since requested undo")
            entry = con.execute(
                "SELECT event_id FROM edit_journal WHERE project_id=? AND applied=1"
                " ORDER BY event_id DESC LIMIT 1",(project_id,)).fetchone()
            if not entry:
                raise ValueError("Nothing to undo")
            self._replay(con,entry[0],project_id,"before_json")
            con.execute("UPDATE edit_journal SET applied=0 WHERE event_id=?",entry)
            con.execute("UPDATE projects SET draft_generation=? WHERE project_id=?",
                        (current+1,project_id))
            return current+1

    def redo(self, project_id: str, *, expected_generation: int):
        with self._write() as con:
            current = self._generation(con,project_id)
            if current != expected_generation:
                raise EditConflict("Draft changed since requested redo")
            entry = con.execute(
                "SELECT event_id FROM edit_journal WHERE project_id=? AND applied=0"
                " ORDER BY event_id ASC LIMIT 1",(project_id,)).fetchone()
            if not entry:
                raise ValueError("Nothing to redo")
            self._replay(con,entry[0],project_id,"after_json")
            con.execute("UPDATE edit_journal SET applied=1 WHERE event_id=?",entry)
            con.execute("UPDATE projects SET draft_generation=? WHERE project_id=?",
                        (current+1,project_id))
            return current+1

    @staticmethod
    def _snapshot(con, project_id):
        EngineeringStore._generation(con,project_id)
        sections = []
        for kind in ("instance","signal","mapping"):
            table=_TABLES[kind][0]
            sections.append([json.loads(r[0]) for r in con.execute(
                f"SELECT record FROM {table} WHERE project_id=? ORDER BY id",
                (project_id,))])
        return decode_project({
            "schema_version":1,"project_id":project_id,
            "instances":sections[0],"signals":sections[1],"mappings":sections[2]
        })

    def load_draft(self, project_id: str) -> ProjectSnapshot:
        """Full snapshot for off-thread validation/export; not for Qt grid."""
        with self._read() as con:
            return self._snapshot(con,project_id)

    def signal_page(self, project_id: str, instance_id: str, *,
                    after_id: str = "", limit: int = LIMIT):
        """Keyset pagination by stable ID: no OFFSET scan at 500k rows."""
        if type(limit) is not int or not 1 <= limit <= LIMIT:
            raise ValueError("Page size must be 1..256")
        with self._read() as con:
            rows = con.execute(
                "SELECT record FROM signals WHERE project_id=? "
                "AND instance_id=? AND id>? ORDER BY id LIMIT ?",
                (project_id,instance_id,after_id,limit)).fetchall()
        return tuple(x[0] for x in (decode_project({
            "schema_version":1,"project_id":project_id,
            "instances":[],"signals":[json.loads(r[0])],"mappings":[]
        }).signals for r in rows))

    def count_signals(self,project_id,instance_id):
        with self._read() as con:
            return con.execute(
                "SELECT COUNT(*) FROM signals WHERE project_id=? AND instance_id=?",
                (project_id,instance_id)).fetchone()[0]

    def stage(self, project_id: str, *, expected_generation: int) -> RevisionRecord:
        """Validate and freeze immutable v1 snapshot, NEVER deploy/activate.

        Holds single writer lock while validating/staging to eliminate race with
        concurrent edits; run on a background Store worker, not UI thread.
        """
        with self._write() as con:
            gen=self._generation(con,project_id)
            if gen!=expected_generation:
                raise EditConflict("Draft changed while validation was requested")
            snapshot=self._snapshot(con,project_id)
            issues=validate_project(snapshot)
            if issues:
                raise StageRejected(issues)
            digest=canonical_project_digest(snapshot)
            payload=_json(encode_project(snapshot)).encode("utf-8")
            zipped=zlib.compress(payload,level=3)
            at=_time()
            con.execute(
                "INSERT OR IGNORE INTO revisions"
                "(project_id,digest,draft_generation,at_utc,body_zlib)"
                " VALUES(?,?,?,?,?)",(project_id,digest,gen,at,zipped))
            data=con.execute(
                "SELECT draft_generation,at_utc,LENGTH(body_zlib) "
                "FROM revisions WHERE project_id=? AND digest=?",
                (project_id,digest)).fetchone()
            return RevisionRecord(digest,project_id,data[0],data[1],data[2])

    def revision(self, project_id: str, digest: str) -> ProjectSnapshot:
        with self._read() as con:
            row=con.execute(
                "SELECT body_zlib FROM revisions WHERE project_id=? AND digest=?",
                (project_id,digest)).fetchone()
            if row is None:
                raise KeyError("Unknown immutable revision")
        snapshot=decode_project(json.loads(zlib.decompress(row[0]).decode("utf-8")))
        if canonical_project_digest(snapshot)!=digest:
            raise ValueError("Stored revision digest mismatch")
        return snapshot

    def close(self):
        with self._writer_lock:
            if not self._closed:
                self._closed=True
                self._writer.close()

    def __enter__(self):
        return self

    def __exit__(self,*_):
        self.close()
