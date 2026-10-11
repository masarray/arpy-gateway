"""Bounded read-only Modbus TCP server for mapped telemetry outputs.

FC01–04 supported. All Modbus writes fail with Illegal Function; the server
must never act as a physical control transport without an audited policy.
This is a focused transport implementation, not a full Modbus compliance claim.
"""
from __future__ import annotations
import asyncio
from collections import defaultdict
import math
import struct
from datetime import datetime, timezone
from typing import AsyncIterator

from arpy_gateway.domain.models import (InstanceRole, ProtocolInstance, ProtocolSignal,
                                         SignalSample, Quality, SignalDirection, SignalFamily)
from arpy_gateway.runtime.ports import ProtocolAdapter
from .configuration import TABLE_TO_FC, ParsedSignal, WIDTH, integer


class ModbusTcpServerAdapter(ProtocolAdapter):
    def __init__(self, *, max_clients=32, read_timeout=5.0):
        if not 1 <= max_clients <= 512:
            raise ValueError("max_clients must be 1..512")
        self._max_clients = max_clients
        self._timeout = max(0.5, min(read_timeout, 120))
        self._server = None
        self._sockets: set[asyncio.StreamWriter] = set()
        self._slots = asyncio.Semaphore(max_clients)
        self._config = {}
        self._tables: dict[tuple[int, int, int], tuple[str, int]] = {}
        self._values: dict[tuple[int, int, int], int] = {}
        self._signals: dict[str, tuple[int,int,int,int,ProtocolSignal]] = {}
        self._active = False
        self._instance: ProtocolInstance | None = None

    @property
    def protocol_id(self):
        return "modbus_tcp"

    @property
    def listening_address(self):
        if self._server and self._server.sockets:
            return self._server.sockets[0].getsockname()
        return None

    async def start(self, instance: ProtocolInstance, signals: tuple[ProtocolSignal, ...]):
        if self._active:
            raise RuntimeError("Already running")
        if instance.protocol != "modbus_tcp" or instance.role is not InstanceRole.SERVER:
            raise ValueError("Server requires modbus_tcp server instance")
        bind = instance.settings.get("bind", "127.0.0.1")
        if not isinstance(bind, str) or not bind.strip():
            raise ValueError("bind address required")
        port = integer(instance.settings.get("port", 1502), "listen port", 0, 65535)
        self._signals.clear()
        self._tables.clear()
        self._values.clear()
        for sig in signals:
            if sig.instance_id != instance.id:
                raise ValueError("Signal owned by wrong server instance")
            if not sig.enabled:
                continue
            if sig.direction not in (SignalDirection.OUTBOUND, SignalDirection.BIDIRECTIONAL):
                raise ValueError("Server telemetry signal must be publishable")
            if sig.family is SignalFamily.COMMAND:
                raise ValueError("Physical command signals cannot use read-only telemetry server")
            p = sig.parameters
            table = p.get("register_table", "")
            if table not in TABLE_TO_FC:
                raise ValueError("Invalid server table")
            func = TABLE_TO_FC[table]
            if func in (1, 2):
                if sig.value_type.value != "bool":
                    raise ValueError("Bit table requires bool signal")
            elif sig.value_type not in WIDTH or sig.value_type.value == "bool":
                raise ValueError("Unsupported numeric server point type")
            count = WIDTH[sig.value_type]
            address = integer(p.get("native_address"), "native_address", 0, 65535)
            unit_id = integer(p.get("unit_id", 1), "unit_id", 1, 247)
            if address + count > 65536:
                raise ValueError("Server point outside Modbus space")
            for n in range(count):
                key = (unit_id, func, address+n)
                if key in self._tables:
                    raise ValueError(f"Server PDU address collision: {key}")
                self._tables[key] = (sig.id, n)
            self._signals[sig.id] = (unit_id, func, address, count, sig)
        self._instance = instance
        if not instance.enabled:
            return
        # Explicit bind; prefer loopback for tests, never default 0.0.0.0.
        self._server = await asyncio.start_server(self._handle, bind, port,
                                                  backlog=self._max_clients)
        self._active = True

    async def stop(self):
        self._active = False
        if self._server:
            self._server.close()
            await self._server.wait_closed()
            self._server = None
        for writer in tuple(self._sockets):
            writer.close()
        if self._sockets:
            await asyncio.gather(*(w.wait_closed() for w in tuple(self._sockets)),
                                 return_exceptions=True)
        self._sockets.clear()

    async def samples(self) -> AsyncIterator[SignalSample]:
        if False:
            yield  # Modbus Server only serves outbound mapped samples; no inbound writes

    async def publish(self, signal: ProtocolSignal, sample: SignalSample) -> None:
        if sample.signal_id != signal.id or signal.id not in self._signals:
            raise ValueError("Unconfigured server signal")
        unit_id, func, address, count, _ = self._signals[signal.id]
        # Modbus has no generic Good/Bad quality register. Non-Good values
        # remove the published words so reads receive device failure (0x04),
        # never expose stale last-good data as freshly valid.
        if sample.quality is not Quality.GOOD:
            for index in range(count):
                self._values.pop((unit_id, func, address+index), None)
            return
        data = self._encode(signal, sample.value, count)
        for i, word in enumerate(data):
            self._values[(unit_id, func, address+i)] = word

    @staticmethod
    def _encode(sig, value, count):
        if count == 1 and sig.value_type.value == "bool":
            if not isinstance(value, bool):
                raise ValueError("Boolean point requires bool")
            return (int(value),)
        typecodes = {"int16": "h", "uint16": "H", "int32": "i", "uint32": "I",
                     "int64": "q", "uint64": "Q", "float32": "f", "float64": "d"}
        fmt = typecodes.get(sig.value_type.value)
        if fmt is None or isinstance(value, bool):
            raise ValueError("Invalid numeric server value")
        if fmt in ("f", "d"):
            if not isinstance(value, (int, float)) or not math.isfinite(value):
                raise ValueError("Non-finite/invalid server float")
        elif type(value) is not int:
            raise ValueError("Integer server point requires integer")
        try:
            payload = struct.pack(">" + fmt, value)
        except (OverflowError, struct.error) as exc:
            raise ValueError("Value outside server type range") from exc
        words = [payload[i:i+2] for i in range(0, len(payload), 2)]
        if sig.parameters.get("byte_order", "big") == "little":
            words = [w[::-1] for w in words]
        if sig.parameters.get("word_order", "big") == "little":
            words.reverse()
        return tuple(int.from_bytes(word, "big") for word in words)

    def _answer(self, unit, function, body):
        # unsupported function and length are diagnosed with proper exception,
        # never mutate any actual field device.
        if function not in (1, 2, 3, 4):
            return bytes((function | 0x80, 1))
        if len(body) != 4:
            return bytes((function | 0x80, 3))
        start, quantity = struct.unpack(">HH", body)
        max_count = 2000 if function < 3 else 125
        if not 1 <= quantity <= max_count:
            return bytes((function | 0x80, 3))
        if start + quantity > 65536:
            return bytes((function | 0x80, 2))
        keys = tuple((unit, function, start+i) for i in range(quantity))
        if any(key not in self._tables for key in keys):
            return bytes((function | 0x80, 2))
        if any(key not in self._values for key in keys):
            return bytes((function | 0x80, 4))
        if function < 3:
            payload = bytearray((quantity+7)//8)
            for i, key in enumerate(keys):
                if self._values[key]:
                    payload[i//8] |= 1 << (i % 8)
        else:
            payload = b"".join(struct.pack(">H", self._values[key]) for key in keys)
        return bytes((function, len(payload))) + bytes(payload)

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        if self._slots.locked():
            writer.close()
            await writer.wait_closed()
            return
        async with self._slots:
            self._sockets.add(writer)
            try:
                while self._active:
                    # Strict MBAP 7-byte header, transaction echo, PDU max 253.
                    header = await asyncio.wait_for(reader.readexactly(7), self._timeout)
                    transaction, protocol, length, unit = struct.unpack(">HHHB", header)
                    if protocol != 0 or not 2 <= length <= 254 or unit > 247:
                        break
                    pdu = await asyncio.wait_for(reader.readexactly(length-1), self._timeout)
                    if not pdu:
                        break
                    answer = self._answer(unit, pdu[0], pdu[1:])
                    response = struct.pack(">HHHB", transaction, 0, len(answer)+1, unit)+answer
                    writer.write(response)
                    await asyncio.wait_for(writer.drain(), self._timeout)
            except (asyncio.IncompleteReadError, asyncio.TimeoutError, ConnectionError, OSError):
                pass
            finally:
                self._sockets.discard(writer)
                writer.close()
                try:
                    await writer.wait_closed()
                except OSError:
                    pass
