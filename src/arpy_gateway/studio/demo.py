"""Temporary, bounded, Qt-free engineering DEMO provider for GUI issue #4.

Not the canonical STORE (#3), not deployed config, not a protocol adapter.
500k rows are *generated on demand*: IDs are stable, no per-row objects cached.
The provider is intentionally replaceable by an async engineering repository.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from threading import RLock
from typing import Any, NamedTuple
import re

from arpy_gateway.domain.models import InstanceRole, ProtocolInstance


@dataclass(frozen=True)
class Field:
    key: str
    title: str
    kind: str = "text"
    width: int = 105
    options: tuple[str, ...] = ()


SHARED = (
    Field("name", "Signal", width=225),
    Field("family", "Family", "choice", 100, ("analog", "binary", "counter", "command")),
    Field("value_type", "Value Type", "choice", 100,
          ("bool", "int16", "uint16", "int32", "uint32", "float32", "float64")),
    Field("direction", "Direction", "choice", 105, ("inbound", "outbound", "bidirectional")),
    Field("unit", "Unit", width=70),
)
MODBUS = SHARED + (
    Field("register_table", "Table", "choice", 105, ("holding", "input", "coil", "discrete")),
    Field("unit_id", "Unit ID", "int", 76),
    Field("native_address", "PDU Address (0-based)", "int", 150),
    Field("register_count", "Registers", "int", 85),
    Field("byte_order", "Byte Order", "choice", 100, ("big", "little")),
    Field("word_order", "Word Order", "choice", 100, ("big", "little")),
    Field("scale", "Scale", "float", 85),
)
IEC104 = SHARED + (
    Field("common_address", "Common Address", "int", 115),
    Field("ioa", "IOA", "int", 115),
    Field("ti", "IEC-104 TI", "choice", 95, ("1", "3", "9", "11", "13", "35", "45")),
    Field("transfer", "Transfer", "choice", 125, ("spontaneous", "interrogation", "both")),
    Field("quality_policy", "Quality", "choice", 140, ("propagate", "invalid_on_loss")),
)
MAX_DEMO_ROWS = 500_000
MAX_BATCH_CELLS = 50_000
MAX_HISTORY = 16


class MappingRow(NamedTuple):
    id: str
    source_instance_id: str
    source_signal_id: str
    destination_instance_id: str
    destination_signal_id: str


class DemoProvider:
    """Lock protects concurrent read workers vs serialized write worker."""

    def __init__(self):
        self._lock = RLock()
        self._rows = 1_000
        self._overrides: dict[tuple[str, int, str], Any] = {}
        self._history: list[list[tuple[tuple[str, int, str], Any, Any]]] = []
        self._redo: list[list[tuple[tuple[str, int, str], Any, Any]]] = []
        self._mappings: list[MappingRow] = []
        self._instances = (
            ProtocolInstance("meter01", "Meter 01 — Client", "modbus_tcp", InstanceRole.CLIENT,
                             True, {"host": "192.168.1.10", "port": 502, "unit_id": 1, "poll_ms": 1000}),
            ProtocolInstance("scada104", "SCADA — IEC-104 Server", "iec104", InstanceRole.SERVER,
                             True, {"bind": "127.0.0.1", "port": 2404, "common_address": 1}),
            ProtocolInstance("mirror", "Mirror — Modbus Server", "modbus_tcp", InstanceRole.SERVER,
                             False, {"bind": "127.0.0.1", "port": 1502, "unit_id": 1}),
        )

    @property
    def count(self) -> int:
        return self._rows

    def instances(self) -> tuple[ProtocolInstance, ...]:
        with self._lock:
            return self._instances

    def instance(self, instance_id: str) -> ProtocolInstance:
        with self._lock:
            return next(i for i in self._instances if i.id == instance_id)

    def set_enabled(self, instance_id: str, enabled: bool) -> None:
        with self._lock:
            self._instances = tuple(replace(i, enabled=enabled) if i.id == instance_id else i
                                    for i in self._instances)

    def set_setting(self, instance_id: str, key: str, value: Any) -> None:
        with self._lock:
            existing = self.instance(instance_id)
            if key not in existing.settings:
                raise ValueError("Unknown parameter")
            updated = dict(existing.settings)
            if key in ("port", "unit_id", "poll_ms", "common_address"):
                value = int(value)
                if not (0 <= value <= 65535):
                    raise ValueError("Numeric parameter outside configured bounds")
                if key == "port" and value == 0:
                    raise ValueError("Port 0 is not permitted")
            updated[key] = value
            self._instances = tuple(replace(i, settings=updated) if i.id == instance_id else i
                                    for i in self._instances)

    def fields(self, instance_id: str) -> tuple[Field, ...]:
        return IEC104 if self.instance(instance_id).protocol == "iec104" else MODBUS

    def resize_demo(self, rows: int) -> None:
        if rows not in (1_000, 100_000, 500_000):
            raise ValueError("Unsupported demo size")
        with self._lock:
            self._rows = rows
            self._overrides.clear()
            self._history.clear()
            self._redo.clear()
            self._mappings.clear()

    @staticmethod
    def signal_id(instance_id: str, index: int) -> str:
        return f"{instance_id}:signal:{index + 1:07d}"

    def _default(self, inst: ProtocolInstance, index: int, key: str) -> Any:
        iec = inst.protocol == "iec104"
        if key == "name":
            return f"{inst.id.upper()} / Voltage {index + 1:06d}"
        if key == "family":
            return "analog"
        if key == "value_type":
            return "float32"
        if key == "direction":
            return "outbound" if inst.role is InstanceRole.SERVER else "inbound"
        if key == "unit":
            return "V"
        if key == "register_table":
            return "holding"
        if key == "unit_id":
            return 1 + index // 30_000
        if key == "native_address":
            return 2 * (index % 30_000)
        if key == "register_count":
            return 2
        if key in ("byte_order", "word_order"):
            return "big"
        if key == "scale":
            return 1.0
        if key == "common_address":
            return 1
        if key == "ioa":
            return 1001 + index
        if key == "ti":
            return "13"
        if key == "transfer":
            return "both"
        if key == "quality_policy":
            return "propagate"
        raise KeyError(key)

    def _get(self, inst: ProtocolInstance, index: int, key: str) -> Any:
        return self._overrides.get((inst.id, index, key), self._default(inst, index, key))

    def _index_for_query(self, search: str) -> int | None:
        text = search.strip().lower()
        if not text:
            return None
        if text in ("voltage", "analog", "all"):
            return None
        # Exact signal sequence lookup uses direct index, not a 500k-row scan.
        match = re.fullmatch(r"(?:signal:|#|voltage\s*)?0*(\d{1,7})", text)
        if match:
            return int(match.group(1)) - 1
        return -1

    def row_count(self, search: str = "") -> int:
        with self._lock:
            index = self._index_for_query(search)
            return self._rows if index is None else int(0 <= index < self._rows)

    def index_for_view(self, instance_id: str, row: int, search: str = "",
                       descending: bool = False, sort_key: str = "name") -> int:
        """O(1) identity lookup for monotonic demo indexes; no 500k sort array."""
        fixed = self._index_for_query(search)
        if fixed is not None:
            if row != 0 or not (0 <= fixed < self._rows):
                raise IndexError(row)
            return fixed
        if not 0 <= row < self._rows:
            raise IndexError(row)
        ordinal = self._rows - row - 1 if descending else row
        if sort_key == "native_address" and self.instance(instance_id).protocol != "iec104":
            full, tail = divmod(self._rows, 30_000)
            if not full:
                return ordinal
            split = (full + 1) * tail
            if ordinal < split:
                addr_index, unit_index = divmod(ordinal, full + 1)
            else:
                addr_offset, unit_index = divmod(ordinal - split, full)
                addr_index = tail + addr_offset
            return 30_000 * unit_index + addr_index
        return ordinal

    def page(self, instance_id: str, offset: int, limit: int,
             search: str = "", descending: bool = False,
             sort_key: str = "name") -> list[tuple[Any, ...]]:
        if limit < 1 or limit > 256 or offset < 0:
            raise ValueError("Page outside bounded size")
        with self._lock:
            inst = self.instance(instance_id)
            fields = self.fields(instance_id)
            fixed = self._index_for_query(search)
            total = self._rows if fixed is None else int(0 <= fixed < self._rows)
            out = []
            for visible_index in range(offset, min(offset + limit, total)):
                index = self.index_for_view(instance_id, visible_index, search, descending, sort_key)
                out.append((self.signal_id(instance_id, index), index,
                            *(self._get(inst, index, col.key) for col in fields)))
            return out

    @staticmethod
    def _cast(col: Field, raw: Any) -> Any:
        if col.kind == "int":
            if str(raw).strip() == "":
                raise ValueError(f"{col.title}: blank is not a valid integer")
            value = int(raw)
            if value < 0 or value > 16_777_215:
                raise ValueError(f"{col.title}: numeric value out of range")
            return value
        if col.kind == "float":
            value = float(raw)
            if not (-1e12 <= value <= 1e12):
                raise ValueError("Scale outside finite numeric range")
            return value
        value = str(raw).strip()
        if col.options and value not in col.options:
            raise ValueError(f"{col.title}: unsupported option {value}")
        if len(value) > 256:
            raise ValueError("Maximum cell text length 256")
        if col.key == "name" and not value:
            raise ValueError("Signal name is required")
        return value

    def edit(self, instance_id: str, changes: list[tuple[str, str, Any]]) -> int:
        """Atomic, identity-based batch edit. (signal_id, field_key, new_value)."""
        if not changes or len(changes) > MAX_BATCH_CELLS:
            raise ValueError("Bulk edit exceeds limit (50,000 cells)")
        with self._lock:
            inst = self.instance(instance_id)
            fields = {c.key: c for c in self.fields(instance_id)}
            updates: dict[tuple[str, int, str], Any] = {}
            for sid, field, raw in changes:
                prefix = f"{instance_id}:signal:"
                if field not in fields or not sid.startswith(prefix):
                    raise ValueError("Invalid protocol field or signal identity")
                index = int(sid[len(prefix):]) - 1
                if index < 0 or index >= self._rows or self.signal_id(instance_id, index) != sid:
                    raise ValueError("Signal identity outside project")
                updates[(instance_id, index, field)] = self._cast(fields[field], raw)
            history = []
            for key, new in updates.items():
                old = self._get(inst, key[1], key[2])
                if old != new:
                    history.append((key, old, new))
            for key, old, new in history:
                if new == self._default(inst, key[1], key[2]):
                    self._overrides.pop(key, None)
                else:
                    self._overrides[key] = new
            if history:
                self._history.append(history)
                self._history = self._history[-MAX_HISTORY:]
                self._redo.clear()
            return len(history)

    def history(self, redo: bool) -> int:
        with self._lock:
            source = self._redo if redo else self._history
            if not source:
                return 0
            batch = source.pop()
            for key, old, new in batch:
                instance = self.instance(key[0])
                target = new if redo else old
                if target == self._default(instance, key[1], key[2]):
                    self._overrides.pop(key, None)
                else:
                    self._overrides[key] = target
            target_stack = self._history if redo else self._redo
            target_stack.append(batch)
            return len(batch)

    def map_range(self, source: str, destination: str, start: int, length: int) -> int:
        if source == destination or not (0 <= start < self._rows) or not (
            1 <= length <= 10_000 and start + length <= self._rows):
            raise ValueError("Invalid cross-protocol range (max 10,000 points)")
        with self._lock:
            self.instance(source)
            self.instance(destination)
            existing = {(m.source_signal_id, m.destination_signal_id) for m in self._mappings}
            pending = []
            for index in range(start, start + length):
                sid = self.signal_id(source, index)
                did = self.signal_id(destination, index)
                if (sid, did) not in existing:
                    pending.append(MappingRow(f"mapping:{source}:{destination}:{index}",
                                              source, sid, destination, did))
            if len(self._mappings) + len(pending) > 50_000:
                raise ValueError("Demo mapping limit reached")
            self._mappings.extend(pending)
            return len(pending)

    def mappings(self) -> tuple[MappingRow, ...]:
        with self._lock:
            return tuple(self._mappings)

    def diagnostics(self) -> str:
        with self._lock:
            return ("ARPY Studio | OFFLINE ENGINEERING DEMO\n"
                    "Runtime connected: NO\nActual RX/TX: unavailable\n"
                    "Signal values: unavailable, not zero or Good\n"
                    f"Virtual signals per instance: {self._rows}\n"
                    f"Session-only mapping count: {len(self._mappings)}\n"
                    "No field devices accessed; simulation is UI preview only.\n")
