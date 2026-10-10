"""Monitoring projection seam. No Studio networking or runtime direct imports.

IPC lane #8 will inject a compatible provider. Disconnected provider cannot
pretend to deliver live measurements, telemetry quality or simulation output.
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class RuntimeInstanceView:
    instance_id: str
    connected: bool
    connection_text: str
    last_value: str
    quality: str
    rx_tx: str
    timestamp: str


class RuntimeViewProvider(Protocol):
    @property
    def connected(self) -> bool: ...
    @property
    def simulation_available(self) -> bool: ...
    def instances(self) -> tuple[RuntimeInstanceView, ...]: ...
    def diagnostics(self) -> str: ...


class DisconnectedRuntime:
    """Honest no-runtime mock: ALL samples unknown, never invented Good or zero."""
    def __init__(self, demo):
        self._demo = demo

    @property
    def connected(self) -> bool:
        return False

    @property
    def simulation_available(self) -> bool:
        return False

    def instances(self) -> tuple[RuntimeInstanceView, ...]:
        return tuple(RuntimeInstanceView(i.id, False, "Not connected",
                                         "—", "Unavailable", "— / —", "—")
                     for i in self._demo.instances())

    def diagnostics(self) -> str:
        return self._demo.diagnostics()
