# Headless Gateway Runtime — implementation blueprint v2

**Status: PROPOSED implementation guidance, not a new normative contract.**  
**Baseline:** `AGENTS.md`, `docs/ARCHITECTURE.md`, `docs/CONTRACTS.md`, accepted ADR-0001/0002/0003 on `main`.  
**Owners:** Runtime service + IPC issue #8; protocol adapters #6/#7; Mapping #5; Store #3; Simulation #9; QA #10.  
**Invariant:** GUI exit, crash, logout, and absence **MUST NOT** end protocol sessions. The runtime may operate with zero Studio clients.

## 1. Deployment and process boundary

```text
OS SERVICE MANAGER (Windows SCM / Linux systemd)
    |
    v
arpy-gatewayd  [one independently installed headless service process]
    +-- Startup preflight / platform service bridge
    +-- Revision verifier, active snapshot loader, durable deployment journal
    +-- Runtime supervisor (per-instance asyncio tasks; exception isolation)
    +-- Adapter registry + connection supervisors
    |     +-- Modbus TCP Client/Server, RTU Master/Slave
    |     +-- IEC-104 Client/Server (separate session/CA semantics)
    +-- Immutable compiled mapping graph / translator
    +-- Latest-sample state store; bounded event/change queues
    +-- Simulation source + command safety boundary
    +-- Local IPC server (health, revisions, diagnostics, subscriptions)
    +-- Metrics, structured audit, log/event limits

arpy-studio   [optional user desktop process; can attach/detach]
    +-- PySide6/QAbstractItemModel + bounded GUI workers
    +-- Engineering project draft + revisions [Store lane]
    +-- IPC client only; no adapter sockets

arpyctl       [proposed, minimal diagnostic/administrative CLI client]
    +-- Calls same authenticated local API; never imports Qt or field adapters
```

- **Not** a GUI-spawned `subprocess.Popen` child, `QProcess`, `QThread`, tray application, shell login session, webview, or Qt event loop.
- Runtime is started/stopped by the OS service manager. Optional foreground `arpy-gatewayd --foreground` is for dev/test only; it must execute **the same runtime composition root**, not a separate implementation.
- One normal service process initially; supervise protocol instances as independent async task groups/actors. Split a particularly risky adapter into a separate process **only if** fault isolation/profile warrants it, with an explicit IPC contract and resource budget.
- OS-managed automatic start, process recovery, watchdog and least privilege are platform features, not responsibilities of PySide6.

## 2. Operational lifecycle and states

Proposed service state machine:

```text
STOPPED --OS starts--> STARTING
STARTING --verify files/credentials/snapshot--> INITIALIZING
INITIALIZING --start local IPC + adapter supervisors--> RUNNING
RUNNING --some IEDs offline--> DEGRADED [other healthy instances stay alive]
RUNNING/DEGRADED --reload requested--> STAGING --> ACTIVATING --> RUNNING/DEGRADED
STAGING/ACTIVATING --error--> ROLLING_BACK --> last known good or DEGRADED
RUNNING/DEGRADED --OS stop--> STOPPING --> STOPPED
unhandled process exception --> OS restart policy; never Studio restart policy
```

Define *separate* health concepts:
- `process_alive`: service process/IPC responsive;
- `runtime_ready`: verified active revision loaded, managers initialized;
- `instances[]`: `CONNECTING/ONLINE/DEGRADED/OFFLINE/DISABLED/ERROR` with reason code, last transition, reconnect attempt, last RX/TX;
- `data_freshness`: good/uncertain/invalid/stale according to protocol semantics;
- `deployed_revision` and `requested_revision`, explicit transition outcome.

**Never equate "service process running" with "all IEDs connected" or "values Good".** If no valid active revision on first boot, service should stay in safe `UNCONFIGURED` state exposing local health/diagnostics, **not** open guessed devices or activate draft. If a previously active snapshot is corrupt, do not fall back silently to an arbitrary draft or enable control. Stop output activation, raise persistent actionable alarm, support deliberate verified recovery.

## 3. Composition root / dependencies

