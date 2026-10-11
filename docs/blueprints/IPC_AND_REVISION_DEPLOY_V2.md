# Control plane, authenticated IPC and revision activation

**Status: PROPOSED implementation contract for issue #8; normative authority remains `docs/CONTRACTS.md`, `contracts/ipc.v1.schema.json` and ADR-0003.** New wire fields, authorization policy and service method signatures require ARCH review before they are treated as normative.

## 1. Fundamental separation

Studio can disappear. Runtime is authoritative only for its *active deployed snapshot*, connected sessions, latest signal state, simulation state and audit records. Engineering Store owns drafts, revision history, migrations and editing; Runtime must **not** query a mutable Studio connection, observe half-completed spreadsheet transactions or share a PySide6-owned SQLite handle.

Flow: Edit draft → Validate → freeze immutable digest/revision → compare deployed diff → Stage (non-live prepare) → explicit authorized Apply → atomically record active revision → continue or safe rollback. `Save` must never be aliased to `Deploy`. If no engineering UI is open, runtime continues operating from last committed revision.

## 2. Local IPC architecture

| Layer | Candidate implementation | Hard requirement |
| --- | --- | --- |
| Linux | UDS under OS-managed runtime directory; filesystem owner/group/mode restrictions | Verify peer credentials, per-method privileges, prevent symlink/race and stale socket takeover |
| Windows | ACL-restricted named pipe, or explicitly authenticated loopback with hardened access | User identity/ACL, non-admin read-only default, reject arbitrary local users for deploy |
| Transport framing | Length-prefixed JSON request/reply for v1 exploration | Maximum frame size, read deadline, strict UTF-8/schema, partial read handling |
| Version | existing `1.x` envelope and request correlation | Major mismatch fail closed; minor downgrade only if advertised capability says safe |
| Authentication | OS peer identity + defense-in-depth authenticated session | Studio launch not equivalent to admin authorization |
| Remote | NO remote admin listener by default | Future remote control requires separate ARCH/security review, TLS & access restrictions |
| Telemetry | subscribe with filtered instance/signal ranges and sequence cursor | Bounded subscribers, quota/backpressure, gaps + snapshot recovery |

Schema reminder: the existing `ipc.v1.schema.json` requires `api_version/message_id/kind/sent_at/correlation_id/payload` and forbids undeclared envelope keys. Auth/session, command names, idempotency key, expected revision and subscriptions are **proposed payload-level protocols** until validated with ARCH; do not claim they already exist on wire.

Use separate request (bounded timeout), long running operation (`operation_id` + progress/status), and telemetry subscriptions. Do not execute blocking validation, schema migration or compiler work inside the IPC listener loop.

## 3. Request boundaries and roles

Proposed logical methods (not current implemented wire APIs):

- `Health/GetCapabilities/GetInstances`: read-only status and supported command/protocol/OS features.
- `SignalPage/GetSnapshot`: immutable paginated snapshot with signal ID, value, quality, timestamps and sequence, filtered by protocol/IED/ASDU scope. Never return all 500k points in one JSON frame.
- `SubscribeTelemetry/Resume`: bounded/coalesced **UI** view with sequence/overrun counters and explicit gap → snapshot resync.
- `GetDiagnostics/GetMetrics`: IDs and stable error codes; secrets/IP credentials sanitized according to role.
- `ValidateRevision/StageRevision`: async operations, strict content hash and schema cap validation.
- `ApplyStagedRevision`: permission-gated, expected-active-revision CAS + idempotency key + explicit activation impact acknowledgement.
- `Rollback`: permission-gated, to selected signed/verified previous revision, reason/audit, compatibility checks.
- `Simulate/StopSimulation`: separate permission, target signal, sample origin/TTL/operator session and physical command block.
- `Service Restart/Stop`: **not** a general Studio action. OS service manager rights/admin policy must govern.

Roles (proposed): `Observer` (health/monitor), `Engineer` (edit draft/validate), `Deployer` (stage/apply/rollback), `SimulationOperator` (test injection) and `ServiceAdministrator` (OS service management). They need independent access policy; local desktop login alone gives no blanket permission to change live gateway.

## 4. Revision activation transaction and recovery

