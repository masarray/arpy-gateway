"""Adapter-facing ports — pure interfaces, no transport or driver implementation.

Individual protocol adapter workstreams own wire encoding, connection lifecycle and
interop tests. This port deliberately does not promise functional Modbus or IEC-104.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import AsyncIterator, Mapping

from arpy_gateway.domain.models import ProtocolInstance, ProtocolSignal, SignalSample


class ProtocolAdapter(ABC):
    @property
    @abstractmethod
    def protocol_id(self) -> str:
        """Unique wire-family capability identifier (e.g. modbus_tcp)."""

    @abstractmethod
    async def start(self, instance: ProtocolInstance, signals: tuple[ProtocolSignal, ...]) -> None:
        """Start one independently supervised instance; never block event loop."""

    @abstractmethod
    async def stop(self) -> None:
        """Idempotent shutdown with bounded timeout and resource cleanup."""

    @abstractmethod
    def samples(self) -> AsyncIterator[SignalSample]:
        """Async iterator; implementation must enforce bounded queue semantics."""

    @abstractmethod
    async def publish(self, signal: ProtocolSignal, sample: SignalSample) -> None:
        """Publish an already-translated sample via the destination adapter.

        Physical commands must be handled by an authenticated, explicit control
        port and cannot be implemented as a generic publish side effect.
        """


class AdapterRegistry(ABC):
    @abstractmethod
    def capabilities(self, protocol_id: str) -> Mapping[str, object]:
        """Adapter-specific roles, settings, signal schema and address semantics."""

    @abstractmethod
    def create(self, protocol_id: str) -> ProtocolAdapter:
        """Return a new instance adapter, not global singleton state."""
