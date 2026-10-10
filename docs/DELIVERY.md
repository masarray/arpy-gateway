# Parallel thread delivery plan / ownership contract

**Prerequisite:** merge foundation contract into `main` only after CI passes. Every implementation thread starts fresh from that protected baseline and uses a dedicated `feat/<lane>-<topic>` branch. No overlapping edits to normative contract without RFC.

| Lane | Owns | First deliverable | Must not edit |
| --- | --- | --- | --- |
| ARCH | `domain/`, `contracts/`, `docs/CONTRACTS.md`, `docs/ADR/` | immutable IDs/DTOs, fixture validation, version policy | live protocol adapter |
| STORE | `engineering/`, DB migrations, repository APIs | relational ProtocolInstance/Signal/Mapping schema + revision lifecycle | Studio Qt components |
| GUI | `studio/` + GUI tests | six-workspace shell and virtual Protocol Signals view | runtime/ protocol I/O |
| MAPPING | `runtime/mapping/` and pure transform packages | mapping compiler + graph/QA/multi-destination contract | Qt |
| MODBUS | `adapters/modbus/` + protocol tests | independently launched client/server TCP/RTU adapter | IEC-104 or domain changes without RFC |
| IEC104 | `adapters/iec104/` + protocol tests | server/client adapter with GI and observed quality | Modbus or domain changes without RFC |
| IPC | `ipc/` + `runtime/service/` | version handshake, auth, paged snapshots, subscription flow control | domain changes without RFC |
| SIM | `runtime/simulation/` + tests | opt-in virtual samples, TTL, ownership, origin and control isolation | Studio injection to real devices |
| QA | `tests/integration/` + `benchmarks/` | fault-injection, 100k/500k and app-lifecycle tests | others' implementation files |

Separate lanes may coordinate via contract interfaces defined below, not import each other's private modules. An issue is a task contract and must state owner lane, inputs/outputs, touched files, acceptance criteria, dependencies, exclusions. Parallel edits to the same path require coordination **before** code.

## Merge order / dependencies
- Phase A: ARCH baseline + STORE schema + GUI shell (independent PRs after baseline).
- Phase B: IPC server/client and mapping compiler parallel (after A public schemas stabilize).
- Phase C: Modbus and IEC-104 adapters in parallel using fake transport and contract tests.
- Phase D: SIM and monitoring after state/IPC APIs, no dependency on GUI for simulation runtime.
- Phase E: end-to-end Modbus→IEC-104 with simulated meter, then real connected interop; regression CI; installer/release.

## PR hygiene
- One workstream = one focused PR, refer to owning issue, base `main`.
- Never merge failing red CI; never merge a draft PR. New architecture breaking change requires ADR, change/migration and downstream dependency notices.
- Required PR template: owned paths, contracts used, changes, tests, benchmarks, docs, unresolved risks, rollback.
- No 'build succeeded' claims as evidence of SCADA interoperability.
- `feat/high-performance-signal-editor` PR #1 remains **draft proof of editor** and needs migration from flat column schema; do not merge it unchanged. Preserve benchmark/test learnings.

## Parallel handoff checklist
Read `AGENTS.md`, `docs/ARCHITECTURE.md`, `docs/CONTRACTS.md`, `docs/WORKSPACES.md`, your issue. Review `main` HEAD and contracts version. Work only in your lane, implement fake fixtures/tests before hardware wiring, and submit PR with exact acceptance evidence. Escalate cross-boundary changes through ARCH issue, don't silently edit other lanes.


## Open parallel work tickets (GitHub)

| Issue | Lane | Scope |
| --- | --- | --- |
| [#3](https://github.com/masarray/arpy-gateway/issues/3) | STORE | Normalized engineering DB + migration/revisions |
| [#4](https://github.com/masarray/arpy-gateway/issues/4) | GUI | Six-workspace shell and fast protocol-specific tables |
| [#5](https://github.com/masarray/arpy-gateway/issues/5) | MAPPING | Typed mapping compiler, transforms, large graph |
| [#6](https://github.com/masarray/arpy-gateway/issues/6) | MODBUS | TCP/RTU client+server adapters |
| [#7](https://github.com/masarray/arpy-gateway/issues/7) | IEC104 | Client/server, GI, spontaneous, quality/commands |
| [#8](https://github.com/masarray/arpy-gateway/issues/8) | IPC | Authenticated runtime service / revisions / telemetry |
| [#9](https://github.com/masarray/arpy-gateway/issues/9) | SIM | Runtime-side simulation with isolation |
| [#10](https://github.com/masarray/arpy-gateway/issues/10) | QA | 100k/500k, fault injection and interop |
| [#11](https://github.com/masarray/arpy-gateway/issues/11) | ARCH | Strict codec, adapter field schemas, contract evolution |

Launch separate development threads using a specific issue and the instructions in `AGENTS.md`. Each PR follows its own lane and must target the contract baseline on `main`. Do not run unmerged PR #1 code as the definitive Gateway architecture.
