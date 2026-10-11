"""Deterministic O(S log S) batched Modbus poll planner per configured IED.

Merge only overlapping or adjacent intervals for same function. No cross-FC
or cross-device merge, no unrequested holes, no request > protocol limit.
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import Sequence
from .configuration import ParsedSignal


@dataclass(frozen=True)
class ReadRequest:
    device_id: str
    function: int
    address: int
    count: int
    points: tuple[ParsedSignal, ...]


def compile_reads(points: Sequence[ParsedSignal]) -> tuple[ReadRequest, ...]:
    buckets: dict[tuple[str, int], list[ParsedSignal]] = {}
    for point in points:
        buckets.setdefault((point.device_id, point.function), []).append(point)
    out = []
    for (did, func), group in sorted(buckets.items()):
        limit = 2000 if func in (1, 2) else 125
        ordered = sorted(group, key=lambda p: (p.address, p.address+p.width, p.signal.id))
        start = end = -1
        selected: list[ParsedSignal] = []

        def emit() -> None:
            if selected:
                out.append(ReadRequest(did, func, start, end-start, tuple(selected)))

        for p in ordered:
            new_end = max(end, p.address+p.width)
            if selected and (p.address > end or new_end-start > limit):
                emit()
                selected = []
            if not selected:
                start = p.address
                end = p.address + p.width
            else:
                end = new_end
            selected.append(p)
        emit()
    return tuple(out)
