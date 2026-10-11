"""Deterministic fake adapter process for service/IPC lifecycle integration.

Explicitly TEST-ONLY. It generates derived samples without field I/O. It is
never a claimed Modbus/IEC-104 connection and contains no PySide6 dependency.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from arpy_gateway.domain.models import Quality, SignalOrigin, SignalSample
from .viewport import ViewportChanges


class FixtureRuntime:
    """Small deterministic test signal source with bounded last-value state."""

    def __init__(self, *, period: float = 0.05, num_signals: int = 8):
        if not 0.01 <= period <= 10.0:
            raise ValueError("Invalid demo sample period")
        if not 1 <= num_signals <= 1000:
            raise ValueError("Fixture signal count outside bound")
        self.period = period
        self.signal_ids = tuple(f"fixture:signal:{i:06d}" for i in range(num_signals))
        self.latest: dict[str, SignalSample] = {}
        self._task: asyncio.Task | None = None
        self._count = 0
        self._changes = ViewportChanges()
        self._started_at: datetime | None = None

    async def start(self):
        if self._task is not None:
            raise RuntimeError("Runtime already started")
        self._started_at = datetime.now(timezone.utc)
        self._task = asyncio.create_task(self._run(), name="runtime:fixture-signal-source")

    async def _run(self):
        try:
            while True:
                self._count += 1
                now = datetime.now(timezone.utc)
                # O(1) per tick; not one timer or thread per signal.
                sid = self.signal_ids[(self._count - 1) % len(self.signal_ids)]
                old = self.latest.get(sid)
                seq = old.sequence + 1 if old is not None else 1
                self.latest[sid] = SignalSample(
                    sid, self._count, Quality.GOOD, SignalOrigin.DERIVED, now, seq)
                self._changes.mark((self._count - 1) % len(self.signal_ids))
                await asyncio.sleep(self.period)
        except asyncio.CancelledError:
            raise

    async def stop(self):
        task, self._task = self._task, None
        if task:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    def health(self):
        return {
            "state": "RUNNING" if self._task is not None and not self._task.done()
                     else "STOPPED",
            "source": "DETERMINISTIC_FIXTURE_ONLY",
            "runtime_ready": self._task is not None and not self._task.done(),
            "active_revision": None,  # no validated deployed snapshot exists
            "frames_generated": self._count,
            "signals_configured": len(self.signal_ids),
            "signals_sampled": len(self.latest),
            "monitor_cursor": self._changes.cursor,
            "monitor_history_depth": self._changes.depth,
            "monitor_history_dropped": self._changes.dropped_events,
            "started_utc": self._started_at.isoformat() if self._started_at else None,
        }

    def page(self, *, offset: int, limit: int):
        if type(offset) is not int or offset < 0:
            raise ValueError("offset must be nonnegative integer")
        if type(limit) is not int or not 1 <= limit <= 128:
            raise ValueError("limit must be 1..128")
        records = []
        for sid in self.signal_ids[offset:offset + limit]:
            sample = self.latest.get(sid)
            records.append({
                "signal_id": sid,
                "value": sample.value if sample is not None else None,
                "quality": sample.quality.value if sample else "unavailable",
                "origin": sample.origin.value if sample else None,
                "sequence": sample.sequence if sample else 0,
                "observed_at": sample.observed_at.isoformat() if sample else None,
            })
        return {"total": len(self.signal_ids), "offset": offset,
                "items": records, "source": "DETERMINISTIC_FIXTURE_ONLY",
                "generation": self._changes.generation,
                "cursor": self._changes.cursor}

    def delta(self, *, generation: str, cursor: int, offset: int, limit: int):
        data = self._changes.changes(generation=generation, cursor=cursor,
                                     offset=offset, limit=limit)
        indices = data.pop("indices")
        data["items"] = [self.page(offset=i, limit=1)["items"][0] for i in indices]
        data["source"] = "DETERMINISTIC_FIXTURE_ONLY"
        return data
