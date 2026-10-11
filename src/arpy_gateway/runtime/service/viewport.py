"""Bounded, cursor-based read-only viewport change tracking.

For Studio monitoring only: NEVER feed protocol delivery from this lossy index.
An explicit gap/restart response requires a fresh page snapshot. No client
subscriptions, timers, per-signal threads or unbounded event retention.
"""
from __future__ import annotations

from collections import deque
from uuid import uuid4


class ViewportChanges:
    def __init__(self, capacity: int = 4096):
        if type(capacity) is not int or not 128 <= capacity <= 65536:
            raise ValueError("Invalid bounded viewport event capacity")
        self.generation = uuid4().hex
        self._events: deque[tuple[int, int]] = deque(maxlen=capacity)
        self.cursor = 0
        self._reset_floor = 0
        self.dropped_events = 0

    def mark(self, signal_index: int) -> None:
        # A global cursor is distinct from each ProtocolSignal's own sequence.
        if type(signal_index) is not int or signal_index < 0:
            raise ValueError("Invalid signal index")
        self.cursor += 1
        if len(self._events) == self._events.maxlen:
            self.dropped_events += 1
        self._events.append((self.cursor, signal_index))

    def invalidate(self) -> None:
        """O(1) runtime-wide quality change; old cursors must resync."""
        self.cursor += 1
        self._events.clear()
        self._reset_floor = self.cursor

    def changes(self, *, generation: str, cursor: int, offset: int,
                limit: int) -> dict:
        if type(generation) is not str or len(generation) > 80:
            raise ValueError("Invalid generation")
        if type(cursor) is not int or cursor < 0:
            raise ValueError("Invalid cursor")
        if type(offset) is not int or offset < 0:
            raise ValueError("Invalid offset")
        if type(limit) is not int or not 1 <= limit <= 128:
            raise ValueError("Invalid limit")
        needs_resync = (
            generation != self.generation
            or cursor > self.cursor
            or cursor < self._reset_floor
            or bool(self._events and cursor < self._events[0][0] - 1)
        )
        result = {
            "generation": self.generation,
            "cursor": self.cursor,
            "resync_required": needs_resync,
            "offset": offset,
            "limit": limit,
            "dropped_history": self.dropped_events,
        }
        if needs_resync:
            result["indices"] = ()
            return result
        # At most the fixed ring capacity is inspected, not 500k signals.
        # Deduplicate multiple updates to the same visible signal: Studio
        # needs its current value, not per-event protocol history.
        visible = set()
        for event_seq, index in reversed(self._events):
            if event_seq <= cursor:
                break
            if offset <= index < offset + limit:
                visible.add(index)
            if len(visible) == limit:
                break
        result["indices"] = tuple(sorted(visible))
        return result

    @property
    def depth(self) -> int:
        return len(self._events)
