"""Pure asyncio client used by Studio IPC worker in later GUI integration.

One framed request per connection: no persistent Studio connection owns
runtime data plane lifecycle. No Qt imports or adapter imports.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from uuid import uuid4

from arpy_gateway.contracts.ipc import API_VERSION, Envelope, MessageKind
from .framing import IPCFrameError, receive, send


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


class ViewportSession:
    """Client-side viewport read model suitable for Qt worker integration.

    Snapshots and deltas never reach a Qt main thread directly. When IPC
    disconnects the session clears cached GOOD values rather than presenting
    them as current data. No timer or background worker is created here.
    """

    def __init__(self, offset: int = 0, limit: int = 128):
        if type(offset) is not int or offset < 0:
            raise ValueError("Invalid viewport offset")
        if type(limit) is not int or not 1 <= limit <= 128:
            raise ValueError("Invalid viewport size")
        self.offset, self.limit = offset, limit
        self.generation: str | None = None
        self.cursor: int | None = None
        self.items: dict[str, dict] = {}
        self.state = "DISCONNECTED"
        self.error_code: str | None = None

    def set_viewport(self, offset: int, limit: int):
        if type(offset) is not int or offset < 0:
            raise ValueError("Invalid viewport offset")
        if type(limit) is not int or not 1 <= limit <= 128:
            raise ValueError("Invalid viewport size")
        self.offset, self.limit = offset, limit
        self.generation = None
        self.cursor = None
        self.items.clear()
        self.state = "DISCONNECTED"

    async def refresh(self, port: int, token: str) -> dict:
        try:
            full_snapshot = self.generation is None
            if not full_snapshot:
                delta = await request(port, token, "signals.delta", {
                    "offset": self.offset, "limit": self.limit,
                    "generation": self.generation, "cursor": self.cursor,
                })
                full_snapshot = delta["resync_required"]
            if full_snapshot:
                packet = await request(port, token, "signals.page", {
                    "offset": self.offset, "limit": self.limit,
                })
                self.items = {row["signal_id"]: row for row in packet["items"]}
            else:
                packet = delta
                for row in packet["items"]:
                    if row["signal_id"] in self.items:
                        self.items[row["signal_id"]] = row
            self.generation, self.cursor = packet["generation"], packet["cursor"]
            self.state = "CONNECTED"
            self.error_code = None
            return {"state": self.state, "full_snapshot": full_snapshot,
                    "cursor": self.cursor, "generation": self.generation,
                    "items": list(self.items.values()), "source": packet["source"]}
        except (OSError, ConnectionError, asyncio.TimeoutError,
                asyncio.IncompleteReadError, IPCRemoteError,
                IPCFrameError, KeyError, TypeError, ValueError) as exc:
            # Never display cached "Good" as fresh while the runtime is gone.
            self.items.clear()
            self.generation = None
            self.cursor = None
            self.state = "DISCONNECTED"
            self.error_code = (exc.code if isinstance(exc, IPCRemoteError)
                               else "IPC_DISCONNECTED")
            return {"state": self.state, "items": [], "error_code": self.error_code}
