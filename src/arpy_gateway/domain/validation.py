"""Deterministic domain validation with bounded O(V+E) graph pass.

Adapter address scopes are handled separately; this module intentionally does
not misinterpret Modbus 4xxxx display labels or IEC-104 TI/IOA formats.
"""
from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass
from typing import Dict, List, Set, Tuple

from .models import (MappingKind, ProjectSnapshot, ProtocolSignal, SignalDirection,
                     SignalFamily, ValueType)


@dataclass(frozen=True)
class ValidationIssue:
    code: str
    entity_type: str
    entity_id: str
    message: str
    severity: str = "error"


_NUMERIC = {ValueType.INT16, ValueType.UINT16, ValueType.INT32, ValueType.UINT32,
            ValueType.INT64, ValueType.UINT64, ValueType.FLOAT32, ValueType.FLOAT64}


def _compatible(src: ProtocolSignal, dst: ProtocolSignal, transform: object) -> bool:
    if src.value_type == dst.value_type:
        return True
    if src.value_type in _NUMERIC and dst.value_type in _NUMERIC:
        return True  # narrowing/overflow checks are adapter/transform responsibilities
    return bool(getattr(transform, "get", lambda *_: False)("explicit_cast"))


def validate_project(project: ProjectSnapshot) -> Tuple[ValidationIssue, ...]:
    """Validate IDs, missing refs, direction, fan-in, control and data cycles.

    Fatal issues block staging/deployment but MUST NOT prevent saving a Draft.
    """
    problems: List[ValidationIssue] = []

    def err(code: str, entity: str, identity: str, explanation: str) -> None:
        problems.append(ValidationIssue(code, entity, identity, explanation))

    if project.schema_version != 1:
        err("SCHEMA_VERSION", "project", project.project_id, "Unsupported schema version")

    instances: Dict[str, object] = {}
    for inst in project.instances:
        if not inst.id or inst.id in instances:
            err("INSTANCE_ID", "instance", inst.id, "Duplicate or empty instance ID")
        else:
            instances[inst.id] = inst
        if not inst.name.strip() or not inst.protocol.strip():
            err("INSTANCE_FIELDS", "instance", inst.id, "Name and protocol required")

    signals: Dict[str, ProtocolSignal] = {}
    for sig in project.signals:
        if not sig.id or sig.id in signals:
            err("SIGNAL_ID", "signal", sig.id, "Duplicate or empty signal ID")
        else:
            signals[sig.id] = sig
        if sig.instance_id not in instances:
            err("SIGNAL_INSTANCE", "signal", sig.id, "Owner protocol instance does not exist")
        if not sig.name.strip():
            err("SIGNAL_NAME", "signal", sig.id, "Signal name required")
        if sig.family is SignalFamily.BINARY and sig.value_type is not ValueType.BOOL:
            err("BINARY_TYPE", "signal", sig.id, "Binary signals require bool value type")

    # O(E), no repeated scans across entire point collection.
    mapping_ids: Set[str] = set()
    route_ids: Set[Tuple[str, str, MappingKind]] = set()
    writers: Dict[str, List[object]] = defaultdict(list)
    adj: Dict[str, List[str]] = defaultdict(list)
    indegree: Dict[str, int] = defaultdict(int)

    for edge in project.mappings:
        if not edge.id or edge.id in mapping_ids:
            err("MAPPING_ID", "mapping", edge.id, "Duplicate or empty mapping ID")
        mapping_ids.add(edge.id)
        src = signals.get(edge.source_signal_id)
        dst = signals.get(edge.destination_signal_id)
        if src is None or dst is None:
            err("MAPPING_REFERENCE", "mapping", edge.id, "Source and destination signals must exist")
            continue
        if not edge.enabled:
            continue
        if not (src.enabled and dst.enabled):
            err("DISABLED_SIGNAL", "mapping", edge.id, "Active mapping references a disabled signal")
        route = (src.id, dst.id, edge.kind)
        if route in route_ids:
            err("DUPLICATE_EDGE", "mapping", edge.id, "Duplicate directed mapping edge")
        route_ids.add(route)
        if src.id == dst.id:
            err("SELF_MAPPING", "mapping", edge.id, "Signal cannot map to itself")
        if src.direction not in (SignalDirection.INBOUND, SignalDirection.BIDIRECTIONAL):
            err("SOURCE_DIRECTION", "mapping", edge.id, "Source point must be readable/inbound")
        if dst.direction not in (SignalDirection.OUTBOUND, SignalDirection.BIDIRECTIONAL):
            err("DESTINATION_DIRECTION", "mapping", edge.id, "Destination must publish/outbound")
        if edge.kind is MappingKind.COMMAND:
            if dst.family is not SignalFamily.COMMAND:
                err("COMMAND_TARGET", "mapping", edge.id, "Command routes require command destination")
            if not edge.request_control:
                err("COMMAND_POLICY", "mapping", edge.id, "Command requires explicit control intent")
        elif src.family is SignalFamily.COMMAND or dst.family is SignalFamily.COMMAND:
            err("COMMAND_AS_TELEMETRY", "mapping", edge.id, "Command points need explicit command mapping")
        if not _compatible(src, dst, edge.transform):
            err("VALUE_TYPE", "mapping", edge.id, "Explicit conversion required")
        writers[dst.id].append(edge)
        if edge.kind is MappingKind.TELEMETRY:
            adj[src.id].append(dst.id)
            indegree[dst.id] += 1
            indegree.setdefault(src.id, 0)

    for dest, edges in writers.items():
        if len(edges) > 1 and any(edge.fan_in_policy == "reject" for edge in edges):
            err("FAN_IN", "signal", dest, "Multiple writers require explicit arbitration policy")
        if len(edges) > 1 and len({edge.fan_in_policy for edge in edges}) != 1:
            err("FAN_IN_POLICY", "signal", dest, "Every writer must use the same arbitration policy")

    # Kahn traversal is iterative: robust with hundreds of thousands of points.
    ready = deque(node for node, degree in indegree.items() if degree == 0)
    visited = 0
    while ready:
        node = ready.popleft()
        visited += 1
        for target in adj[node]:
            indegree[target] -= 1
            if indegree[target] == 0:
                ready.append(target)
    if visited != len(indegree):
        err("MAPPING_CYCLE", "project", project.project_id, "Telemetry mapping graph has a cycle")

    problems.extend(_validate_server_address_scopes(project, instances))
    return tuple(problems)