**Preflight and stage, no active side effects:**
1. Check authenticated caller, payload size, expected active revision and idempotency key.
2. Locate complete engineering revision and verify cryptographic digest, schema version, referenced protocol capability versions, ConnectionPoint/DataStation foreign keys (ARCH #13), native address scopes and role permissions.
3. Validate mapping topology (cycle/fan-in/control paths), type conversion and source→destination directions; reject at first unsafe actionable diagnostic.
4. Compile immutable dispatch graph; calculate impact plan for changed instances/peers/addresses/quality policies and whether each adapter can hot-swap or requires restart. **Do not** assume all changes can be applied without session loss.
5. Persist stage record and operator-reviewed diff **durably**, not a Python in-memory flag.

**Apply:**
6. Recheck expected active revision (CAS) and stage TTL/capabilities; append write-ahead activation journal with operation ID.
7. Prepare optional replacement resources where possible. Classify change as `hot-swappable`, `session-restart`, `service-restart` or `incompatible` with user-visible impact and approval.
8. Activate an all-or-nothing **logical revision pointer** only after required validation/readiness checks; if individual field sessions cannot be switched atomically, provide explicit outage windows/partial-failure policy and retain last verified recovery plan.
9. Durably record activated revision ID/digest + audit, retire old resources safely, publish revision-changed event (not dependent on GUI connected).
10. On failure, revert pointer/resources according to an explicit state diagram, mark operation failed and surface impact. Never treat partial stage as deployed success.

**Power-loss recovery:** journal records distinguish STAGED/ACTIVATING/COMMITTED/ROLLBACK states. After restart verify checksums and reconstruct last fully committed revision; **never** promote an uncommitted stage automatically. Simulation sessions must default to a safe cleared/expired state after runtime restart unless an explicit, reviewed persistence requirement exists.

## 5. Compatibility and concurrency hazards

- Bounded readers and a **single ordered writer** in the engineering repository; separate SQLite read handles, WAL, explicit migration. Don't expose writable engineering DB via network shares.
- Snapshot ID includes stable opaque IDs, contract version, content hash, created UTC, validated capability manifest, migration version; reconcile with approved `EngineeringRevision` model.
- Idempotency cache must be durable for high-impact requests and use payload digest + expiry. A repeat of the same key with **different** body rejects; a replay after a timed-out apply returns recorded status, not a second activation.
- Schema v1 unknown keys reject according to schema. Invalid/malformed frames, excessive recursion, arbitrary JSON size, missing correlation, unauthorized mutations and stale revision all have stable structured errors.
- If IPC disconnects mid-apply, the runtime **does not cancel nor repeat** a confirmed operation merely because Studio closed. Client reconnects and queries status by operation ID; policy for explicit cancellation must define the last cancellable phase.
- Server must record telemetry sequence per scoped subscription; on overflow send gap/coalesce counters and force snapshot sync. Every lost monitoring notification is not a lost protocol sample.
- Runtimes and Studios of incompatible major protocol/schema versions reject mutating requests, but health/upgrade guidance should remain available through a carefully defined read-only compatibility path.
- Store/engine version skew on upgrade is a **migration gate**, not an opportunity to wipe old configuration.

## 6. Security and IEC-specific semantics

- Bind administrative surfaces locally; no default remote UI/API on TCP/0.0.0.0. Configure device-facing Modbus/IEC104 listeners explicitly and separately from management IPC.
- Least-privilege service user; only authorized owners can modify active configs. Protect journal/snapshots, hashes, backup encryption where operationally mandated, log redaction, OS audit.
- Production operation must threat-model unauthorized physical command, control paths, replay, malicious device payloads and network segmentation per NIST SP 800-82 Rev.3. IEC-104 ordinary protocol transport is **not** assumed encrypted/authenticated by default; place field interfaces within a controlled OT zone.
- Master peer IP allowlist does not itself constitute strong administrative authentication. Keep IEC104 peer connection policy independent of CA/IOA local data ownership.
- Unexpected `COT`/TI/quality/timestamp is a protocol semantic error or documented mapping policy; do not fake device timestamps.

## 7. Acceptance evidence before enabling deploy

| Case | Evidence |
| --- | --- |
| GUI force-closed during 30-minute fake-adapter run | No protocol session reset triggered by GUI; sequence continuity |
| Wrong major API, malformed JSON, 16 MiB attempt | Fail closed within deadline; no unbounded memory; max frame **to be specified** |
| Low-privilege observer requests Apply/Simulation | Denied, audited, active revision unchanged |
| Two engineers race to deploy | Exactly one CAS success, loser sees conflict |
| Same idempotency key retried | Exactly one activation, replay yields prior outcome |
| Crash after STAGE and mid ACTIVATE | Last committed revision restored or safe stop with diagnostic |
| Address/graph invalid or missing peer | Stage rejected, no live side effects |
| Studio reconnect loses telemetry cursor | Gap reported, page snapshot rehydration |
| Runtime PID changes | Service recovery, state/revision/quality rehydrated; interruption honestly reported |

**Related:** `docs/blueprints/RUNTIME_SERVICE_V2.md`; `docs/quality/SERVICE_ACCEPTANCE_MATRIX.md`; implementation ownership `docs/implementation/SERVICE_DELIVERY_HANDOFF.md`.
