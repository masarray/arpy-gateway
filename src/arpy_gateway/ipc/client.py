"""Pure asyncio client used by Studio IPC worker in later GUI integration.

One framed request per connection: no persistent Studio connection owns
runtime data plane lifecycle. No Qt imports or adapter imports.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from uuid import uuid4

from arpy_gateway.contracts.ipc import API_VERSION, Envelope, MessageKind
from .framing import receive, send


class IPCRemoteError(Exception):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


async def request(port: int, token: str, method: str,
                  params: dict | None = None, timeout: float = 6.0) -> dict:
    if not isinstance(port, int) or not 1 <= port <= 65535:
        raise ValueError("port must be a valid local TCP port")
    msg = Envelope(
        API_VERSION, str(uuid4()), MessageKind.REQUEST, datetime.now(timezone.utc),
        {"auth": token, "method": method, "params": params or {}},
    ).to_json_object()

    async def operation():
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        try:
            await send(writer, msg)
            reply = await receive(reader)
            if (reply.get("correlation_id") != msg["message_id"] or
                    reply.get("api_version") != API_VERSION or
                    reply.get("kind") not in ("response", "error") or
                    type(reply.get("payload")) is not dict):
                raise IPCRemoteError("INVALID_IPC_RESPONSE")
            if reply["kind"] == "error":
                raise IPCRemoteError(str(reply["payload"].get("code", "UNKNOWN_ERROR")))
            return reply["payload"]
        finally:
            writer.close()
            await writer.wait_closed()

    return await asyncio.wait_for(operation(), timeout)
