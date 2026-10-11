"""RFC #13 candidate, additive v2 engineering identity model.

This is an ARCH-lane proposal, not an approved v1 replacement.
Explicit peer/station bindings are separate from stable signal IDs; no GUI,
SQLite, sockets or protocol adapter imports.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

from .models import ProjectSnapshot, deep_freeze


@dataclass(frozen=True)
class CommunicationPoint:
    id: str
    instance_id: str
    name: str
    kind: str                 # remote_device | authorized_master
    enabled: bool = True
    settings: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        object.__setattr__(self, "settings", deep_freeze(self.settings))


@dataclass(frozen=True)
class DataStation:
    id: str
    instance_id: str
    name: str
    enabled: bool = True
    settings: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        object.__setattr__(self, "settings", deep_freeze(self.settings))


@dataclass(frozen=True)
class SignalBinding:
    signal_id: str
    communication_point_id: str | None = None
    data_station_id: str | None = None


@dataclass(frozen=True)
class EngineeringProjectV2:
    """v1 DTOs + explicit scoped identities; keeps all preexisting IDs.

    The v1 ProjectSnapshot remains intact and is never silently migrated on
    decode. A v1 project may be proposed as a v2 DRAFT with missing bindings;
    only explicit engineer selection makes v2 eligible for staging.
    """
    base: ProjectSnapshot
    communication_points: tuple[CommunicationPoint, ...] = ()
    data_stations: tuple[DataStation, ...] = ()
    signal_bindings: tuple[SignalBinding, ...] = ()

    def __post_init__(self):
        if self.base.schema_version != 1:
            raise ValueError("V2 wrapper requires canonical v1 DTO foundation")
        for field_name in ("communication_points", "data_stations", "signal_bindings"):
            object.__setattr__(self, field_name, tuple(getattr(self, field_name)))


def propose_migration_v1_draft(base: ProjectSnapshot) -> EngineeringProjectV2:
    """Non-destructive migration proposal: NEVER guess missing remote IEDs.

    Existing v1 instance/signal/mapping IDs and metadata are preserved
    exactly. No network connection or runtime Deploy is triggered.
    """
    return EngineeringProjectV2(base)