`src/arpy_gateway/runtime/service/` (IPC lane #8) is proposed composition/root entrypoint; `src/arpy_gateway/runtime/` and `runtime/mapping/` are runtime/mapping lane code; `src/arpy_gateway/adapters/{modbus,iec104}/` are protocol lanes. Resolve all service dependencies explicitly; avoid circular imports and importing Studio anywhere in runtime package.

Proposed (interface descriptions, **not** approved Python signatures):
- `RevisionRepository`: read *validated, content-hashed, immutable* snapshot and last-known-good; audit/recovery journal, versioned migration gate.
- `InstanceSupervisor`: start/stop/reconfigure independent protocol instance, bounded deadlines; correct per-session state and failure reason.
- `SignalStatePort`: latest sample by immutable signal ID and sequence; bounded change-event bus.
- `MappingPlan`: immutable compiled graph with validated direction, fanout, transforms, quality and address scopes.
- `RuntimeControlAPI`: authenticated version negotiation, health/diagnostics, subscription snapshots, revision staging/deploy/rollback.
- `MetricsPort`: bounded cardinality process/protocol metrics, redacted logs and structured audit.

**RFC gate:** if proposed interfaces require changes to `domain/`, `contracts/` or `contracts/ipc.v1.schema.json`, stop and coordinate ARCH #11/#13; do not extend IPC payloads silently.

## 4. Event and data path

1. Adapter reads decoded protocol data; per-instance task owns socket/serial handle, deadline, reconnect loop.
2. Resolve stable `signal_id` from configured connection/station and native address; no label/address-as-ID; receive decoded value, quality, source timestamp if provided, receive wall-clock and monotonic age context, strictly increasing per-source sequence.
3. Normalize typed sample. Preserve uncertain/invalid/time-tag semantics, **never synthesize "Good", numeric zero or fresh timestamps on loss**.
4. Update latest-sample store **atomically** and enqueue a bounded change/event notification to the mapping scheduler.
5. Translation uses precompiled graph and policy (signedness/scale/unit/direction/COT-where-applicable); reject loops, invalid fan-in and unauthorized commands before activation.
6. Destination adapter serves correct protocol behavior: Modbus Server reads a consistent register snapshot; IEC-104 Server supports GI/interrogation snapshots and spontaneous/report queue with explicit COT/quality/time rules.
7. Monitor IPC is a separate **loss-tolerant projection**: viewport filtered, 5–10 GUI updates/s by default, coalesce latest values with overflow counters and snapshot recovery.

**Critical:** never use a lossy GUI coalescing queue as the sole source for protocol delivery. Distinguish (a) latest-value cache, (b) bounded protocol-required events and confirmations, (c) diagnostic/event retention, (d) best-effort UI telemetry. Backpressure/overflow semantics must be specified *per lane*. Never silently drop commands, acknowledgements or protocol events whose ordering is required: fail closed/mark degraded/resynchronize, and record counters.

## 5. Scheduling and bounded concurrency

- Async TCP I/O via `asyncio`; one isolated task group per active protocol instance plus well-defined child sessions.
- Modbus Master polls **per IED** and groups only contiguous registers with compatible Unit ID, function/register table, datatype boundaries, maximum PDU quantities, poll priority and scan intervals. Never one timer/thread per signal. RTU is **half-duplex serialized per physical port** with inter-frame timing and its own reconnect state.
- IEC-104 maintains per-peer sessions, timers, backpressure and ASDU queues; IEC-104 Server local CA/IOA station ownership is **not** ownership by a connected SCADA master IP.
- Per instance queue caps, outstanding transaction caps, connection limits, retry budgets, bounded exponential backoff with jitter, disable/start cancellation and socket cleanup.
- CPU-bound parsing or compression enters a **bounded process/thread worker** only after profiler and GIL/thread-safety measurement. Do not offload tiny operations to thousands of tasks.
- Bounded in-memory signal latest cache (one latest record per subscribed/configured signal as needed), bounded history ring and page-based snapshots. Large signal capacity is separate from live update rate.
- On overload prioritize protocol semantics, enforce queue admission, expose skipped/coalesced/drop metrics; reject unauthenticated/misbehaving subscriptions before they consume runtime resources.
- On engine shutdown issue OS grace period; safely stop accepting control/deploy, release sessions and ports deterministically; power-loss is **not** graceful shutdown, so rely on durable atomic state and replayable journal.

## 6. State continuity and fault recovery

| Failure | Runtime must do | Must not do |
| --- | --- | --- |
| Studio closes/logs out | Continue protocol sessions unchanged | Stop adapters or network sockets |
| Studio crashes/restarts | Reauthenticate, fresh snapshot + sequence cursor | Reset live values to fake zero |
| One IED TCP times out | Isolate that IED; mark affected signals stale/invalid; retry with jitter | Stall unrelated IEDs |
| IEC-104 peer disconnects | Clean peer session; per-peer reconnect/health | Delete local CA signals |
| Serial port detached | Invalidate affected RTU devices; close/reopen after bounded retry | Busy-spin or retain invalid file handle |
| Runtime process crashes | OS policy restarts service and verified last-good revision | Auto-apply last unsaved draft |
| Power fails during activation | Journal recovery selects last fully committed revision | Open partially staged endpoints as active |
| Dependency/schema mismatch | Reject incompatible config/IPC operation visibly | Silent schema downgrade |
| Link restored | Resync protocol-specific state (GI, poll read etc.) and quality | Claim uninterrupted delivery history |

A service restart **can** interrupt protocol sessions; the safety contract is *GUI lifecycle independence* and controlled recovery, **not** a promise of zero downtime under OS/runtime failure. Hot apply changes are classified by impact; any required socket restart must be shown to operator before deploy.

## 7. Capacity, observability and release gates

Reference budget targets are in `docs/PERFORMANCE.md` and must be benchmarked on named hardware. Record `python/qt/OS/CPU/RAM/adapter` versions, p50/p95/p99 ingest-to-publish latency, poll jitter, mapped updates/s, queue depth/high-water, stale transitions, RX/TX/errors, reconnect count, dropped/coalesced monitor updates, RSS/fd/handle count, boot/recovery duration.

Minimum demonstrations before claiming working gateway:
1. Headless service starts on Windows + Linux *without launching Studio*.
2. Fake adapter traffic persists unchanged across Studio close, force-kill, logout and Studio restart.
3. Independent Modbus client/server and IEC-104 client/server interoperability with packet/frame captures, quality and command safety.
4. End-to-end physical/simulated Modbus source → typed mapper → **real adapter output** → independent IEC-104 SCADA client readback.
5. Power-loss/deploy failure recovery proves last-known-good revision and no partial activation; restart/reconnect clearly reported.

**See:** `docs/blueprints/IPC_AND_REVISION_DEPLOY_V2.md`, `docs/blueprints/PLATFORM_PACKAGING_V2.md`, `docs/quality/SERVICE_ACCEPTANCE_MATRIX.md`.
