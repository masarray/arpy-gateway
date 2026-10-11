"""Adapter-to-IPC supervision bridge using the accepted ProtocolAdapter port.

Production source adapters are injected by composition root after approved
config deployment; never imported directly by Studio or IPC transport.
No per-signal worker, full-snapshot rebuild per tick or unbounded event history.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Sequence

from arpy_gateway.domain.models import (
    ProtocolInstance, ProtocolSignal, SignalSample, Quality,
)
from arpy_gateway.runtime.ports import ProtocolAdapter
from .viewport import ViewportChanges


class AdapterRuntime:
    """Own a single protocol adapter and a bounded latest-sample read model.

    The adapter's lossy sample iterator is safe only for *monitoring*. Mapped
    protocol delivery MUST consume a lossless/appropriately backpressured data
    path in its own runtime implementation, not these IPC snapshots.
    """

    def __init__(self, instance: ProtocolInstance,
                 signals: Sequence[ProtocolSignal],
                 adapter: ProtocolAdapter):
        self._instance = instance
        self._signals = tuple(x for x in signals if x.enabled)
        if len(self._signals) > 500_000:
            raise ValueError("Protocol instance configured above 500k signal limit")
        self._ids = tuple(s.id for s in self._signals)
        self._ordinal = {sid: i for i, sid in enumerate(self._ids)}
        if len(self._ordinal) != len(self._ids):
            raise ValueError("Duplicate stable signal ID")
        if any(s.instance_id != instance.id for s in self._signals):
            raise ValueError("Foreign signal instance")
        if adapter.protocol_id != instance.protocol:
            raise ValueError("Wrong adapter protocol family")
        self._adapter = adapter
        self._latest: dict[str, SignalSample] = {}
        self._changes = ViewportChanges()
        self._reader: asyncio.Task | None = None
        self._state = "UNCONFIGURED"
        self._received = 0
        self._ignored = 0
        self._started: datetime | None = None
        self._error: str | None = None
        self._failure_at: datetime | None = None
        self._monitor_sink = None

    @property
    def signal_ids(self) -> tuple[str, ...]:
        return self._ids

    def set_change_sink(self, sink):
        """Attach a fleet monitor callback only before adapter startup."""
        if self._state != "UNCONFIGURED":
            raise RuntimeError("Cannot attach monitor after runtime start")
        if sink is not None and not callable(sink):
            raise TypeError("monitor sink must be callable")
        self._monitor_sink = sink

    async def start(self):
        if self._state != "UNCONFIGURED":
            raise RuntimeError("Adapter runtime already started")
        self._state = "STARTING"
        try:
            await self._adapter.start(self._instance, self._signals)
        except Exception:
            self._state = "FAILED_TO_START"
            # Partial transport initialization may already own a socket/COM
            # handle. Run bounded cleanup while preserving original failure.
            try:
                await asyncio.wait_for(self._adapter.stop(), 5.0)
            except Exception:
                pass
            # Propagate so packaging/deployment errors do not retry forever.
            raise
        self._state = "RUNNING"
        self._started = datetime.now(timezone.utc)
        self._reader = asyncio.create_task(self._consume(),
                                            name=f"runtime-adapter:{self._instance.id}")

    async def _consume(self):
        try:
            async for sample in self._adapter.samples():
                if self._state != "RUNNING":
                    break
                index = self._ordinal.get(sample.signal_id)
                if index is None:
                    self._ignored += 1
                    continue
                last = self._latest.get(sample.signal_id)
                if last is not None and sample.sequence <= last.sequence:
                    self._ignored += 1
                    continue
                # Mutate latest O(1); never buffer unlimited per-signal history.
                self._latest[sample.signal_id] = sample
                self._received += 1
                self._changes.mark(index)
                if self._monitor_sink is not None:
                    self._monitor_sink(index)
            if self._state == "RUNNING":
                self._mark_link_failed("SAMPLE_STREAM_ENDED")
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self._mark_link_failed("SAMPLE_STREAM_" + type(exc).__name__.upper())

    def _mark_link_failed(self, reason: str):
        # O(1) transition even with 500k signals. Page projection derives
        # invalid quality lazily rather than rewriting half a million samples.
        self._state = "DEGRADED"
        self._error = reason
        self._failure_at = datetime.now(timezone.utc)
        self._changes.invalidate()
        if self._monitor_sink is not None:
            self._monitor_sink(None)
        # Unseen samples remain unavailable; no invented zero or fresh Good.

    async def stop(self):
        self._state = "STOPPING"
        reader, self._reader = self._reader, None
        if reader is not None:
            reader.cancel()
            await asyncio.gather(reader, return_exceptions=True)
        try:
            await asyncio.wait_for(self._adapter.stop(), 5.0)
        finally:
            self._state = "STOPPED"

    def health(self):
        return {
            "state": self._state,
            "source": "INJECTED_ADAPTER",
            "runtime_ready": self._state == "RUNNING",
            "instance_id": self._instance.id,
            "protocol": self._instance.protocol,
            "active_revision": None,  # snapshot activation owned by #3/#8
            "frames_generated": self._received,
            "samples_ignored": self._ignored,
            "adapter_monitor_dropped": int(getattr(self._adapter, "dropped_notifications", 0)),
            "signals_configured": len(self._ids),
            "signals_sampled": len(self._latest),
            "monitor_cursor": self._changes.cursor,
            "monitor_history_depth": self._changes.depth,
            "monitor_history_dropped": self._changes.dropped_events,
            "started_utc": self._started.isoformat() if self._started else None,
            "error_code": self._error,
        }

    def page(self, *, offset: int, limit: int):
        if type(offset) is not int or offset < 0:
            raise ValueError("offset must be nonnegative integer")
        if type(limit) is not int or not 1 <= limit <= 128:
            raise ValueError("limit must be between 1 and 128")
        rows = []
        for sid in self._ids[offset:offset + limit]:
            sample = self._latest.get(sid)
            failed = self._state == "DEGRADED" and sample is not None
            rows.append({
                "signal_id": sid,
                "value": None if failed else sample.value if sample else None,
                "quality": ("communication_lost" if failed else
                            sample.quality.value if sample else "unavailable"),
                "origin": sample.origin.value if sample else None,
                "sequence": sample.sequence + 1 if failed else sample.sequence if sample else 0,
                "observed_at": (self._failure_at.isoformat() if failed else
                                sample.observed_at.isoformat() if sample else None),
                "source_timestamp": sample.source_timestamp.isoformat()
                                    if sample and sample.source_timestamp else None,
            })
        return {"total": len(self._ids), "offset": offset,
                "items": rows, "source": "INJECTED_ADAPTER",
                "generation": self._changes.generation,
                "cursor": self._changes.cursor}

    def has_device_view(self) -> bool:
        return callable(getattr(self._adapter, "device_page", None))

    def device_page(self, *, offset: int, limit: int):
        """Read-only paged transport-health capability if adapter exports it."""
        page = getattr(self._adapter, "device_page", None)
        if not callable(page):
            raise ValueError("Device status view unavailable for this adapter")
        return page(offset=offset, limit=limit)

    def delta(self, *, generation: str, cursor: int, offset: int, limit: int):
        data = self._changes.changes(generation=generation, cursor=cursor,
                                     offset=offset, limit=limit)
        indices = data.pop("indices")
        data["items"] = [self.page(offset=i, limit=1)["items"][0] for i in indices]
        data["source"] = "INJECTED_ADAPTER"
        return data
