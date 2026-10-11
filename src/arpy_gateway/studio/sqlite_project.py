"""Single-source SQLite v2 engineering read/write facade for Studio.

All methods are worker-only. No transport, gateway runtime, or DEMO
dependencies. Existing user's engineering.sqlite3 is opened in place: the
authoritative stable IDs are never copied, renamed or seeded from GUI demo.

Source-of-truth revisions and atomic writes are owned by EngineeringStoreV2.
This facade performs bounded, read-only keyset projections in a single SQLite
snapshot; the Store's existing public APIs perform all mutations.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
import json
from pathlib import Path
from uuid import uuid4

from arpy_gateway.contracts.codec import decode_project
from arpy_gateway.domain.models import (
    InstanceRole, MappingDefinition, ProtocolSignal, ProjectSnapshot,
    ProtocolInstance, SignalDirection, SignalFamily, ValueType,
)
from arpy_gateway.domain.v2 import (
    CommunicationPoint, DataStation, SignalBinding,
)
from .persisted_devices import TCPDeviceRepository, TCPDeviceEdit

_LIMIT = 128


@dataclass(frozen=True)
class Topology:
    generation: int
    instance_rows: tuple[ProtocolInstance, ...]
    point_rows: tuple[CommunicationPoint, ...]
    station_rows: tuple[DataStation, ...]

    def instances(self, include_disabled=False):
        return (self.instance_rows if include_disabled else
                tuple(i for i in self.instance_rows if i.enabled))

    def points(self, instance_id):
        return tuple(p for p in self.point_rows if p.instance_id == instance_id)

    def stations(self, instance_id):
        return tuple(s for s in self.station_rows if s.instance_id == instance_id)


class SQLiteEngineeringProject:
    """Studio's ONLY engineering project source while in installed mode.

    Display model refreshes after committed SQLite transactions. The prior
    DemoProvider is available only when the operator explicitly runs --demo.
    This facade intentionally does not connect, poll, or Deploy any IED.
    """

    def __init__(self, path: Path):
        self.devices = TCPDeviceRepository(path)
        self.path = Path(path)
        self.project_id = self.devices.project_id

    def _store(self):
        return self.devices._store()

    def prepare(self):
        self.devices.prepare()
        return self.topology()

    def topology(self):
        with self._store() as store:
            with store._read() as db:
                generation = store._generation(db, self.project_id)
                # Protocol instances are bounded by gateway capacity; never
                # materialize tens of thousands of tree items by accident.
                raw_instances = [json.loads(row[0]) for row in db.execute(
                    "SELECT record FROM instances WHERE project_id=? ORDER BY id LIMIT 65",
                    (self.project_id,))]
                if len(raw_instances) > 64:
                    raise RuntimeError("Too many protocol instances for engineering tree")
                base = decode_project({
                    "schema_version": 1, "project_id": self.project_id,
                    "instances": raw_instances, "signals": [], "mappings": [],
                })
                points = tuple(CommunicationPoint(**json.loads(row[0]))
                               for row in db.execute(
                    "SELECT record FROM communication_points WHERE project_id=? "
                    "ORDER BY instance_id,id LIMIT 2001", (self.project_id,)))
                stations = tuple(DataStation(**json.loads(row[0]))
                                 for row in db.execute(
                    "SELECT record FROM data_stations WHERE project_id=? "
                    "ORDER BY instance_id,id LIMIT 2001", (self.project_id,)))
                if len(points) > 2000 or len(stations) > 2000:
                    raise RuntimeError("Too many topology nodes: use scoped device paging")
                return Topology(generation, base.instances, points, stations)

    def connections(self, instance_id, *, after_id="", limit=_LIMIT):
        with self._store() as store:
            return store.communication_point_page(
                self.project_id, instance_id, after_id=after_id, limit=limit)

    def stations(self, instance_id, *, after_id="", limit=_LIMIT):
        with self._store() as store:
            return store.data_station_page(
                self.project_id, instance_id, after_id=after_id, limit=limit)

    def signal_page(self, instance_id, *, after_id="", limit=_LIMIT,
                    communication_point_id=None, data_station_id=None):
        """One SQLite read transaction for signal+owner identity, O(page).

        For an IED/station page use the indexed binding owner first, not an
        unbounded scan across all configured signals in that instance.
        """
        if type(limit) is not int or not 1 <= limit <= _LIMIT:
            raise ValueError("Signal page limit must be between 1..128")
        if communication_point_id and data_station_id:
            raise ValueError("Select a device OR a station, not both")
        with self._store() as store:
            with store._read() as db:
                generation = store._generation(db, self.project_id)
                if communication_point_id or data_station_id:
                    field = ("communication_point_id" if communication_point_id
                             else "data_station_id")
                    target = communication_point_id or data_station_id
                    # Both fields are fixed strings selected by code, never
                    # interpolated user column identifiers.
                    rows = db.execute(
                        "SELECT s.record, b.communication_point_id, b.data_station_id "
                        "FROM signal_bindings b JOIN signals s "
                        "ON s.project_id=b.project_id AND s.id=b.id "
                        "WHERE b.project_id=? AND s.instance_id=? "
                        f"AND b.{field}=? AND b.id>? ORDER BY b.id LIMIT ?",
                        (self.project_id, instance_id, target, after_id, limit+1),
                    ).fetchall()
                else:
                    rows = db.execute(
                        "SELECT s.record,b.communication_point_id,b.data_station_id "
                        "FROM signals s LEFT JOIN signal_bindings b "
                        "ON s.project_id=b.project_id AND s.id=b.id "
                        "WHERE s.project_id=? AND s.instance_id=? AND s.id>? "
                        "ORDER BY s.id LIMIT ?",
                        (self.project_id, instance_id, after_id, limit+1),
                    ).fetchall()
                has_more = len(rows) > limit
                rows = rows[:limit]
        decoded = decode_project({
            "schema_version":1, "project_id":self.project_id,
            "instances":[], "signals":[json.loads(row[0]) for row in rows],
            "mappings":[],
        }).signals
        return {"generation":generation, "has_more":has_more,
                "rows":tuple((sig, row[1], row[2]) for sig,row in zip(decoded,rows))}

    def mapping_page(self, *, after_id="", limit=_LIMIT):
        if type(limit) is not int or not 1 <= limit <= _LIMIT:
            raise ValueError("Mapping page limit out of range")
        with self._store() as store:
            with store._read() as db:
                generation=store._generation(db,self.project_id)
                rows=db.execute(
                    "SELECT record FROM mappings WHERE project_id=? AND id>? "
                    "ORDER BY id LIMIT ?",
                    (self.project_id,after_id,limit+1)).fetchall()
        has_more=len(rows)>limit
        records=[json.loads(r[0]) for r in rows[:limit]]
        items=decode_project({
            "schema_version":1,"project_id":self.project_id,
            "instances":[],"signals":[],"mappings":records,
        }).mappings
        return {"generation":generation, "rows":items, "has_more":has_more}

    def save_instance(self, instance: ProtocolInstance, *,
                      expected_generation: int):
        """One authoritative SQLite edit for instance name/status/settings."""
        if not instance.name.strip():
            raise ValueError("Protocol instance name cannot be empty")
        with self._store() as store:
            generation = store.apply_v2(
                self.project_id, expected_generation=expected_generation,
                instances=(instance,), actor="studio",
                reason="Save Protocol Instance draft",
            )
        return {"generation": generation, "id": instance.id}

    def save_tcp_device(self, details: TCPDeviceEdit, *,
                        expected_generation:int, original=None):
        return self.devices.save(details,expected_generation=expected_generation,
                                 original=original)

    def save_signal(self, instance_id, scope_id, record: dict, *,
                    expected_generation:int, original=None):
        """Save every editable signal field and its device/station binding.

        Protocol-specific parameters remain canonical v1 JSON, stable owner is
        stored in v2 signal_bindings. No peer label or row-position inference.
        """
        name = str(record["name"]).strip()
        if not name:
            raise ValueError("Signal name is required")
        family = SignalFamily(record["family"])
        dtype = ValueType(record["value_type"])
        direction = SignalDirection(record["direction"])
        params = {
            "register_table":str(record["register_table"]),
            "native_address":int(record["native_address"]),
        }
        if params["register_table"] not in ("holding","input","coil","discrete"):
            raise ValueError("Unsupported Modbus register table")
        if not 0 <= params["native_address"] <= 65535:
            raise ValueError("Native Modbus address must be 0..65535")
        if family is SignalFamily.BINARY and dtype is not ValueType.BOOL:
            raise ValueError("Binary point requires boolean value type")
        if family is SignalFamily.COMMAND or direction is not SignalDirection.INBOUND:
            raise ValueError("Only read-only Modbus input telemetry is supported")
        if scope_id is None or not scope_id:
            raise ValueError("Select an IED before saving a Modbus signal")
        signal = ProtocolSignal(
            original.id if original else "signal-"+uuid4().hex,
            instance_id, name, family, dtype, direction,
            bool(record["enabled"]),str(record["unit"]).strip(),params,
        )
        binding = SignalBinding(signal.id, communication_point_id=scope_id)
        with self._store() as store:
            generation=store.apply_v2(
                self.project_id,expected_generation=expected_generation,
                signals=(signal,),signal_bindings=(binding,),
                actor="studio",reason="Save Modbus signal draft",
            )
        return {"generation":generation,"id":signal.id}

    def save_station(self, instance_id, name, common_address, *,
                     expected_generation, original=None):
        if type(common_address) is not int or not 1<=common_address<=65534:
            raise ValueError("IEC104 Common Address must be 1..65534")
        if not name.strip():
            raise ValueError("Station name required")
        record = DataStation(
            original.id if original else "station-"+uuid4().hex,
            instance_id,name.strip(), True,{"common_address":common_address})
        with self._store() as store:
            generation=store.apply_v2(
                self.project_id,expected_generation=expected_generation,
                data_stations=(record,),actor="studio",reason="Save IEC104 station draft")
        return {"generation":generation,"id":record.id}

    def save_mapping(self, source_id, destination_id, *, expected_generation):
        if not source_id or not destination_id or source_id==destination_id:
            raise ValueError("Select two different saved stable signal IDs")
        record=MappingDefinition("mapping-"+uuid4().hex,source_id,destination_id)
        with self._store() as store:
            generation=store.apply_v2(
                self.project_id,expected_generation=expected_generation,
                mappings=(record,),actor="studio",reason="Save mapping draft")
        return {"generation":generation,"id":record.id}

    def stage(self, *, expected_generation):
        return self.devices.stage(expected_generation=expected_generation)
