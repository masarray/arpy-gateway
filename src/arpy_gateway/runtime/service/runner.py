"""Headless runtime + local IPC lifecycle shared by fixture and real adapters.

The runtime object is supplied by composition root; GUI may attach/detach
without controlling its lifecycle. This is not SCM/systemd installation.
"""
from __future__ import annotations

import asyncio
from typing import Callable

from arpy_gateway.ipc.server import LocalReadOnlyServer


async def serve_runtime(runtime, *, token: str, stop: asyncio.Event,
                        port: int = 0, ready: Callable[[int], None] | None = None):
    """Serve a preconstructed runtime; no Qt/Modbus import or mutable config IO.

    Return only on explicit service stop. Never link a connected IPC client's
    cancellation or socket close to stopping the runtime data-plane.
    """
    server = LocalReadOnlyServer(runtime, token=token)
    started = False
    try:
        await runtime.start()
        started = True
        actual_port = await server.start(port)
        if ready:
            ready(actual_port)
        await stop.wait()
    finally:
        await server.stop()
        if started:
            await runtime.stop()
