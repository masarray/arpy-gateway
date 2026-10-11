# ADR-0004 (PROPOSAL): Explicit CommunicationPoint and DataStation in project v2

**Status: PROPOSED, issue #13 — NOT accepted.** The existing canonical project v1 and deployed revision semantics remain unchanged. This PR is an executable architecture proposal requiring ARCH/STORE/MODBUS/IEC104/IPC/GUI review before merge.

## Decision proposed

`ProtocolInstance` owns protocol/role and shared bus or listener settings. An instance owns zero or more `CommunicationPoint` nodes, each with stable project-scoped ID, `kind`, `enabled`, protocol-specific `settings`. A Modbus TCP Master peer (`remote_device`) owns its host/port/unit_id; Modbus RTU Master peers own individual slave Unit IDs while the instance alone owns COM/TTY/baud/parity. IEC-104 Server peers (`authorized_master`) are **access policy remote master IPs**, never owners of the station's CA/IOA.

`DataStation` is a separate stable-ID object owned by instance, with `common_address` in its IEC-104 Server-specific metadata. `SignalBinding(signal_id, communication_point_id, data_station_id)` provides explicit single-scope attachment to existing v1 `ProtocolSignal` identities. The v2 wire codec publishes bindings inline on each signal. No renumbering of stable ID on rename, address edit, ordering or GUI filtering; mapping remains a separate directed stable-ID edge.

The v2 proposal wraps the canonical v1 immutable DTOs additively (`EngineeringProjectV2(base, points, stations, bindings)`), preserving current Python v1 codec and schema unchanged. Strict new `contracts/project.v2.schema.json` and `encode_project_v2/decode_project_v2` fail closed on unknown fields and unsupported wire version.

## Validation and safety gates

O(I + peers + stations + signals + mapping graph) validation checks: unique instance-scoped Modbus TCP host/port/unit and RTU bus unit, TCP/serial ownership, bounded TCP port/Unit ID/poll settings, IEC-104 station CA and direction-scoped IOA uniqueness, allowed master IP addresses independently of CA/IOA, dangling/wrong-instance/disabled bindings, commands/mappings via existing v1 cross-protocol validator. Signal parameters must never invent an IED identifier from TCP endpoint, signal address, GUI row number, or IED display name.

**Non-destructive migration:** v1→v2 produces an editable **DRAFT ONLY**, preserving all existing instances, signals, mappings, metadata, IDs, and v1 schema semantics. It does **NOT** choose connection owners, data stations or SCADA IP permissions automatically. Enabled Modbus Master/IEC-104 Server points stay blocked from staging until an engineer explicitly binds each scope. An old v1 deployed runtime revision must continue to operate unchanged while the draft is edited, validated and staged.

## Coordination / merge gates

1. ARCH issue #13 formal approval of names, protocol capability, settings profiles, v2 wire schema and migration constraints. Current PR is draft, no accepted v1 files modified.
2. STORE #3 follow-up (not part of ARCH PR): SQLite schema **v2** with `communication_points`, `data_stations`, `signal_bindings`, FK on project+instance, indices, explicit upgrade from SQLite user_version 1, staged snapshot hashes, undo/redo and 500k keyset.
3. MODBUS #6 binds approved CommunicationPoint directly to existing `DeviceConfig`; IEC104 #7 scopes CA station independent of master sessions.
4. IPC #8 must accept only immutable, authorized, versioned validated deployment with CAS/audit/LKG; GUI #4 must Save Draft via Store worker, never access runtime sockets directly.
5. Windows-first unit/integration CI validates two Modbus TCP peers in one instance, RTU shared bus / different slaves, two allowed SCADA masters independent of CA/IOA and mapping fanout, old v1 codec unchanged. Linux/release/hardware follow after Windows acceptance.

**No automatic migration of active runtime, no automatic live Deploy and no unsupported IEC104 physical command capability are implied by this proposal.**
