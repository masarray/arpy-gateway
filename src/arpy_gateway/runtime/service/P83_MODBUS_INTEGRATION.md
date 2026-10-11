# P8.3 — Headless Modbus Runtime integration

**Status: implemented P8.3 protocol-to-IPC integration seam with real TCP wire CI.** Not a shipped installer, native Windows/Linux service, connected PySide6 GUI or persisted/deployed gateway. PR #15 Modbus source stays in its own ownership lane and PR #18 headless IPC remains a draft prerequisite.

## Technical baseline and branch isolation

- This branch builds **on PR #18 head `a3bafc387df685b303baa93bffaa7c2aeaed36ba`**, not on the older GUI demo. Review this as a *stacked* PR targeting `feat/8-headless-ipc-lifecycle`; do not merge it directly into main before base contracts and security decisions are approved.
- The Modbus adapter exists on **draft PR #15** at pinned commit `cb94de62d49bdb3c3151f7cd472311911442f241`. To prove integration without altering another lane's files, GitHub Actions fetches that precise revision and temporarily overlays **only** `src/arpy_gateway/adapters/modbus` into the CI working tree. It does not commit/cherry-pick/merge that tree into this PR.
- Python 3.12 × Windows/Linux uses `pymodbus[serial]==3.15.0` **on CI builders only**. End-user installers must bundle reviewed and pinned protocol transports offline (see PR #15 Bundling Contract), not run pip or download components while gateway operates.
- This PR changes only `ipc/`, `runtime/service/`, relevant `tests/ipc/` and the IPC workflow. No modifications to domain, accepted schemas, Studio widgets or Modbus adapter implementation.

## Runtime structure

```text
                   optional PySide6 Studio (future GUI lane)
                        |        ^
                read-only versioned IPC
                        |        |
arpy-gatewayd       LocalReadOnlyServer
 independent process     |   signals.page / signals.delta / devices.page
                        |
                   AdapterRuntime (latest indexed cache + quality)
                        ^
             canonical ProtocolAdapter
                        ^
                  BoundModbusAdapter
             +----------+-----------+
             | Modbus TCP Client    | Modbus RTU Client
             | per TCP IED          | single serial-port owner
             +----------+-----------+
                actual external field Modbus devices
```

- `BoundModbusAdapter` adapts `ModbusClientAdapter` (which reports a generic Modbus family until started) to a **specific** `modbus_tcp` / `modbus_rtu` canonical `ProtocolAdapter.protocol_id`. It checks the bound instance before every start. Importing the fixture runtime does not pull PyModbus/serial into a Qt-less runtime.
- `build_modbus_runtime(instance, devices, signals)` composes an *in-memory validated plan* passed by a trusted caller. This is **not** mutable draft loading, auto-deploy, a JSON configuration file for customers, or a replacement for approved Store #3 / ARCH #13. No implicit live write permission; `publish()` delegates to the existing Modbus adapter's read-only control policy.
- `serve_runtime(runtime, token, stop, ...)` is shared by the fixture entrypoint and injected live adapter runtime. Service loop lifetime does not depend on IPC clients being connected. Existing fixture `__main__` remains developer-only; there is NO new end-user CLI requirement.
- IPC `devices.page(offset,limit)` is available when runtime supports it, with a strict 128-row bound. Returns stable **Device ID**, state, reads_ok/read_errors/reconnects, last sanitized reason and last real RX UTC. Does **not** treat TCP peer IP or Modbus Unit ID as a stable record key. `health` reports dropped monitoring notifications; `signals.page` / `signals.delta` are retained for live values/quality/timestamps.
- The IPC channel is a **loss-tolerant Value Viewer projection**, not a protocol event-delivery bus. TCP/RTU socket ownership and polling remain entirely within the headless engine, never PySide6.

## Multi-instance fleet supervisor

`RuntimeFleet` combines **1–64 independent, injected `AdapterRuntime` instances** into one read-only IPC endpoint. The fleet does not reassign Modbus addresses, change device owners, or become a scheduler; each TCP peer group/RTU bus continues to own its own socket and retry policy.

- Precomputes a compact prefix-offset index for up to **500,000 configured signal IDs** across instances. `signals.page(offset,limit)` uses binary search + bounded slices, not materialization of every signal/Qt widget.
- Accepted child sample changes feed one bounded fleet `ViewportChanges` monitor ring. Client subscriptions receive coalesced **latest values** only; global link quality loss invalidates old cursor in O(1) and requires snapshot resync, without iterating all 500k signal values.
- `health` contains bounded per-instance status/error counters. `devices.page` exposes stable IED/slave IDs, including `instance_id` so identical display names/Unit IDs cannot accidentally be conflated across different serial/TCP buses.
- Independent startup via per-instance timeouts: one failed bus reports **DEGRADED** while other healthy instances continue polling; if all fail to initialize, the caller receives a startup failure rather than a false healthy service.
- Cancellation during partial adapter startup triggers bounded resource cleanup; shutdown tears down each instance independently and surfaces any cleanup failures.
- **This is monitoring and lifecycle orchestration only**, not high availability/redundancy, transactional Deploy, protocol-output event queues, or automatic packet failover.

New `tests/ipc/test_runtime_fleet.py` covers mixed TCP/RTU supervisor identity, cross-instance virtual pagination/delta, per-device status, a failed instance being isolated, and quality invalidation on unexpected stream loss. The test uses protocol-port fakes; separately, `test_modbus_runtime_integration.py` proves the real TCP wire path through one service instance.

## Tested operational behavior

- A separate real TCP slave responds with Modbus MBAP/PDU frames for FC03 (IEEE Float32 = 123.0) and FC01 (coil true); independent PyModbus client reads via the ARPY transport adapter, publishes typed samples through `AdapterRuntime`, then IPC returns **Good / actual value / timestamp**.
- Another remote IED port is unreachable; the healthy TCP IED continues polling, the other IED remains unavailable/communication-lost. `devices.page` reports per-device health/retry and does not invent Good.
- Changing TCP slave register value to 226.25 becomes a `signals.delta` update with the real new value; no GUI polling of 500k objects.
- **Process isolation:** source TCP slave and Gateway Runtime are separate OS processes. A client attaches, disconnects, and attaches again; the gateway process and wire poll count continue.
- RTU bridge contract tested with injected fake port on Windows/Linux without serial hardware; physical RS-485 framing, bus timing/reconnect and RTU Server are **not verified**.
- Baseline IPC tests include malformed frame, auth, overflow/generation resync, client kill/exit, bounded admissions and read-only mutation denial.

CI suite separation is deliberate: clean-interpreter headless CI verifies runtime imports without PyModbus/Qt, while optional Modbus interop tests intentionally load PyModbus. Cross-lane tests must not falsely assert the library is absent from `sys.modules` after an integration test has imported it.

## P8.3 delivery status / blockers

| Requirement | Status | Release gate |
| --- | --- | --- |
| Modbus TCP real-wire → runtime → IPC values | **Implemented, CI validated** | Physical IED and independent external interoperability testing pending |
| Multiple TCP IED fault isolation | **CI validated** | field timeout/reconnect/soak test pending |
| RTU identity, device-page bridge | **Unit tested with fake serial** | physical RS-485, OS COM/TTY and hardware required |
| Service independent of client lifecycle | **OS subprocess tested** | actual SCM/systemd logout, boot and restart not yet |
| 500k virtual viewport delta | **Bounded by design** | measured capacity/latency and memory on target hardware pending |
| Save/Validate/Stage/Deploy and rollback | **BLOCKED: Store #3 + ARCH #13** | approved immutable revision, durable journal, CAS and safety audit |
| PySide6 Add Device / realtime Value Viewer | **BLOCKED: GUI #4 integration** | installable Windows/Linux Studio + secure IPC |
| No-terminal self-contained installer | **BLOCKED: packaging #8/#10** | native signed/ACL service, offline bundled PyModbus/PySerial |
| Active/standby redundancy | **Future RFC #16** | fencing/quorum and split-brain safety |

**Do not merge the stacked PR or declare gateway usable by operators** until platform security and release gates are resolved. Modbus client processing must never be coupled to Studio exit or to a user login session.
