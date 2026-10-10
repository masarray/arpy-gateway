# ARPY architecture blueprint v1

**Status:** normative contract baseline; implementation skeleton only. Intent: modular cross-platform industrial protocol gateway, engineered with Python and optionally controlled by PySide6 Studio. References: SICAM PAS/PQS V8.24 and SICAM Device Manager V26.20 manuals provided by product owner for UX inspiration; no Siemens code copied.

## Process boundary

```text
  ARPY Gateway Studio (PySide6, optional; can exit at any time)
   ├─ Gateway Config workspace
   ├─ Protocol Config workspace
   ├─ Protocol Signals workspace (one schema-backed editor per protocol)
   ├─ Protocol Mapping workspace
   ├─ Protocol Monitoring workspace
   └─ Value Simulation workspace (integrated with monitoring)
      │
      ├─ Worker coordinator: bounded read, ordered write, cancellable validation
      ├─ Engineering Store (SQLite WAL, versioned schema, revision control)
      └─ IPC client (versioned control plane + throttled telemetry subscriptions)
                   ║ strictly versioned local boundary
  Gateway Runtime Service (headless Python process; independently restartable)
   ├─ Config snapshot verifier + atomic revision activator/rollback
   ├─ Device/Protocol Instance Supervisor (adapter lifecycle & backoff)
   ├─ Protocol Adapter Registry (Modbus / IEC-104; extendable plugins)
   ├─ Typed Signal State Store (latest sample + bounded event feed)
   ├─ Mapping Compiler -> immutable dispatch graph
   ├─ Translator pipeline (quality / types / scaling / timestamps)
   ├─ Simulation Source (non-physical and clearly marked)
   └─ Observability (structured diagnostics, bounded history, metrics)
```

**Do not run runtime in Qt event loop, QThread, GUI subprocess lifetime or GUI's SQLite connection.** Dedicated runtime should have an OS service/service-manager adapter. Studio's local database is engineering-only; deployed runtime reads *immutable validated snapshot*, never live in-progress edits.

## Data plane (no GUI dependencies)

1. Protocol adapter receives raw frame and decodes using protocol semantics (no whole-frame forwarding).
2. Decoder assigns stable `instance_id` + `signal_id`, domain `SignalSample`, source/device timestamp and quality. Unavailable/invalid never invented.
3. Signal state store atomically updates last sample (sequence per source); bounded change queue notifies compiled mapping edges.
4. Mapping compiler resolves source/destination identities and pre-validates graph, data-type compatibility, direction, fan-in, loops, command policies, transforms and address scope.
5. Translator applies explicit conversions, engineering scaling and destination quality policy. Fan-out uses zero-copy references to immutable snapshots where safe.
6. Adapter publishes to IEC-104/Modbus/other downstream with proper application-layer semantics (GI, spontaneous, polling snapshot, write-confirmation, etc). No claim of report/interoperability until tested.
7. Connection-down triggers explicit stale/invalid quality transition and diagnostic; never quietly publishes last-good as fresh.

## Control plane (Studio ⇄ Runtime)

Versioned request/reply and event-subscription API (`docs/CONTRACTS.md`, `contracts/ipc.v1.schema.json`). A control-plane client must perform compatibility handshake, authentication/authorization before modification, and backpressure on telemetry subscriptions.

- Read commands: health, capabilities, instances, paginated signal snapshots, diagnostics/metrics.
- Write commands: validate revision, request deploy, rollback, enable/disable instance, enable/disable simulation.
- Dangerous commands MUST carry explicit authorization, target/expiry and audit trail.
- Idempotency key for mutation retries; optimistic current-revision check prevents overwriting changes.
- Async operation IDs for long-running deploy/validation; never block IPC event loop.
- Studio can attach/detach; runtime state persists independently.

JSON over framed local IPC is a **proposed wire default**, not deployed yet. Transport on Windows may use authenticated loopback or named pipes; Linux/macOS Unix sockets or authenticated loopback. Separate implementation spike must fix security, framing, size and protocol schema before shipping. No unauthenticated internet-exposed administrative API.

## Engineering state machine

`Draft` (may be incomplete; autocopy, edit, undo/redo, save allowed) → `Validated` (deterministic checks + immutable hashed snapshot) → `Staged` (compile + dry-run adapter readiness) → `Deployed` (atomic revision promotion) or `Rejected` (still old deployed revision). Rollback swaps back known-good snapshot. Every state transition audited. Editing `Draft` has no effect on Runtime until promotion.

## Technology strategy

- Python stdlib dataclasses/typing in domain, no platform imports.
- Adapter protocol stacks evaluated separately; do not tie architecture to PyModbus / Hat Drivers API shape.
- SQLite WAL for engineering; one ordered writer + separate pooled readers. Migration must be explicit/reversible where possible, never silently destructive.
- Runtime `asyncio` for network I/O and per-instance actors. Offload CPU/blocking parsing via bounded worker pool only where benchmark proves needed; serial links are serialized per adapter.
- Shared-memory high-volume transport may be added *after* profiling; default is decimated/paginated telemetry snapshot API.
- PySide6 `QTableView` / `QAbstractTableModel`, page queries & LRU; no widgets per signal.
- Tests and deterministic simulator are mandatory before field device connectivity.

## Package structure

```text
src/arpy_gateway/
  domain/           immutable public entities + validation [architecture-owned]
  contracts/        serialization/versioning [architecture-owned]
  engineering/      SQLite project store, migrations, revisions [store lane]
  runtime/          headless asyncio supervisor, services, mapping [runtime lane]
  adapters/         modbus/, iec104/, later plugins [per-adapter lanes]
  ipc/              Studio-runtime transport [IPC lane]
  studio/           PySide6 shell and six distinct workspaces [GUI lane]
tests/              contract/unit/worker/integration/performance layers
examples/           deterministic offline sample engineering projects
docs/               decisions + contracts + handoffs
```

Existing `studio/store.py` in draft PR #1 is a throwaway spike until migrated into engineering repository. Never use its flat source→destination columns as final schema.

## Explicitly out of scope in foundation PR
Functional Modbus or IEC-104 adapter, field controls, connected SCADA, operational simulation, security-complete IPC server, live Qt workspace shell, hardware performance guarantees.
