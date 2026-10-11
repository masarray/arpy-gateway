"""Headless asyncio Modbus TCP/RTU Master polling engine.

Runtime-only, no Qt, no persistent store. Stable DeviceConfig IDs come from
future approved Engineering Store #3/#13; attach Value Viewer through IPC #8.
"""
from __future__ import annotations
import asyncio
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
import random
from typing import AsyncIterator, Sequence

from arpy_gateway.domain.models import (InstanceRole, ProtocolInstance, ProtocolSignal,
                                         SignalSample, Quality, SignalOrigin)
from arpy_gateway.runtime.ports import ProtocolAdapter
from .configuration import DeviceConfig, ParsedSignal, parse_signal, validate_serial
from .encoding import decode_value
from .planner import ReadRequest, compile_reads
from .transport import ModbusReadError, PymodbusFactory


class DeviceState(str, Enum):
    DISABLED = "disabled"
    CONNECTING = "connecting"
    ONLINE = "online"
    DEGRADED = "degraded"
    OFFLINE = "offline"


@dataclass(frozen=True)
class PollStatus:
    device_id: str
    state: DeviceState
    reads_ok: int = 0
    read_errors: int = 0
    reconnects: int = 0
    last_error: str = ""
    last_rx_utc: datetime | None = None


class ModbusClientAdapter(ProtocolAdapter):
    """One instance owns many TCP IEDs OR one shared RTU port with many slaves.

    The samples queue is a bounded *latest-value notification* projection. If
    downstream is slower than producers it drops oldest notifications and records
    overflow. A mapping/runtime state store must consume or resnapshot latest;
    it must not use this lossy view for command/ordered event delivery.
    """

    def __init__(self, devices: Sequence[DeviceConfig], *, factory=None,
                 queue_capacity: int = 4096, retry_limit_seconds: float = 30.0):
        if not 16 <= queue_capacity <= 65_536:
            raise ValueError("Queue capacity must be in 16..65536")
        self._devices = tuple(devices)
        self._factory = factory or PymodbusFactory()
        self._queue: asyncio.Queue[SignalSample] = asyncio.Queue(maxsize=queue_capacity)
        self._retry_limit = max(1.0, min(retry_limit_seconds, 60.0))
        self._tasks: list[asyncio.Task] = []
        self._clients: list[object] = []
        self._statuses: dict[str, PollStatus] = {}
        self._latest: dict[str, SignalSample] = {}
        self._sequence: dict[str, int] = {}
        self._overruns = 0
        self._running = False
        self._instance: ProtocolInstance | None = None

    @property
    def protocol_id(self) -> str:
        return self._instance.protocol if self._instance else "modbus"

    @property
    def dropped_notifications(self):
        return self._overruns

    def device_statuses(self) -> tuple[PollStatus, ...]:
        return tuple(self._statuses.values())

    def latest_snapshot(self) -> dict[str, SignalSample]:
        """Snapshot for future IPC signal pagination; never fabricated good/zero."""
        return dict(self._latest)

    async def start(self, instance: ProtocolInstance,
                    signals: tuple[ProtocolSignal, ...]) -> None:
        if self._running:
            raise RuntimeError("Already started")
        if instance.role is not InstanceRole.CLIENT or instance.protocol not in ("modbus_tcp", "modbus_rtu"):
            raise ValueError("Requires Modbus TCP/RTU client instance")
        ids = set()
        for d in self._devices:
            d.validate(instance)
            if d.id in ids:
                raise ValueError("Duplicate stable connection IDs")
            ids.add(d.id)
        if instance.protocol == "modbus_rtu":
            validate_serial(instance)  # verify before spawning tasks
            if len({d.unit_id for d in self._devices if d.enabled}) != sum(d.enabled for d in self._devices):
                raise ValueError("Duplicate RTU slave ID on one physical bus")
        configured = []
        for signal in signals:
            if signal.instance_id != instance.id:
                raise ValueError("Signal belongs to different protocol instance")
            if signal.enabled:
                configured.append(parse_signal(signal, ids))
        plans = compile_reads(configured)
        by_device: dict[str, list[ReadRequest]] = defaultdict(list)
        for req in plans:
            by_device[req.device_id].append(req)
        self._instance = instance
        self._latest.clear()
        self._sequence.clear()
        self._overruns = 0
        while not self._queue.empty():
            self._queue.get_nowait()
        self._statuses = {d.id: PollStatus(d.id, DeviceState.CONNECTING if instance.enabled and d.enabled
                                          else DeviceState.DISABLED) for d in self._devices}
        self._running = True
        if not instance.enabled:
            return  # not a single TCP/serial connection attempt
        if instance.protocol == "modbus_tcp":
            for d in self._devices:
                if d.enabled:
                    self._tasks.append(asyncio.create_task(self._run_tcp(d, by_device.get(d.id, ())),
                                                           name=f"modbus-tcp:{d.id}"))
        else:
            enabled = tuple(d for d in self._devices if d.enabled)
            if enabled:
                self._tasks.append(asyncio.create_task(self._run_rtu(instance, enabled, by_device),
                                                       name=f"modbus-rtu:{instance.id}"))

    async def stop(self) -> None:
        self._running = False
        for task in self._tasks:
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()
        for client in self._clients:
            client.close()
        self._clients.clear()
        for did, status in tuple(self._statuses.items()):
            self._statuses[did] = PollStatus(did, DeviceState.DISABLED,
                                               status.reads_ok, status.read_errors,
                                               status.reconnects, status.last_error,
                                               status.last_rx_utc)

    async def samples(self) -> AsyncIterator[SignalSample]:
        """Bounded change stream for latest-state projection; cancel on shutdown."""
        while self._running or not self._queue.empty():
            try:
                yield await asyncio.wait_for(self._queue.get(), timeout=0.5)
            except asyncio.TimeoutError:
                continue

    async def publish(self, signal: ProtocolSignal, sample: SignalSample) -> None:
        raise PermissionError("Field Modbus writes blocked: no authorized control port")

    def _status(self, device: DeviceConfig, state: DeviceState, *,
                ok: int = 0, errors: int = 0, reconnect: int = 0, message: str = ""):
        old = self._statuses[device.id]
        self._statuses[device.id] = PollStatus(device.id, state,
            reads_ok=old.reads_ok + ok, read_errors=old.read_errors + errors,
            reconnects=old.reconnects + reconnect,
            last_error=message if message else ("" if state is DeviceState.ONLINE else old.last_error),
            last_rx_utc=datetime.now(timezone.utc) if ok else old.last_rx_utc)

    def _sample(self, point: ParsedSignal, value, quality: Quality):
        sid = point.signal.id
        seq = self._sequence.get(sid, 0)+1
        self._sequence[sid] = seq
        sample = SignalSample(sid, value, quality, SignalOrigin.PHYSICAL,
                              datetime.now(timezone.utc), seq)
        self._latest[sid] = sample
        if self._queue.full():
            try:
                self._queue.get_nowait()
                self._overruns += 1
            except asyncio.QueueEmpty:
                pass
        self._queue.put_nowait(sample)

    def _lost(self, requests: Sequence[ReadRequest]):
        # Emit a transition once; repeated connection retries must not flood
        # the bounded telemetry queue with identical unavailable samples.
        seen = set()
        for req in requests:
            for point in req.points:
                sid = point.signal.id
                if sid not in seen:
                    old = self._latest.get(sid)
                    if old is None or old.quality is not Quality.COMMUNICATION_LOST:
                        self._sample(point, None, Quality.COMMUNICATION_LOST)
                    seen.add(sid)

    async def _poll(self, device: DeviceConfig, client, requests: Sequence[ReadRequest]):
        for req in requests:
            try:
                # library timeout and outer deadline prevent indefinite socket stalls
                raw = await asyncio.wait_for(client.read(req, device.unit_id),
                                             timeout=device.timeout_ms/1000 + 0.25)
                invalid_count = 0
                for point in req.points:
                    try:
                        offset = point.address - req.address
                        value = decode_value(point, raw[offset:offset+point.width])
                    except (ValueError, OverflowError, TypeError):
                        self._sample(point, None, Quality.INVALID)
                        invalid_count += 1
                    else:
                        self._sample(point, value, Quality.GOOD)
                self._status(device, DeviceState.DEGRADED if invalid_count else DeviceState.ONLINE,
                             ok=1, errors=invalid_count,
                             message="DECODE_INVALID" if invalid_count else "")
            except ModbusReadError:
                for point in req.points:
                    self._sample(point, None, Quality.INVALID)
                self._status(device, DeviceState.DEGRADED, errors=1,
                             message=f"FC{req.function:02d}_MODBUS_EXCEPTION")
            except (asyncio.TimeoutError, OSError, ConnectionError) as exc:
                self._status(device, DeviceState.OFFLINE, errors=1,
                             message=f"TRANSPORT_{type(exc).__name__.upper()}")
                raise ConnectionError("Device link lost") from exc

    async def _backoff(self, attempt: int):
        delay = min(self._retry_limit, 0.5 * (2 ** min(attempt, 6)))
        await asyncio.sleep(delay * (0.8 + random.random()*0.4))

    async def _run_tcp(self, device: DeviceConfig, requests: Sequence[ReadRequest]):
        attempt = 0
        while self._running:
            client = None
            try:
                self._status(device, DeviceState.CONNECTING)
                client = self._factory.tcp(device)
                self._clients.append(client)
                await asyncio.wait_for(client.connect(), timeout=device.timeout_ms/1000 + 0.5)
                self._status(device, DeviceState.ONLINE, reconnect=int(attempt > 0))
                attempt = 0
                while self._running:
                    start = asyncio.get_running_loop().time()
                    await self._poll(device, client, requests)
                    await asyncio.sleep(max(0.025, device.poll_ms/1000 -
                                            (asyncio.get_running_loop().time() - start)))
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # isolate this IED; safe and bounded recovery
                self._status(device, DeviceState.OFFLINE, errors=1,
                             message=f"CONNECT_OR_POLL_{type(exc).__name__.upper()}")
                self._lost(requests)
                attempt += 1
                if self._running:
                    await self._backoff(attempt)
            finally:
                if client is not None:
                    client.close()
                    if client in self._clients:
                        self._clients.remove(client)

    async def _run_rtu(self, instance, devices, by_device):
        attempt = 0
        while self._running:
            client = None
            try:
                for d in devices:
                    self._status(d, DeviceState.CONNECTING)
                timeout = max(d.timeout_ms for d in devices)/1000
                client = self._factory.serial(instance, timeout)
                self._clients.append(client)
                await asyncio.wait_for(client.connect(), timeout=timeout + 0.5)
                for d in devices:
                    self._status(d, DeviceState.ONLINE, reconnect=int(attempt > 0))
                attempt = 0
                # One serial owner/task. Use each slave's configured scan period
                # and earliest due scheduling, never poll every device at the
                # fastest device interval. Traffic is strictly sequential.
                loop = asyncio.get_running_loop()
                due = {d.id: loop.time() for d in devices}
                while self._running:
                    now = loop.time()
                    ready = [d for d in devices if due[d.id] <= now]
                    if not ready:
                        await asyncio.sleep(min(0.5, max(0.005, min(due.values())-now)))
                        continue
                    # Stable round-robin tie break; no task per slave or signal.
                    for d in ready:
                        started = loop.time()
                        await self._poll(d, client, by_device.get(d.id, ()))
                        due[d.id] = max(started + d.poll_ms/1000, loop.time())
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                for d in devices:
                    self._status(d, DeviceState.OFFLINE, errors=1,
                                 message=f"SERIAL_{type(exc).__name__.upper()}")
                    self._lost(by_device.get(d.id, ()))
                attempt += 1
                if self._running:
                    await self._backoff(attempt)
            finally:
                if client is not None:
                    client.close()
                    if client in self._clients:
                        self._clients.remove(client)
