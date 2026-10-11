"""Length-framed JSON transport with hard caps, deadlines, duplicate-key rejection.

Local IPC wire framing only. Does not modify v1 canonical envelope schema.
"""
from __future__ import annotations

import asyncio
import json
import struct
from typing import Any

MAX_FRAME_BYTES = 64 * 1024
IO_TIMEOUT = 4.0


class IPCFrameError(ValueError):
    """Malformed or oversized IPC frame; safe to reject without interpretation."""


def _pairs(pairs):
    obj = {}
    for key, value in pairs:
        if key in obj:
            raise IPCFrameError("Duplicate JSON key")
        obj[key] = value
    return obj


def _invalid_number(value):
    raise IPCFrameError("Invalid JSON number")


async def receive(reader: asyncio.StreamReader) -> dict[str, Any]:
    try:
        prefix = await asyncio.wait_for(reader.readexactly(4), IO_TIMEOUT)
        (size,) = struct.unpack(">I", prefix)
        if not 2 <= size <= MAX_FRAME_BYTES:
            raise IPCFrameError("Frame length outside allowed range")
        payload = await asyncio.wait_for(reader.readexactly(size), IO_TIMEOUT)
        data = json.loads(payload.decode("utf-8"),
                          object_pairs_hook=_pairs,
                          parse_constant=_invalid_number)
        if type(data) is not dict:
            raise IPCFrameError("Envelope must be JSON object")
        return data
    except (asyncio.IncompleteReadError, asyncio.TimeoutError,
            UnicodeDecodeError, json.JSONDecodeError, RecursionError,
            OverflowError) as exc:
        raise IPCFrameError("Invalid or incomplete IPC frame") from exc


async def send(writer: asyncio.StreamWriter, message: dict) -> None:
    try:
        data = json.dumps(message, separators=(",", ":"), ensure_ascii=False,
                          allow_nan=False).encode("utf-8")
    except (TypeError, ValueError, RecursionError) as exc:
        raise IPCFrameError("Unable to encode response") from exc
    if not 2 <= len(data) <= MAX_FRAME_BYTES:
        raise IPCFrameError("Response exceeds bounded frame size")
    writer.write(struct.pack(">I", len(data)) + data)
    await asyncio.wait_for(writer.drain(), IO_TIMEOUT)
