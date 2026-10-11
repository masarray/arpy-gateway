"""GUI-only protocol engineering projection with stable connection/station references.

This is a mock engineering repository for issue #4, NOT a domain schema extension.
Canonical ConnectionPoint/DataStation contracts require ARCH RFC #13 and STORE #3.
All mutations must be dispatched via the serialized Studio worker.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from threading import RLock
from typing import Any, Optional
from uuid import uuid4

from arpy_gateway.domain.models import InstanceRole
from .demo import DemoProvider, Field

MAX_ASSIGN = 2000
ADDRESS_MOD = 30_000


@dataclass(frozen=True)
class CommunicationPoint:
    id: str
    instance_id: str
    name: str
    kind: str                   # modbus_ied / rtu_slave / allowed_master
    settings: tuple[tuple[str, Any], ...]

    def params(self) -> dict:
        return dict(self.settings)


@dataclass(frozen=True)
class DataStation:
    id: str
    instance_id: str
    name: str
    address: int


@dataclass
class SelectionContext:
    workspace: int = 0
    instance_id: str = ""
    scope_id: str = ""
    node_type: str = ""
    signal_id: str = ""
    mapping_id: str = ""
    back: list[tuple[int, str, str, str, str, str]] = field(default_factory=list)

    def save(self):
        self.back.append((self.workspace, self.instance_id, self.scope_id,
                          self.node_type, self.signal_id, self.mapping_id))
        del self.back[:-24]

    def restore(self):
        if not self.back:
            return False
        (self.workspace, self.instance_id, self.scope_id, self.node_type,
         self.signal_id, self.mapping_id) = self.back.pop()
        return True


class EngineeringView:
    """Small protocol topology indexes and identity-scoped virtual signal access."""

    def __init__(self, demo: Optional[DemoProvider] = None):
        self.demo = demo or DemoProvider()
        self._lock = RLock()
        self._peers: dict[str, list[CommunicationPoint]] = {
            "meter01": [
                CommunicationPoint("meter01:ied:1", "meter01", "Meter 01",
                                   "modbus_ied", (("host", "192.168.1.10"), ("port", 502), ("unit_id", 1))),
                CommunicationPoint("meter01:ied:2", "meter01", "Meter 02",
                                   "modbus_ied", (("host", "192.168.1.11"), ("port", 502), ("unit_id", 1))),
            ],
            "scada104": [
                CommunicationPoint("scada104:peer:1", "scada104", "SCADA Master A",
                                   "allowed_master", (("ip", "10.10.1.10"), ("enabled", True))),
                CommunicationPoint("scada104:peer:2", "scada104", "SCADA Master B",
                                   "allowed_master", (("ip", "10.10.1.11"), ("enabled", True))),
            ],
        }
        self._stations: dict[str, list[DataStation]] = {
            "scada104": [DataStation("scada104:ca:1", "scada104", "ASDU Station CA 1", 1)],
            "mirror": [DataStation("mirror:unit:1", "mirror", "Local Unit ID 1", 1)],
        }
        self._seeded: dict[str, tuple[str, ...]] = {
            "meter01": ("meter01:ied:1", "meter01:ied:2"),
            "scada104": (),  # starts with empty IEC104 destination signal catalog
            "mirror": ("mirror:unit:1",),
        }
        self._extra_owners: dict[tuple[str, int], str] = {}
        self._point_by_id = {p.id: p for peers in self._peers.values() for p in peers}
        self._station_by_id = {x.id: x for stations in self._stations.values() for x in stations}

    def instances(self, include_disabled: bool = False):
        items = self.demo.instances()
        return items if include_disabled else tuple(x for x in items if x.enabled)

    def points(self, instance_id: str) -> tuple[CommunicationPoint, ...]:
        with self._lock:
            return tuple(self._peers.get(instance_id, ()))

    def stations(self, instance_id: str) -> tuple[DataStation, ...]:
        with self._lock:
            return tuple(self._stations.get(instance_id, ()))

    def add_point(self, instance_id: str, name: str, host: str) -> CommunicationPoint:
        inst = self.demo.instance(instance_id)
        if not name.strip():
            raise ValueError("Connection name required")
        role = "allowed_master" if inst.protocol == "iec104" and inst.role is InstanceRole.SERVER else (
            "rtu_slave" if inst.protocol == "modbus_rtu" else "modbus_ied")
        settings = (("ip", host), ("enabled", True)) if role == "allowed_master" else (
            (("unit_id", 1), ("poll_ms", 1000)) if role == "rtu_slave" else
            (("host", host), ("port", 502), ("unit_id", 1)))
        point = CommunicationPoint("peer:" + uuid4().hex[:16], instance_id, name.strip(),
                                   role, tuple(settings))
        with self._lock:
            self._peers.setdefault(instance_id, []).append(point)
            self._point_by_id[point.id] = point
        return point

    def add_station(self, instance_id: str, ca: int) -> DataStation:
        inst = self.demo.instance(instance_id)
        if inst.protocol != "iec104" or inst.role is not InstanceRole.SERVER:
            raise ValueError("ASDU stations only belong to IEC-104 Server")
        if not 1 <= ca <= 65534:
            raise ValueError("Common Address outside 1..65534")
        with self._lock:
            if any(s.address == ca for s in self._stations.get(instance_id, ())):
                raise ValueError("Common Address already configured")
            item = DataStation("station:" + uuid4().hex[:16], instance_id,
                               f"ASDU Station CA {ca}", ca)
            self._stations.setdefault(instance_id, []).append(item)
            self._station_by_id[item.id] = item
            return item

    def update_point(self, instance_id: str, point_id: str, key: str, value: Any) -> None:
        with self._lock:
            points = self._peers.get(instance_id, [])
            for n, point in enumerate(points):
                if point.id == point_id:
                    values = point.params()
                    if key == "name":
                        if not str(value).strip():
                            raise ValueError("Connection name required")
                        points[n] = replace(point, name=str(value).strip())
                        self._point_by_id[point_id] = points[n]
                        return
                    if key not in values:
                        raise ValueError("Unrecognized connection parameter")
                    if key == "enabled":
                        value = str(value).strip().lower() in ("1", "yes", "on", "true")
                    if key in ("port", "unit_id", "poll_ms"):
                        value = int(value)
                        if not 0 <= value <= 65535:
                            raise ValueError("Connection parameter out of range")
                    if key in ("host", "ip") and not str(value).strip():
                        raise ValueError("Network peer address cannot be empty")
                    values[key] = value
                    points[n] = replace(point, settings=tuple(values.items()))
                    self._point_by_id[point_id] = points[n]
                    return
            raise KeyError("Connection ID not found")

    def _seed_range(self, instance_id: str, scope: str):
        ids = self._seeded.get(instance_id, ())
        n = self.demo.count
        if scope not in ids:
            return (0, 0)
        ordinal = ids.index(scope)
        return ((n * ordinal) // len(ids), (n * (ordinal+1)) // len(ids))

    def owner_for(self, instance_id: str, index: int) -> str:
        with self._lock:
            if index >= self.demo.count:
                return self._extra_owners.get((instance_id, index), "")
            seeded = self._seeded.get(instance_id, ())
            if not seeded:
                return ""
            return seeded[min(len(seeded)-1, index * len(seeded) // self.demo.count)]

    def owner_for_signal_id(self, instance_id: str, signal_id: str) -> str:
        prefix = f"{instance_id}:signal:"
        if not signal_id.startswith(prefix):
            raise ValueError("Signal identity does not match protocol instance")
        index = int(signal_id[len(prefix):]) - 1
        if not 0 <= index < self.demo.instance_count(instance_id):
            raise ValueError("Unknown signal ID")
        return self.owner_for(instance_id, index)

    def source(self, instance_id: str, scope_id: str = ""):
        return ScopedSource(self, instance_id, scope_id)

    def append_signal(self, instance_id: str, scope_id: str, name: str) -> str:
        if scope_id and scope_id not in (
            {p.id for p in self.points(instance_id)} |
            {s.id for s in self.stations(instance_id)}
        ):
            raise ValueError("Signal's ConnectionPoint/DataStation is missing")
        with self._lock:
            sid, index = self.demo.append_signal(instance_id, name)
            self._extra_owners[(instance_id, index)] = scope_id
            return sid

    def assign_catalog(self, src_inst: str, src_scope: str, start_row: int,
                       dst_inst: str, station_id: str, ioa_start: int,
                       count: int, ti: int = 13) -> tuple[str, ...]:
        """Mock atomic assign from explicit source view to new IEC104 destination IDs.

        No control commands; downstream field output is never enabled in Studio.
        """
        if src_inst == dst_inst or not 1 <= count <= MAX_ASSIGN:
            raise ValueError("Different source/destination required (max 2000 signals)")
        station = next((s for s in self.stations(dst_inst) if s.id == station_id), None)
        if station is None or self.demo.instance(dst_inst).protocol != "iec104":
            raise ValueError("Destination must be an IEC-104 data station, not master IP")
        if ti not in (1, 3, 9, 11, 13, 35):
            raise ValueError("Auto-assignment is monitoring-only; TI45 command is not a measurement")
        if not 0 <= ioa_start <= 16777215 or ioa_start + count - 1 > 16777215:
            raise ValueError("IOA range exceeds IEC-104 address space")
        source = self.source(src_inst, src_scope)
        if start_row < 0 or start_row + count > source.row_count():
            raise ValueError("Source selection outside configured IED signal catalog")
        with self._lock, self.demo._lock:
            # Check scoped address collisions among existing assigned signal IDs.
            already = set()
            for edge in self.demo._mappings:
                if edge.destination_instance_id != dst_inst:
                    continue
                target_idx = int(edge.destination_signal_id.rsplit(":", 1)[-1]) - 1
                target_owner = self.owner_for(dst_inst, target_idx)
                if target_owner == station_id:
                    row = self.demo.page(dst_inst, target_idx, 1)[0]
                    fieldnames = tuple(f.key for f in self.demo.fields(dst_inst))
                    already.add(int(row[fieldnames.index("ioa")+2]))
            if any(ioa in already for ioa in range(ioa_start, ioa_start+count)):
                raise ValueError("Destination IOA collision; change start address")
            source_indices = [source.index_for_view(src_inst, start_row + i, "", False, "name")
                              for i in range(count)]
            old_added = self.demo._added.get(dst_inst, 0)
            old_mappings = len(self.demo._mappings)
            old_history = len(self.demo._history)
            created = []
            edits = []
            target_indices = []
            try:
                for i, source_idx in enumerate(source_indices):
                    source_row = self.demo.page(src_inst, source_idx, 1)[0]
                    sid, index = self.demo.append_signal(
                        dst_inst, "Mapped / " + str(source_row[2])[:220])
                    created.append(sid)
                    target_indices.append(index)
                    self._extra_owners[(dst_inst, index)] = station_id
                    edits.extend(((sid, "common_address", station.address),
                                  (sid, "ioa", ioa_start + i),
                                  (sid, "ti", str(ti)),
                                  (sid, "family", "binary" if ti in (1, 3) else "analog"),
                                  (sid, "value_type", "bool" if ti in (1, 3) else
                                   "int16" if ti in (9, 11, 35) else "float32")))
                self.demo.edit(dst_inst, edits)
                self.demo.bind_explicit(src_inst, dst_inst,
                                        list(zip(source_indices, target_indices)))
            except Exception:
                self.demo._mappings[old_mappings:] = []
                self.demo._history[old_history:] = []
                self.demo._added[dst_inst] = old_added
                for i in target_indices:
                    self._extra_owners.pop((dst_inst, i), None)
                    for f in self.demo.fields(dst_inst):
                        self.demo._overrides.pop((dst_inst, i, f.key), None)
                raise
            return tuple(created)


    def assign_selected(self, source_instance: str, source_scope: str,
                        selected_ids: tuple[str, ...], target_instance: str,
                        station_id: str, ioa_start: int, ti: int) -> tuple[str, ...]:
        """Atomically bind explicit selected stable signal IDs, never row ranges."""
        ids = tuple(selected_ids)
        if not 1 <= len(ids) <= MAX_ASSIGN or len(ids) != len(set(ids)):
            raise ValueError("Select 1–2000 unique source signal rows")
        if source_instance == target_instance or ti not in (1, 3, 9, 11, 13, 35):
            raise ValueError("Invalid cross-protocol monitoring assignment")
        if not 0 <= ioa_start <= 16777215 or ioa_start + len(ids) > 16777216:
            raise ValueError("IOA range outside IEC-104 address space")
        target = self.demo.instance(target_instance)
        source = self.demo.instance(source_instance)
        if target.protocol != "iec104" or target.role is not InstanceRole.SERVER:
            raise ValueError("Select an IEC-104 Server destination")
        station = next((x for x in self.stations(target_instance) if x.id == station_id), None)
        if station is None:
            raise ValueError("Destination must be a local ASDU station, not a master peer")
        prefix = f"{source_instance}:signal:"
        indexes = []
        for sid in ids:
            try:
                idx = int(sid[len(prefix):]) - 1 if sid.startswith(prefix) else -1
            except ValueError:
                idx = -1
            if idx < 0 or idx >= self.demo.instance_count(source_instance):
                raise ValueError("Unknown source signal ID")
            if sid != self.demo.signal_id(source_instance, idx):
                raise ValueError("Malformed source signal ID")
            if source_scope and self.owner_for(source_instance, idx) != source_scope:
                raise ValueError("Selected signal no longer belongs to selected IED")
            indexes.append(idx)
        with self._lock, self.demo._lock:
            used = {int(self.demo._get(target, i, "ioa"))
                    for (iid, i), owner in self._extra_owners.items()
                    if iid == target_instance and owner == station_id}
            if any(ioa in used for ioa in range(ioa_start, ioa_start + len(indexes))):
                raise ValueError("Duplicate IEC-104 IOA in the selected ASDU station")
            for idx in indexes:
                family = self.demo._get(source, idx, "family")
                if (ti in (1, 3) and family != "binary") or (
                        ti not in (1, 3) and family != "analog"):
                    raise ValueError("Selected source family incompatible with IEC-104 TI")
            old_added = self.demo._added.get(target_instance, 0)
            old_mappings = len(self.demo._mappings)
            old_history, old_redo = list(self.demo._history), list(self.demo._redo)
            created, indexes_to_rollback, changes = [], [], []
            try:
                for offset, source_idx in enumerate(indexes):
                    label = str(self.demo._get(source, source_idx, "name"))
                    sid, index = self.demo.append_signal(target_instance, "Mapped / " + label[:220])
                    created.append(sid)
                    indexes_to_rollback.append(index)
                    self._extra_owners[(target_instance, index)] = station_id
                    for key, value in (("common_address", station.address),
                                       ("ioa", ioa_start + offset), ("ti", str(ti)),
                                       ("family", "binary" if ti in (1, 3) else "analog"),
                                       ("value_type", "bool" if ti in (1, 3) else
                                        "int16" if ti in (9, 11, 35) else "float32")):
                        changes.append((sid, key, value))
                self.demo.edit(target_instance, changes)
                self.demo.bind_explicit(source_instance, target_instance,
                                        list(zip(indexes, indexes_to_rollback)))
            except Exception:
                del self.demo._mappings[old_mappings:]
                self.demo._history[:] = old_history
                self.demo._redo[:] = old_redo
                self.demo._added[target_instance] = old_added
                for index in indexes_to_rollback:
                    self._extra_owners.pop((target_instance, index), None)
                    for field in self.demo.fields(target_instance):
                        self.demo._overrides.pop((target_instance, index, field.key), None)
                raise
            return tuple(created)


def _cumulative_mod_count(length: int, residue: int) -> int:
    full, tail = divmod(length, ADDRESS_MOD)
    return full * (residue + 1) + min(tail, residue + 1)


def nth_numeric_address(start: int, end: int, rank: int) -> int:
    """O(log 30000) index of nth native Modbus address within range [start,end)."""
    if not (0 <= rank < end - start):
        raise IndexError(rank)
    lo, hi = 0, ADDRESS_MOD - 1
    while lo < hi:
        mid = (lo+hi)//2
        count = _cumulative_mod_count(end, mid) - _cumulative_mod_count(start, mid)
        if count > rank:
            hi = mid
        else:
            lo = mid + 1
    residue = lo
    prior = (_cumulative_mod_count(end, residue-1) -
             _cumulative_mod_count(start, residue-1)) if residue else 0
    q = (start - residue + ADDRESS_MOD - 1) // ADDRESS_MOD
    return residue + (q + rank - prior)*ADDRESS_MOD


class ScopedSource:
    """Immutable UI query scope compatible with the existing virtual grid API."""

    def __init__(self, project: EngineeringView, instance_id: str, scope_id: str = ""):
        self.project, self.demo = project, project.demo
        self.instance_id, self.scope_id = instance_id, scope_id

    @property
    def count(self):
        return self.demo.count

    def fields(self, instance_id):
        columns = self.demo.fields(instance_id)
        # Ownership is an ID-backed read-only projection until STORE #3 supports
        # transactional cross-device rebinding and undo/redo.
        return (columns[0], Field("owner", "IED / ASDU Station", "readonly", 172),
                *columns[1:])

    def owner_name(self, index):
        identity = self.project.owner_for(self.instance_id, index)
        with self.project._lock:
            point = self.project._point_by_id.get(identity)
            station = self.project._station_by_id.get(identity)
        if point is not None:
            return point.name
        if station is not None:
            return station.name
        return "Unassigned" if identity == "" else identity

    def signal_id(self, instance_id, index):
        return self.demo.signal_id(instance_id, index)

    def edit(self, instance_id, changes):
        if any(field == "owner" for _, field, _ in changes):
            raise PermissionError("Connection reassignment requires STORE #3 transactional binding")
        return self.demo.edit(instance_id, changes)

    def history(self, redo):
        return self.demo.history(redo)

    def _range(self):
        if not self.scope_id:
            # IEC104 server destination is built by assignment or explicit Add,
            # not pre-filled with 500k fake accessible IOAs.
            if self.demo.instance(self.instance_id).protocol == "iec104":
                return (0, 0)
            return 0, self.demo.count
        return self.project._seed_range(self.instance_id, self.scope_id)

    def _extra(self):
        start = self.demo.count
        count = self.demo.instance_count(self.instance_id)
        with self.project._lock:
            return tuple(i for i in range(start, count)
                         if not self.scope_id or
                         self.project._extra_owners.get((self.instance_id, i)) == self.scope_id)

    def _search_index(self, search):
        return self.demo._index_for_query(search)

    def row_count(self, search: str = ""):
        fixed = self._search_index(search)
        if fixed is not None:
            if fixed < 0:
                return 0
            return int(self.contains(fixed))
        a, b = self._range()
        return b-a + len(self._extra())

    def contains(self, index: int) -> bool:
        a, b = self._range()
        return (a <= index < b or
                index in self._extra())

    def index_for_view(self, instance_id, row, search="", descending=False, sort_key="name"):
        if instance_id != self.instance_id:
            raise ValueError("Scoped source instance changed")
        fixed = self._search_index(search)
        if fixed is not None:
            if row != 0 or not self.contains(fixed):
                raise IndexError("Signal not in selected IED/station")
            return fixed
        a, b = self._range()
        extras = self._extra()
        total = b-a+len(extras)
        if not 0 <= row < total:
            raise IndexError(row)
        n = total - row - 1 if descending else row
        if n >= b-a:
            return extras[n-(b-a)]
        if sort_key == "native_address" and self.demo.instance(instance_id).protocol.startswith("modbus"):
            return nth_numeric_address(a, b, n)
        return a+n

    def page(self, instance_id, offset, limit, search="", descending=False, sort_key="name"):
        if limit < 1 or limit > 256 or offset < 0:
            raise ValueError("Page size must be 1..256")
        total = self.row_count(search)
        indexes = [self.index_for_view(instance_id, n, search, descending, sort_key)
                   for n in range(offset, min(total, offset+limit))]
        if not indexes:
            return []
        # Construct only viewport records; never materialize all 500k signal rows.
        # In the production Store adapter this becomes a paged indexed SQL query.
        out = []
        for index in indexes:
            entry = self.demo.page(instance_id, index, 1)[0]
            display_name = entry[2]
            # In generated dataset labels, use the actual connection owner, not
            # the parent ProtocolInstance technical ID (meter01 is *not* an IED).
            if (self.demo.instance(instance_id).protocol.startswith("modbus") and
                    not self.demo.has_field_override(instance_id, index, "name")):
                display_name = self.owner_name(index).upper().replace(" ", "") + (
                    " / Voltage " + str(index+1).zfill(6))
            out.append((entry[0], entry[1], display_name, self.owner_name(index), *entry[3:]))
        return out