def _validate_server_address_scopes(project: ProjectSnapshot, instances: dict) -> list[ValidationIssue]:
    """Conservative server address collision check, O(S log S) for interval ranges.

    This is NOT a replacement for adapter-owned protocol validation. Input/client
    polling points may legitimately read overlapping registers. All server
    output points must have unambiguous addressing unless future adapter policy
    explicitly supports aliases. IEC-104 monitors/controls use separate direction
    namespaces here; each adapter will validate exact TI and packet semantics.
    """
    issues: list[ValidationIssue] = []
    seen_iec: dict[tuple, str] = {}
    modbus_ranges: dict[tuple, list[tuple[int, int, str]]] = defaultdict(list)

    for sig in project.signals:
        inst = instances.get(sig.instance_id)
        if not sig.enabled or inst is None or not inst.enabled:
            continue
        if inst.role.value != "server":
            continue
        p = sig.parameters

        if inst.protocol == "iec104":
            try:
                ca = int(p.get("common_address", inst.settings.get("common_address")))
                ioa = int(p["ioa"])
                if not (1 <= ca <= 65534 and 0 <= ioa <= 16777215):
                    raise ValueError("IEC-104 CA/IOA out of protocol range")
            except (KeyError, TypeError, ValueError, OverflowError):
                issues.append(ValidationIssue("ADDRESS_INVALID", "signal", sig.id,
                                              "IEC-104 server requires valid Common Address and IOA"))
                continue
            scope = (inst.id, ca, ioa, sig.direction.value)
            if scope in seen_iec:
                issues.append(ValidationIssue("ADDRESS_COLLISION", "signal", sig.id,
                                              "IEC-104 CA/IOA direction already owned by " + seen_iec[scope]))
            else:
                seen_iec[scope] = sig.id

        elif inst.protocol in ("modbus_tcp", "modbus_rtu"):
            if sig.direction is SignalDirection.INBOUND:
                continue  # incoming write target and read-only input need adapter-specific policy
            table = p.get("register_table")
            if table not in ("coil", "discrete", "holding", "input"):
                issues.append(ValidationIssue("ADDRESS_INVALID", "signal", sig.id,
                                              "Modbus register table must be coil/discrete/holding/input"))
                continue
            try:
                addr = int(p["native_address"])
                unit = int(p.get("unit_id", inst.settings.get("unit_id", 1)))
                if "register_count" in p:
                    count = int(p["register_count"])
                else:
                    count = {ValueType.INT32: 2, ValueType.UINT32: 2,
                             ValueType.FLOAT32: 2, ValueType.INT64: 4,
                             ValueType.UINT64: 4, ValueType.FLOAT64: 4}.get(sig.value_type, 1)
                if table in ("coil", "discrete") and count != 1:
                    raise ValueError("Bit table points must have width one")
                if not (0 <= addr <= 65535 and 1 <= count <= 125 and addr + count <= 65536
                        and 0 <= unit <= 255):
                    raise ValueError("Modbus address/width/Unit ID out of range")
            except (KeyError, TypeError, ValueError, OverflowError):
                issues.append(ValidationIssue("ADDRESS_INVALID", "signal", sig.id,
                                              "Modbus server requires valid PDU native address, count and Unit ID"))
                continue
            key = (inst.id, unit, table)
            modbus_ranges[key].append((addr, addr + count, sig.id))

    for ranges in modbus_ranges.values():
        ranges.sort(key=lambda r: (r[0], r[1], r[2]))
        prev_end = -1
        prev_id = ""
        for start, end, sid in ranges:
            if start < prev_end:
                issues.append(ValidationIssue("ADDRESS_COLLISION", "signal", sid,
                                              "Modbus server register range overlaps " + prev_id))
            if end > prev_end:
                prev_end = end
                prev_id = sid
    return issues
