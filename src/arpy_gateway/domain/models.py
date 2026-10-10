"""Immutable contracts for engineering. No Qt, SQLite, sockets, or protocol libraries.

These DTOs are deliberately narrower than actual protocol adapters. Each adapter
validates its own parameters; the pure domain validates cross-adapter invariants.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from types import MappingProxyType
from typing import Any, Mapping, Optional, Tuple, Union


class InstanceRole(str, Enum):
    CLIENT = "client"
    SERVER = "server"


class SignalFamily(str, Enum):
    BINARY = "binary"
    ANALOG = "analog"
    COUNTER = "counter"
    COMMAND = "command"


class SignalDirection(str, Enum):
    INBOUND = "inbound"       # adapter reads/receives this signal
    OUTBOUND = "outbound"     # adapter serves/publishes this signal
    BIDIRECTIONAL = "bidirectional"


class MappingKind(str, Enum):
    TELEMETRY = "telemetry"
    COMMAND = "command"


class ValueType(str, Enum):
    BOOL = "bool"
    INT16 = "int16"
    UINT16 = "uint16"
    INT32 = "int32"
    UINT32 = "uint32"
    INT64 = "int64"
    UINT64 = "uint64"
    FLOAT32 = "float32"
    FLOAT64 = "float64"
    STRING = "string"
    BYTES = "bytes"


class Quality(str, Enum):
    GOOD = "good"
    INVALID = "invalid"
    STALE = "stale"
    COMMUNICATION_LOST = "communication_lost"
    BLOCKED = "blocked"
    TEST = "test"


class SignalOrigin(str, Enum):
    PHYSICAL = "physical"
    SIMULATED = "simulated"
    DERIVED = "derived"


def deep_freeze(value: Any) -> Any:
    """Defensively copy JSON-like settings; avoid mutation through a frozen DTO."""
    if isinstance(value, Mapping):
        return MappingProxyType({str(k): deep_freeze(v) for k, v in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(deep_freeze(v) for v in value)
    if value is None or type(value) in (str, bool, int, float, bytes):
        return value
    raise TypeError("Protocol metadata must contain JSON-like primitives")


def to_mutable(value: Any) -> Any:
    """Detach from DTOs for canonical JSON encoding."""
    if isinstance(value, Mapping):
        return {k: to_mutable(v) for k, v in value.items()}
    if isinstance(value, tuple):
        return [to_mutable(v) for v in value]
    return value


@dataclass(frozen=True)
class ProtocolInstance:
    id: str
    name: str
    protocol: str
    role: InstanceRole
    enabled: bool = False
    settings: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "settings", deep_freeze(self.settings))


@dataclass(frozen=True)
class ProtocolSignal:
    id: str
    instance_id: str
    name: str
    family: SignalFamily
    value_type: ValueType
    direction: SignalDirection
    enabled: bool = True
    unit: str = ""
    parameters: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "parameters", deep_freeze(self.parameters))


@dataclass(frozen=True)
class MappingDefinition:
    id: str
    source_signal_id: str
    destination_signal_id: str
    kind: MappingKind = MappingKind.TELEMETRY
    enabled: bool = True
    transform: Mapping[str, Any] = field(default_factory=dict)
    fan_in_policy: str = "reject"
    # This flag does not authorize physical control on its own. Runtime policy
    # additionally requires authenticated operator permissions and safeguards.
    request_control: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "transform", deep_freeze(self.transform))


@dataclass(frozen=True)
class ProjectSnapshot:
    project_id: str
    instances: Tuple[ProtocolInstance, ...] = ()
    signals: Tuple[ProtocolSignal, ...] = ()
    mappings: Tuple[MappingDefinition, ...] = ()
    schema_version: int = 1

    def __post_init__(self) -> None:
        object.__setattr__(self, "instances", tuple(self.instances))
        object.__setattr__(self, "signals", tuple(self.signals))
        object.__setattr__(self, "mappings", tuple(self.mappings))


SampleValue = Optional[Union[bool, int, float, str, bytes]]


@dataclass(frozen=True)
class SignalSample:
    signal_id: str
    value: SampleValue
    quality: Quality
    origin: SignalOrigin
    observed_at: datetime
    sequence: int
    source_timestamp: Optional[datetime] = None

    def __post_init__(self) -> None:
        if self.sequence < 0:
            raise ValueError("sequence must be nonnegative")
        if self.observed_at.tzinfo is None:
            raise ValueError("observed_at must be timezone-aware UTC or offset")
        if self.source_timestamp is not None and self.source_timestamp.tzinfo is None:
            raise ValueError("source_timestamp must be timezone-aware")
        if self.quality is Quality.GOOD and self.value is None:
            raise ValueError("GOOD sample cannot have a missing value")
