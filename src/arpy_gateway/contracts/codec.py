"""Deterministic, safe DTO to/from canonical JSON for engineering snapshots."""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict
from typing import Any, Dict

from arpy_gateway.domain.models import (
    InstanceRole, MappingDefinition, MappingKind, ProjectSnapshot, ProtocolInstance,
    ProtocolSignal, SignalDirection, SignalFamily, ValueType, to_mutable
)


def encode_project(project: ProjectSnapshot) -> Dict[str, Any]:
    return {
        "schema_version": project.schema_version,
        "project_id": project.project_id,
        "instances": [
            {"id": x.id, "name": x.name, "protocol": x.protocol, "role": x.role.value,
             "enabled": x.enabled, "settings": to_mutable(x.settings)}
            for x in project.instances
        ],
        "signals": [
            {"id": x.id, "instance_id": x.instance_id, "name": x.name,
             "family": x.family.value, "value_type": x.value_type.value,
             "direction": x.direction.value, "enabled": x.enabled, "unit": x.unit,
             "parameters": to_mutable(x.parameters)}
            for x in project.signals
        ],
        "mappings": [
            {"id": x.id, "source_signal_id": x.source_signal_id,
             "destination_signal_id": x.destination_signal_id, "kind": x.kind.value,
             "enabled": x.enabled, "transform": to_mutable(x.transform),
             "fan_in_policy": x.fan_in_policy, "request_control": x.request_control}
            for x in project.mappings
        ],
    }


def decode_project(raw: Dict[str, Any]) -> ProjectSnapshot:
    """Decode only recognized version 1 fields; unknown fields fail closed."""
    if raw.get("schema_version") != 1:
        raise ValueError("Unsupported engineering project schema version")
    if set(raw) != {"schema_version", "project_id", "instances", "signals", "mappings"}:
        raise ValueError("Unknown/missing top-level fields in project")
    required = {
        "instances": {"id", "name", "protocol", "role", "enabled", "settings"},
        "signals": {"id", "instance_id", "name", "family", "value_type",
                    "direction", "enabled", "unit", "parameters"},
        "mappings": {"id", "source_signal_id", "destination_signal_id",
                     "kind", "enabled", "transform", "fan_in_policy", "request_control"},
    }
    for section, keys in required.items():
        collection = raw[section]
        if type(collection) is not list:
            raise ValueError(section + " must be an array")
        for index, item in enumerate(collection):
            if type(item) is not dict or set(item) != keys:
                raise ValueError("Unexpected fields in %s[%d]" % (section, index))
    return ProjectSnapshot(
        project_id=raw["project_id"],
        schema_version=1,
        instances=tuple(
            ProtocolInstance(id=x["id"], name=x["name"], protocol=x["protocol"],
                             role=InstanceRole(x["role"]), enabled=x["enabled"],
                             settings=x["settings"]) for x in raw["instances"]
        ),
        signals=tuple(
            ProtocolSignal(id=x["id"], instance_id=x["instance_id"], name=x["name"],
                           family=SignalFamily(x["family"]), value_type=ValueType(x["value_type"]),
                           direction=SignalDirection(x["direction"]), enabled=x["enabled"],
                           unit=x["unit"], parameters=x["parameters"]) for x in raw["signals"]
        ),
        mappings=tuple(
            MappingDefinition(id=x["id"], source_signal_id=x["source_signal_id"],
                              destination_signal_id=x["destination_signal_id"],
                              kind=MappingKind(x["kind"]), enabled=x["enabled"],
                              transform=x["transform"], fan_in_policy=x["fan_in_policy"],
                              request_control=x["request_control"]) for x in raw["mappings"]
        ),
    )


def canonical_project_digest(project: ProjectSnapshot) -> str:
    """Stable digest is an integrity identifier, NOT an authorization signature."""
    payload = json.dumps(encode_project(project), sort_keys=True,
                         separators=(",", ":"), ensure_ascii=False, allow_nan=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
