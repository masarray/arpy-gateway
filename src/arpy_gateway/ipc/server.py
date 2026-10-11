"""Authenticated *read-only* local control plane for the headless fixture runtime.

Not a production security implementation: loopback + shared secret is a
developer/test transport. Production OS peer-credential/ACL IPC is issue #8's
next gate. No remote listener, no deployment or control commands exposed.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import hmac
from uuid import uuid4

from arpy_gateway.contracts.ipc import API_VERSION, Envelope, MessageKind
from .framing import IPCFrameError, receive, send


class RequestFault(Exception):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


class LocalReadOnlyServer:
    def __init__(self, runtime, *, token: str, max_clients: int = 16):
        if type(token) is not str or len(token) < 32 or len(token) > 256:
            raise ValueError("IPC test secret must contain 32..256 characters")
        if not 1 <= max_clients <= 64:
            raise ValueError("IPC max clients must be in 1..64")
        self.runtime, self._token = runtime, token
        self._slots = asyncio.Semaphore(max_clients)
        self._server: asyncio.AbstractServer | None = None
        self._clients: set[asyncio.StreamWriter] = set()
        self._tasks: set[asyncio.Task] = set()
        self._rejected = 0

    async def start(self, port: int = 0) -> int:
        if self._server is not None:
            raise RuntimeError("IPC already started")
        if type(port) is not int or not 0 <= port <= 65535:
            raise ValueError("Invalid port")
        # Hardcoded loopback. This transport must never expose admin services
        # by changing a user-provided bind to 0.0.0.0.
        self._server = await asyncio.start_server(self._handle, host="127.0.0.1",
                                                  port=port, backlog=16)
        return self._server.sockets[0].getsockname()[1]

    async def stop(self):
        server, self._server = self._server, None
        if server is not None:
            server.close()
            await server.wait_closed()
        clients = tuple(self._clients)
        for writer in clients:
            writer.close()
        current = asyncio.current_task()
        tasks = tuple(t for t in self._tasks if t is not current)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        if clients:
            await asyncio.gather(*(w.wait_closed() for w in clients),
                                 return_exceptions=True)
        self._clients.clear()

    def _reply(self, kind: MessageKind, data: dict, correlation: str = ""):
        return Envelope(
            API_VERSION, str(uuid4()), kind, datetime.now(timezone.utc),
            data, correlation_id=correlation
        ).to_json_object()

    def _validate(self, frame: dict):
        required = {"api_version", "message_id", "kind", "sent_at",
                    "correlation_id", "payload"}
        if set(frame) != required:
            raise RequestFault("MALFORMED_ENVELOPE")
        if frame["api_version"] != API_VERSION:
            raise RequestFault("UNSUPPORTED_API_VERSION")
        if frame["kind"] != "request" or not isinstance(frame["message_id"], str) or not (
                1 <= len(frame["message_id"]) <= 100):
            raise RequestFault("MALFORMED_ENVELOPE")
        try:
            at = datetime.fromisoformat(frame["sent_at"])
        except (TypeError, ValueError, OverflowError):
            raise RequestFault("INVALID_TIMESTAMP") from None
        if at.tzinfo is None:
            raise RequestFault("INVALID_TIMESTAMP")
        payload = frame["payload"]
        if type(payload) is not dict or set(payload) != {"auth", "method", "params"}:
            raise RequestFault("MALFORMED_PAYLOAD")
        if type(payload["auth"]) is not str or not hmac.compare_digest(
                payload["auth"], self._token):
            raise RequestFault("UNAUTHORIZED")
        method, params = payload["method"], payload["params"]
        if type(method) is not str or type(params) is not dict:
            raise RequestFault("MALFORMED_PAYLOAD")
        return method, params

    def _dispatch(self, method: str, params: dict):
        if method == "hello":
            if params:
                raise RequestFault("BAD_PARAMETERS")
            return {"api_version": API_VERSION,
                    "transport": "loopback_fixture_only",
                    "capabilities": ["health", "signals.page", "diagnostics"],
                    "mutable_operations": False,
                    "source": "DETERMINISTIC_FIXTURE_ONLY"}
        if method == "health":
            if params:
                raise RequestFault("BAD_PARAMETERS")
            return self.runtime.health()
        if method == "signals.page":
            if set(params) != {"offset", "limit"}:
                raise RequestFault("BAD_PARAMETERS")
            try:
                return self.runtime.page(**params)
            except (TypeError, ValueError):
                raise RequestFault("BAD_PARAMETERS") from None
        if method == "diagnostics":
            if params:
                raise RequestFault("BAD_PARAMETERS")
            return {"ipc_rejected": self._rejected,
                    "runtime": self.runtime.health(),
                    "notice": "No real protocol adapter or engineering revision deployed"}
        # Mutation/control commands are deliberately absent, even when token
        # is correct. They require roles, CAS, idempotency and audit in #8.
        raise RequestFault("OPERATION_NOT_SUPPORTED")

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        if self._slots.locked():
            writer.close()
            await writer.wait_closed()
            return
        current = asyncio.current_task()
        self._tasks.add(current)
        self._clients.add(writer)
        async with self._slots:
            correlation = ""
            try:
                frame = await receive(reader)
                if isinstance(frame.get("message_id"), str):
                    correlation = frame["message_id"][:100]
                method, params = self._validate(frame)
                # Current read-only methods are O(1) or bounded page <=128.
                result = self._dispatch(method, params)
                response = self._reply(MessageKind.RESPONSE, result, correlation)
            except RequestFault as exc:
                self._rejected += 1
                response = self._reply(MessageKind.ERROR, {"code": exc.code}, correlation)
            except IPCFrameError:
                self._rejected += 1
                response = self._reply(MessageKind.ERROR, {"code": "INVALID_FRAME"},
                                       correlation)
            try:
                await send(writer, response)
            except (IPCFrameError, ConnectionError, OSError, asyncio.TimeoutError):
                pass
            finally:
                self._clients.discard(writer)
                self._tasks.discard(current)
                writer.close()
                try:
                    await asyncio.wait_for(writer.wait_closed(), 2.0)
                except (OSError, ConnectionError, asyncio.TimeoutError):
                    pass
