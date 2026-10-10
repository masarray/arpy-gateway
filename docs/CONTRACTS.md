# Canonical domain / interface contracts v1

Every implementation MUST align with the Python skeleton under `src/arpy_gateway/domain` and versioned machine-readable `contracts/`. Breaking changes require ADR, updated fixtures, migration plan and approval before parallel lanes merge.

## Identity and ownership
- `Project.id`: stable opaque ID (UUID in new projects).
- `ProtocolInstance.id`: stable project-scoped ID, has protocol, role, enabled, parameters; one protocol type may have many instances. Example: two independent Modbus TCP clients and two IEC-104 servers.
- `ProtocolSignal.id`: stable project-scoped ID, belongs to exactly one instance, independent of address/name. Signal family (`binary` / `analog` / `counter` / `command`), typed engineering value and native protocol metadata.
- `Mapping.id`: separate directed edge(s) from one `source` signal to one `destination` signal; fan-out via several records. A signal may participate in several mappings, without duplication. Never encode mapping in `ProtocolSignal`. Reverse traffic needs its own explicit edge and loop/command safety.
- `SignalSample`: live runtime value keyed by signal ID, with source timestamp, observed timestamp, quality, origin (`physical`/`simulated`), and monotonic source sequence.
- `EngineeringRevision`: validated immutable snapshot of all instances, signals, mappings, transforms and safety policy; runtime deployment is by revision ID/content digest only.

An address is unique only within a **specific adapter-defined scope** (e.g. IEC-104 server instance + Common Address + IOA; Modbus server instance + Unit ID + register table + native address). Protocol signal address is protocol-owned `parameters`, not an application-global integer. Duplicate addresses for intended aliases need explicit capability/policy and test; default is reject in addressable server namespaces.

## Protocol-instance settings
`ProtocolInstance` holds `protocol` (e.g. `modbus_tcp`, `modbus_rtu`, `iec104`), `role` (`client` / `server`; UI may also display master/slave), `enabled`, optional `display_name`, and typed/validated adapter-owned communication settings. Instance owns connections and lifecycle. A protocol type is not the same as an endpoint instance.

## Protocol Signals — adapter-specific, no universal monster table
A small universal domain: `id`, `instance_id`, `name`, `family`, `value_type`, `unit`, `enabled`, `direction`, `parameters`. Adapter metadata encodes Modbus register table, 0-based PDU address vs human label, Unit ID, uint/int widths, byte/word order, scale; or IEC-104 CA, IOA, ASDU/TI, cause/timestamp/counter/command options. Use a per-protocol field-schema/editor rather than showing irrelevant fields globally.

IEC-104 TI 35 = time-tagged measured scaled value, TI 45 = single command. Never make UI show TI 35 and 45 as interchangeable analog data types. Distinguish monitoring point from controls (select/execute, acknowledgment, SBO/execute model, command state).

## Mapping rules
- Mapping is typed `source_signal_id` → `destination_signal_id` and refers to existing signals in two instances. Same instance routing is allowed only if adapter declares that case, and never as an implicit identity/echo loop.
- A source may fan out to many destination signals. **Multiple writers into the same destination** require explicit arbitration/fan-in policy (otherwise reject).
- Direction is explicit: telemetry vs command. A command route is not an inverse of monitoring mapping.
- Transform chain is explicit: decode → normalize → scale/unit convert → quality propagation → destination encode; errors do not produce fabricated zero.
- On link loss/timeout mark output quality according to protocol policy; never treat last value as newly sampled.
- Mapping compiler validates graph complexity in O(V+E) for topological ordering/cycle detection (excluding explicit bounded feedback semantics implemented later). Stable IDs prevent changing target of edits under sort/filter.
- Simulation cannot automatically cross into an energized physical control path; simulate monitoring output only unless authenticated test policy explicitly allows isolated command test.

## Simulation
Simulation belongs logically to `Protocol Monitoring & Simulation`, but implementation is runtime-side:
- Per source signal explicit enable; modes manual, stepped, ramp, sine, scripted (later); no global override of physical inputs.
- Sample origin `simulated`, source quality policy and diagnostics must be explicit; do not assert all downstream protocols support the same simulated bit.
- Per-signal TTL, session ownership, rate cap, disconnect behavior and Stop All action.
- When a physical signal reappears, configured precedence must be explicit and visible; never merge two writers silently.
- Control/read-only segregation defaults safe; physical devices are never commanded merely because a simulation value changed.
- End-to-end acceptance: simulated source value → mapping translator → actual destination adapter → SCADA/client readback, measured separately from GUI visualization.

## IPC / messages
Versioned wire major `1`; every message requires `api_version`, `message_id`, `kind`, `sent_at`, `payload`. Requests requiring side effects also include auth/session, idempotency key and expected revision. Replies correlate by message ID. Telemetry has sequence per subscription; bounded queue with drop/coalesce counters and snapshot recovery. Introduce no undocumented network endpoint or silent auto-deploy.

## Error & diagnostics
Machine-stable codes; user-readable descriptions and copy-diagnostics payload. Never write passwords/keys/authorization tokens into logs. Include UTC timestamps, instance/signal/mapping IDs, phase, attempted operation and actionable recovery. Timeout, stale, invalid, blocked, test and simulated origin are distinct; map to protocol quality flags according to adapter capability.

## Authority and version policy
`contracts/project.v1.schema.json` is canonical wire-format shape; Python dataclasses are canonical in-process semantics. `contracts/ipc.v1.schema.json` defines minimum envelope. `docs/ADR` records migration approvals. Version 1 is a *foundation contract*, not wire-interop guarantee until separately exercised by real implementations.
