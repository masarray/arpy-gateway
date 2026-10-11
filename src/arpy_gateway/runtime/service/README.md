# Headless gateway runtime: issue #8 phase 1

**Status:** genuine independent Python process, but using a deterministic **FAKE adapter**, not Modbus/IEC104, installed OS service, or a production authentication system. Nothing in this milestone constitutes field communication, a PySide6 Value Viewer, a deployment service or a persistent engineering database. The code is confined to the IPC lane.

## Implemented

- Standalone `python -m arpy_gateway.runtime.service` process does **not** import Qt or any protocol adapter; its lifecycle is not owned by a GUI.
- `FixtureRuntime` publishes immutable `SignalSample` objects with source `DETERMINISTIC_FIXTURE_ONLY` and bounded sample count, stable IDs, per-signal sequence and UTC observation time. Never label it physical.
- Local read-only request/reply IPC binds **127.0.0.1 only**. One request per connection; authentication on every request; strict v1 envelope; bounded 64-KiB length-prefixed JSON and deadlines; 16 simultaneous client admissions.
- Methods: `hello`, `health`, `signals.page` (maximum 128 rows), `diagnostics`. All writes, configuration updates, commands, simulation and Deploy/Apply are **explicitly rejected**. This is not pretending to support those operations.
- Developer/test-only `ARPY_IPC_TEST_TOKEN` 32+ characters is required. Without it startup fails before sockets open. Credential via environment is **NOT** sufficient as a production OS service secret mechanism.
- Client requests are isolated and bounded. Closing the client TCP socket, crashing it or restarting another client cannot call runtime shutdown, close adapter sessions or clear live samples.
- CI uses separate daemon and separate client subprocesses on Windows/Linux to establish that source counters continue increasing with no GUI open.

## Developer-only usage / not for ARPY customers

In a source checkout, set `PYTHONPATH=src` and a throwaway `ARPY_IPC_TEST_TOKEN` (minimum 32 characters), then run `python -m arpy_gateway.runtime.service --port 0`. The daemon emits a one-line JSON readiness record with the loopback port and no credential. The CI/client harness discovers that ephemeral port for tests; the production Studio must perform secure discovery automatically. Users must not run Python, pip, PowerShell or JSON probes.

Run `PYTHONPATH=src python -m unittest discover -s tests/ipc -v` to test with stdlib only.

## Protocol adapter integration seam

`AdapterRuntime` is a GUI-free supervisor that accepts an existing `ProtocolAdapter` through dependency injection. It uses the approved `ProtocolInstance`, `ProtocolSignal` and `SignalSample` types, maintains an indexed latest-only value cache, rejects unknown source IDs and stale sequence numbers, and provides the same bounded `health()/page()` projection that local IPC uses. This is the integration seam for the independently reviewed Modbus PR #15 after schema/revision gating.

On unexpected sample stream termination, health becomes `DEGRADED`. **An O(1) failure flag invalidates published page values lazily**: no synchronous rewrite of 500,000 records in the asyncio event loop, no fabricated zero, and existing source timestamps remain distinguishable. The IPC view is read-only/loss-tolerant; it **must not** become the sole transport for required ordered protocol publications. Current supervisor is one injected adapter instance and does not implement auto-restart, persisted revisions or protocol-specific connector diagnostics.

Automated `tests/ipc/test_adapter_runtime.py` verifies typed sample ingestion through the official adapter port, sequence/order checks, wrong instance/family preflight, stream loss, actual IPC value pages and stop cleanup.

## Required before production

1. **IPC #8 security:** Windows ACL-restricted named pipe or reviewed same-machine authentication; Linux UDS peer identity and socket permissions; no developer env-token scheme, full roles, audit, key protection and replay controls.
2. **SCM/systemd service:** native OS-managed service account and startup/recovery, first boot unconfigured, no desktop-session dependency; test reboot/logout and stop/restart. A Python process used in tests is not an installed OS service.
3. **STORE #3 / ARCH #13:** immutable validated revision with connection/station identities; Save Draft independent of Deploy; journal + revision CAS + idempotency + rollback.
4. **MODBUS #6 PR #15:** real device adapters are separately owned and not merged into this IPC branch. Runtime integration must use approved ports, not private APIs; bundled dependency check, real TCP/RTU field tests and status/quality.
5. **GUI #4 PR #12:** Studio displays runtime health, connection state and paged real values over IPC only; no Qt-to-adapter import.
6. **QA #10:** strict load/backpressure, malformed-frame and privilege tests; OS service independence (C2), external wire/RS485 interoperability (C3), real field testing (C4).

The follow-up milestones are IPC peer security → revisioned store/deploy → Modbus connection import through approved adapter registry → IPC realtime value viewer → native Windows/Linux packaging. **Do not merge on this C1 lifecycle result alone.**
