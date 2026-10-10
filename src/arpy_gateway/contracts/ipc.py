"""Transport-neutral IPC message envelope; not a running IPC server."""
from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Mapping
from uuid import UUID

from arpy_gateway.domain.models import deep_freeze, to_mutable

API_VERSION = "1.0"


class MessageKind(str, Enum):
    REQUEST = "request"
    RESPONSE = "response"
    EVENT = "event"
    ERROR = "error"


@dataclass(frozen=True)
class Envelope:
    api_version: str
    message_id: str
    kind: MessageKind
    sent_at: datetime
    payload: Mapping[str, Any] = field(default_factory=dict)
    correlation_id: str = ""

    def __post_init__(self) -> None:
        if self.api_version.split(".")[0] != API_VERSION.split(".")[0]:
            raise ValueError("Incompatible IPC major version")
        if not self.message_id:
            raise ValueError("Message ID required")
        if self.sent_at.tzinfo is None:
            raise ValueError("UTC/offset-aware sent_at required")
        object.__setattr__(self, "payload", deep_freeze(self.payload))

    def to_json_object(self) -> dict:
        return {"api_version": self.api_version, "message_id": self.message_id,
                "kind": self.kind.value, "sent_at": self.sent_at.isoformat(),
                "correlation_id": self.correlation_id, "payload": to_mutable(self.payload)}
