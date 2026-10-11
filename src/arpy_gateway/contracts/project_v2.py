"""Strict additive v2 JSON codec for RFC #13; canonical v1 stays unchanged.

V2 identity is explicit on signals. v1→v2 migration produces ONLY a draft:
missing peer/station owners must be resolved by an engineer before staging.
"""
from __future__ import annotations

import hashlib
import json

from arpy_gateway.contracts.codec import decode_project, encode_project
from arpy_gateway.domain.models import to_mutable
from arpy_gateway.domain.v2 import (
    CommunicationPoint, DataStation, EngineeringProjectV2, SignalBinding,
)

_TOP = frozenset(("schema_version", "project_id", "instances",
                  "communication_points", "data_stations", "signals", "mappings"))
_POINT = frozenset(("id", "instance_id", "name", "kind", "enabled", "settings"))
_STATION = frozenset(("id", "instance_id", "name", "enabled", "settings"))
_BINDING = frozenset(("connection_point_id", "data_station_id"))
_SIGNAL = frozenset(("id", "instance_id", "name", "family", "value_type",
                      "direction", "enabled", "unit", "parameters"))


def encode_project_v2(project: EngineeringProjectV2) -> dict:
    raw = encode_project(project.base)
    raw["schema_version"] = 2
    raw["communication_points"] = [
        {"id": x.id, "instance_id": x.instance_id, "name": x.name,
         "kind": x.kind, "enabled": x.enabled, "settings": to_mutable(x.settings)}
        for x in project.communication_points
    ]
    raw["data_stations"] = [
        {"id": x.id, "instance_id": x.instance_id, "name": x.name,
         "enabled": x.enabled, "settings": to_mutable(x.settings)}
        for x in project.data_stations
    ]
    by_id = {b.signal_id: b for b in project.signal_bindings}
    if len(by_id) != len(project.signal_bindings):
        raise ValueError("Duplicate v2 signal binding in canonical encoder")
    for signal in raw["signals"]:
        binding = by_id.get(signal["id"])
        signal["connection_point_id"] = (
            binding.communication_point_id if binding else None)
        signal["data_station_id"] = (
            binding.data_station_id if binding else None)
    return raw


def decode_project_v2(raw: dict) -> EngineeringProjectV2:
    if type(raw) is not dict or set(raw) != _TOP or type(raw["schema_version"]) is not int or raw["schema_version"] != 2:
        raise ValueError("Expected exact engineering project schema v2")
    for name, keys in (
        ("communication_points", _POINT), ("data_stations", _STATION),
        ("signals", _SIGNAL | _BINDING),
    ):
        if type(raw[name]) is not list or any(
                type(item) is not dict or set(item) != keys for item in raw[name]):
            raise ValueError("Invalid v2 array/unknown fields: " + name)
    base_dict = {
        "schema_version": 1, "project_id": raw["project_id"],
        "instances": raw["instances"],
        "signals": [
            {key: value for key, value in item.items() if key in _SIGNAL}
            for item in raw["signals"]
        ],
        "mappings": raw["mappings"],
    }
    base = decode_project(base_dict)
    points = tuple(CommunicationPoint(**p) for p in raw["communication_points"])
    stations = tuple(DataStation(**p) for p in raw["data_stations"])
    bindings = []
    for item in raw["signals"]:
        conn, station = item["connection_point_id"], item["data_station_id"]
        if (conn is not None and (type(conn) is not str or not conn)
                or station is not None and (type(station) is not str or not station)):
            raise ValueError("Invalid explicit v2 signal binding ID")
        if conn is not None or station is not None:
            bindings.append(SignalBinding(item["id"], conn, station))
    return EngineeringProjectV2(
        base, points, stations, tuple(bindings)
    )


def canonical_project_digest_v2(project: EngineeringProjectV2) -> str:
    """Integrity digest only — never treat it as a Deploy authorization."""
    payload = json.dumps(encode_project_v2(project), ensure_ascii=False,
                         sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
