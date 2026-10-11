"""Explicit Modbus TCP/RTU protocol adapter integration for runtime issue #8.

Adapter source stays owned by issue #6. This IPC-lane bridge imports its public
exports lazily and adapts its family-level, pre-start identifier to the strict
ProtocolAdapter port without modifying third-party or parallel-branch code.
No Qt, GUI, project database, runtime pip installation or endpoint discovery.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Sequence

from arpy_gateway.domain.models import (
    InstanceRole, ProtocolInstance, ProtocolSignal, SignalSample,
)
from arpy_gateway.runtime.ports import ProtocolAdapter

_MODBUS_PROTOCOLS = frozenset(("modbus_tcp", "modbus_rtu"))


class BoundModbusAdapter(ProtocolAdapter):
    """Owns one ModbusClientAdapter and an exact validated transport identity."""

    def __init__(self, instance: ProtocolInstance, devices: Sequence[Any],
                 *, inner=None):
        if instance.protocol not in _MODBUS_PROTOCOLS or instance.role is not InstanceRole.CLIENT:
            raise ValueError("Only Modbus TCP/RTU Master instances are supported")
        # The adapter can remain absent in contract-only CI; no side effects
        # or imports when the service is running a fixture source.
        if inner is None:
            try:
                from arpy_gateway.adapters.modbus import ModbusClientAdapter
            except ImportError as exc:
                raise RuntimeError(
                    "Bundled ARPY Modbus adapter is unavailable; repair installation"
                ) from exc
            inner = ModbusClientAdapter(tuple(devices))
        self._instance = instance
        self._inner = inner

    @property
    def protocol_id(self) -> str:
        return self._instance.protocol

    async def start(self, instance: ProtocolInstance,
                    signals: tuple[ProtocolSignal, ...]) -> None:
        if instance != self._instance:
            raise ValueError("Runtime attempted to bind a different protocol instance")
        await self._inner.start(instance, signals)

    async def stop(self) -> None:
        await self._inner.stop()

    def samples(self):
        return self._inner.samples()

    async def publish(self, signal: ProtocolSignal, sample: SignalSample) -> None:
        # Physical writes remain denied by the Modbus adapter. Do not invent
        # generic control permission in an IPC/read-model bridge.
        await self._inner.publish(signal, sample)

    def device_page(self, *, offset: int, limit: int) -> dict:
        if type(offset) is not int or offset < 0:
            raise ValueError("Device offset must be a nonnegative integer")
        if type(limit) is not int or not 1 <= limit <= 128:
            raise ValueError("Device page limit must be 1..128")
        statuses = self._inner.device_statuses()
        rows = []
        for status in statuses[offset:offset + limit]:
            state = status.state.value if hasattr(status.state, "value") else str(status.state)
            at = status.last_rx_utc
            rows.append({
                "device_id": status.device_id,
                "state": state,
                "reads_ok": status.reads_ok,
                "read_errors": status.read_errors,
                "reconnects": status.reconnects,
                "last_error": status.last_error[:160],
                "last_rx_utc": at.isoformat() if isinstance(at, datetime) else None,
            })
        return {"total": len(statuses), "offset": offset, "items": rows,
                "source": "MODBUS_ADAPTER_RUNTIME"}

    @property
    def dropped_notifications(self) -> int:
        return int(getattr(self._inner, "dropped_notifications", 0))


def build_modbus_runtime(instance: ProtocolInstance,
                         devices: Sequence[Any],
                         signals: Sequence[ProtocolSignal],
                         *, adapter=None):
    """Composition root for a validated in-memory plan; NOT a deploy API.

    Caller must obtain the plan from an approved immutable revision after
    Store #3/RFC #13. Tests inject configurations without persisting/deploying.
    """
    from .adapter_runtime import AdapterRuntime

    bound = BoundModbusAdapter(instance, devices, inner=adapter)
    return AdapterRuntime(instance, tuple(signals), bound)
