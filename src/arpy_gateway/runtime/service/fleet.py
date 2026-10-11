"""Multi-instance headless monitoring coordinator for independent adapter services.

Not an event-delivery bus or a deployment manager. Per-instance supervisors
own protocol sockets. The fleet owns only bounded, generation-scoped, read-only
IPC projections, stable pagination and health aggregation. No GUI/Qt imports.
"""
from __future__ import annotations

import asyncio
from bisect import bisect_right
from datetime import datetime, timezone

from .viewport import ViewportChanges


class RuntimeFleet:
    """At most 64 injected, independently supervised protocol instances."""

    def __init__(self, instances):
        self._runtimes = tuple(instances)
        if not 1 <= len(self._runtimes) <= 64:
            raise ValueError("Fleet must contain 1..64 instance runtimes")
        self._offsets = [0]
        seen = set()
        for runtime in self._runtimes:
            ids = runtime.signal_ids
            if any(sid in seen for sid in ids):
                raise ValueError("Same stable signal ID belongs to multiple instances")
            seen.update(ids)
            self._offsets.append(self._offsets[-1] + len(ids))
        if self._offsets[-1] > 500_000:
            raise ValueError("Fleet exceeds 500k signal capacity")
        self._changes = ViewportChanges()
        self._state = "UNCONFIGURED"
        self._started_at = None
        self._start_errors = {}
        self._device_counts = []
        for i, runtime in enumerate(self._runtimes):
            base = self._offsets[i]
            runtime.set_change_sink(lambda index, base=base: self._change(base, index))

    def _change(self, base: int, index: int | None):
        if index is None:
            self._changes.invalidate()
        else:
            self._changes.mark(base + index)

    async def start(self):
        if self._state != "UNCONFIGURED":
            raise RuntimeError("Fleet already started")
        self._state = "STARTING"
        self._started_at = datetime.now(timezone.utc)
        # All instances initialize independently; one unreachable IED is not
        # allowed to prevent healthy instances serving. Each adapter.start()
        # has its own bounded deadline; failed instances report actionable
        # status but have no open field outputs.
        results = await asyncio.gather(
            *(asyncio.wait_for(runtime.start(), 8.0)
              for runtime in self._runtimes),
            return_exceptions=True)
        errors = {}
        count_ready = 0
        for i, result in enumerate(results):
            if isinstance(result, BaseException):
                errors[i] = "START_" + type(result).__name__.upper()
            else:
                count_ready += 1
        self._start_errors = errors
        self._state = "RUNNING" if not errors else "DEGRADED" if count_ready else "FAILED_TO_START"
        self._device_counts = []
        for runtime in self._runtimes:
            if runtime.has_device_view():
                self._device_counts.append(runtime.device_page(offset=0, limit=1)["total"])
            else:
                self._device_counts.append(0)
        if count_ready == 0:
            raise RuntimeError("No validated protocol instance started")

    async def stop(self):
        self._state = "STOPPING"
        results = await asyncio.gather(
            *(asyncio.wait_for(runtime.stop(), 5.0)
              for runtime in self._runtimes), return_exceptions=True)
        self._state = "STOPPED"
        errors = [r for r in results if isinstance(r, BaseException)]
        if errors:
            raise RuntimeError("One or more instance cleanup operations failed")

    def health(self):
        rows = []
        for i, runtime in enumerate(self._runtimes):
            item = runtime.health()
            rows.append({
                "instance_id": item.get("instance_id"),
                "protocol": item.get("protocol"),
                "state": item["state"],
                "signals_configured": item["signals_configured"],
                "signals_sampled": item["signals_sampled"],
                "samples_received": item["frames_generated"],
                "start_error": self._start_errors.get(i),
                "error_code": item.get("error_code"),
            })
        if self._state in ("RUNNING", "DEGRADED"):
            state = ("RUNNING" if all(x["state"] == "RUNNING" for x in rows)
                     else "DEGRADED")
        else:
            state = self._state
        return {
            "state": state, "source": "INJECTED_ADAPTER_FLEET",
            "runtime_ready": state in ("RUNNING", "DEGRADED"),
            "active_revision": None,  # Pending approved Store/Deploy.
            "instances": rows,
            "instances_total": len(rows),
            "frames_generated": sum(x["samples_received"] for x in rows),
            "signals_configured": self._offsets[-1],
            "signals_sampled": sum(x["signals_sampled"] for x in rows),
            "monitor_cursor": self._changes.cursor,
            "monitor_history_depth": self._changes.depth,
            "monitor_history_dropped": self._changes.dropped_events,
            "started_utc": self._started_at.isoformat() if self._started_at else None,
        }

    def page(self, *, offset: int, limit: int):
        if type(offset) is not int or offset < 0:
            raise ValueError("offset must be a nonnegative integer")
        if type(limit) is not int or not 1 <= limit <= 128:
            raise ValueError("limit must be between 1..128")
        first, last = offset, min(offset + limit, self._offsets[-1])
        items = []
        while first < last:
            i = bisect_right(self._offsets, first) - 1
            take = min(last - first, self._offsets[i + 1] - first)
            if take <= 0:
                raise RuntimeError("Nonprogressing fleet page cursor")
            batch = self._runtimes[i].page(offset=first - self._offsets[i],
                                           limit=take)
            items.extend(batch["items"])
            first += take
        return {
            "total": self._offsets[-1], "offset": offset, "items": items,
            "generation": self._changes.generation, "cursor": self._changes.cursor,
            "source": "INJECTED_ADAPTER_FLEET",
        }

    def delta(self, *, generation: str, cursor: int, offset: int, limit: int):
        data = self._changes.changes(generation=generation, cursor=cursor,
                                     offset=offset, limit=limit)
        indices = data.pop("indices")
        data["items"] = [self.page(offset=i, limit=1)["items"][0] for i in indices]
        data["source"] = "INJECTED_ADAPTER_FLEET"
        return data

    def has_device_view(self):
        return any(runtime.has_device_view() for runtime in self._runtimes)

    def device_page(self, *, offset: int, limit: int):
        if type(offset) is not int or offset < 0:
            raise ValueError("offset must be nonnegative integer")
        if type(limit) is not int or not 1 <= limit <= 128:
            raise ValueError("limit must be 1..128")
        total = sum(self._device_counts)
        start, end = offset, min(total, offset + limit)
        items = []
        for i, (runtime, count) in enumerate(zip(self._runtimes, self._device_counts)):
            if start >= count:
                start -= count
                end -= count
                continue
            take = min(count - start, max(0, end - start))
            if take:
                batch = runtime.device_page(offset=start, limit=take)
                instance_id = runtime.health()["instance_id"]
                items.extend({**row, "instance_id": instance_id}
                             for row in batch["items"])
            end -= count
            start = 0
            if len(items) >= limit:
                break
        return {"total": total, "offset": offset, "items": items,
                "source": "INJECTED_ADAPTER_FLEET"}
