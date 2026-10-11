# Cross-thread implementation handoff — headless ARPY gateway

**Status: PROPOSED integration plan, no runtime implementation claimed.** Based strictly on accepted ADR-0001/0002/0003 and unchanged `main` contracts. Own exactly one lane from `docs/DELIVERY.md` per PR; branch from current main; no cross-ownership edits/accidental merges.

## 1. Final product decomposition

**Mandatory executable processes:**
- `arpy-gatewayd`: Python headless runtime and OS-managed service, no Qt import.
- `arpy-studio`: optional PySide6 engineering/UI client, own bounded Qt workers, attach/detach IPC.
- `arpyctl`: proposed headless CLI diagnostic client, same authentication/authorization as Studio.

**Inside runtime:** revision verifier, supervisor per enabled instance/connection, Modbus and IEC104 adapter registry, per-instance connection/session state, typed latest signal store and protocol-aware bounded event delivery, compiled mapping graph, simulation source and observability. Studio never owns network sockets or active config. No one `thread per signal`; protocol instance polling and per-device grouping drive scheduling.

**Compatibility:** one Python source repository does not mean one portable executable: Windows SCM-hosted bundle, Linux systemd package and CLI/Studio artifacts require separate platform QA.

## 2. Ownership / interfaces (must not overlap)

| Lane / issue | Exclusive source ownership | Deliverables + handshake |
| --- | --- | --- |
| ARCH #11/#13 | `domain/`, `contracts/`, `docs/CONTRACTS.md`, accepted ADR/migration | approve ConnectionPoint vs local DataStation/CA identity, protocol field schemas, auth/command semantics; versioned fixtures |
| STORE #3 | `engineering/`, SQL migrations/repository API | revisioned normalized peers/stations/signals/mappings, immutable validated snapshots, hashes, ordered writer & migrations |
| GUI #4 / PR #12 | `studio/` and GUI tests | top tabs/explorer/cell editor/catalog, typed IPC client adapter + reconnect/read-only status; never link adapter code |
| MAPPING #5 | `runtime/mapping/`, pure transforms | deterministic compiled O(V+E) graph; address/cycle/fan-in/policy diagnostics, quality/TS propagation |
| MODBUS #6 | `adapters/modbus/` and protocol test | TCP/RTU client/server, per-IED/bus scheduler, socket/COM recovery, documented native address semantics |
| IEC104 #7 | `adapters/iec104/` and protocol test | client/server TI/COT/GI/spontaneous/confirm semantics, remote peer sessions vs CA ownership, command isolation |
| IPC/service #8 | `ipc/`, `runtime/service/`, lifecycle tests | SCM/systemd-ready process composition, auth/versioned local IPC, subscription snapshot/resume, staged deploy/rollback |
| SIM #9 | `runtime/simulation/` and test | runtime-owned TTL/owner/provenance injection, pipeline-forwarded monitoring, physical control blocked |
| QA #10 | `tests/integration/`, `benchmarks/`, acceptance & packaging smoke | failure injection, Windows/Linux service liveness, protocols captured against external peers, measured performance |

Packaging/test workflows impacting other lanes require coordination before editing shared files. Documentation in `docs/blueprints` / `docs/operations` / `docs/quality` is implementation guidance; no silent normative API changes.

## 3. Dependency graph and gates

```text
Accepted v1 ADR/Contracts on main
        |
        +--> ARCH #11/#13 contract decisions, fixtures & version gates
        |         +--> STORE #3 peer/station/revisions/persistence
        |         +--> adapter schema alignment (#6/#7)
        |
        +--> IPC #8 service lifecycle + fake adapters + health bridge   [can prototype now]
        |         +--> Studio attach/detach & reconnection (#4 integration)
        |
        +--> MAPPING #5 pure compile graph with synthetic snapshots
        +--> Modbus #6 / IEC104 #7 independently stubbed against adapter port
                    |
            join: Store validated snapshot + IPC staging + compiled mapping
                    |
            SIM #9 (after runtime signal store/publish path)
                    |
            QA #10 multi-OS / actual Modbus→IEC104 interoperability
                    |
               Candidate Release
```

Do **not** wait for all protocol stacks to begin service lifecycle: implement fake adapter heartbeat and independent Studio GUI close/reopen first. But do **not** call this fake adapter a connected industrial gateway. Integrate with real adapters only after typed ports and capability models pass contract tests.

## 4. Proposed sequence of focused implementation PRs

### S0 — ARCH contract readiness / review (owner ARCH)
- Resolve RFC #13 distinction: CommunicationPoint/peer identity vs IEC104 local DataStation/CA and Modbus RTU shared serial bus; adapt project schema with explicit migration if approved.
- Reconcile `EngineeringRevision` content/hash and adapter-owned settings with deployment dry-run and address namespace validation.
- Define protocol time/quality/COT semantics and control authorization at public contract boundary; ADR if incompatible with v1.
- Exit gate: documented owner-approved fixtures for 2 Modbus IED peers + 1 IEC104 Server with CA=1 + 2 allowed SCADA master IP peers; no ambiguous field ownership.

### S1 — Minimal independently managed runtime (owner IPC #8)
- Provide headless main `arpy-gatewayd` composing fake adapter and health service, without PySide6 imports.
- Lifecycle: STARTING / UNCONFIGURED / RUNNING / DEGRADED / STOPPING; non-blocking supervisor and clean cancellation; signal handling on Linux; Windows SCM service wrapper tied to same composition root.
- Local IPC handshake, health, capabilities and read-only revision status; invalid requests reject. Unit tests prove no Studio required.
- Exit gate: service fake-adapter sample counter **continues across Studio exit/force-kill/logout**, demonstrable Windows & Linux OS service.

### S2 — Store snapshots + safe deployment (STORE #3, IPC #8, MAPPING #5)
- One draft writer and indexed readers; stable identity relations and migration; snapshot hash/revision ledger.
- Compile and validate mapping graph from immutable revision. Stage/explicit Apply/rollback with idempotency and expected-active-revision CAS.
- Power-loss journal injection before/during/after commit; restart honors last-good only. Impact plan indicates reload vs endpoint restart.
- Exit gate: corrupted/invalid draft cannot affect running IED; crash at any activation boundary has deterministic safe recovery.

### S3 — Protocol adapters and sessions (MODBUS #6 / IEC104 #7)
- Independent protocol fakes and external tool interop first; no Qt dependency, correct native addressing, GI/spontaneous/quality/timestamp/COT and command safety.
- Map source values into destination protocol with correct backpressure; simulate LAN loss, RTU disconnect, busy SCADA sessions.
- Exit gate: independent remote client observes value transmitted through Modbus → Mapping → IEC104; behavior proven with traces, not mock UI.

### S4 — Monitoring / simulation / operational tooling (IPC #8, SIM #9, GUI #4)
- Studio uses only typed IPC adapter; reconnect refreshes current revision/instance health, quality and viewport signal data.
- Runtime Sim source has TTL/owner, origin and safe priority vs real; Stop All, redacted diagnostic capture; physical control blocked by default.
- Exit gate: mock source absent; simulation through real destination to external SCADA confirmed; closing GUI does not stop adapter sessions.

### S5 — Cross-OS packaging and release (QA #10, IPC/platform packager)
- Windows SCM service wrapper + portable/installed Studio; Linux systemd **system** unit + desktop Studio, service user/ACL, no unintended admin listener.
- Test clean install, boot, logout, uninstall, GUI-only update, engine upgrade/migration, backup/rollback and 100k/500k performance.
- Exit gate: `docs/quality/SERVICE_ACCEPTANCE_MATRIX.md` mandatory C2/C3 rows pass on selected OS; C4 field signoff before claiming field-ready.

## 5. Proposed public seam inputs/outputs

Freeze these semantics via ARCH approval; avoid editing another lane's public model opportunistically.

| Interface | Input | Output / error | Compatibility rule |
| --- | --- | --- | --- |
| Snapshot repository | revision ID + expected schema | hashed immutable revision or validation error | same address/station semantics across OS |
| Adapter Registry | protocol capability/instance | adapter object + settings/field schema | transport/library selection internal |
| Supervisor | instance + validated snapshot | state+reason, async lifecycle, bounded samples | no GUI events required |
| Mapping compiler | immutable signal IDs/edges | immutable dispatch graph + detailed errors | O(V+E), explicit type/control policy |
| State store | immutable SignalSample with ID/seq | atomically current sample / change event | no manufactured timestamps/Good |
| IPC | versioned envelope + auth | versioned replies/operation ID/subscription pages | no raw Python object serialization |
| Simulation | authorized ID, TTL, mode, value | origin-tagged injected sample/audit | no physical control by default |

Compatibility test fixtures must include multi-peer Modbus, IEC104 CA vs master IP, 500k ID paging, mixed time quality, command denied, deployed revision persistence and fake crash-restart sequence.

## 6. No-naive implementation review checklist

1. Does runtime import Qt/Studio, call a GUI subprocess, or share Studio SQLite handles? **BLOCK**.
2. Does Studio closing trigger adapter.stop or signal state reset? **BLOCK**.
3. Is there any one-thread/timer per signal, unbounded async queue, unlimited socket retry or busy loop? **BLOCK**.
4. Are false `Good`/`now`/`0` values emitted on timeout, stale link or fake monitoring? **BLOCK**.
5. Is deployed configuration reading mutable drafts, or save auto-deploying? **BLOCK**.
6. Are IDs inferred from visible row/address/IED name, or IEC104 peer IP confused with CA station? **BLOCK**.
7. Is a failed stage/partial deploy recoverable only by wiping DB/restarting Studio? **BLOCK**.
8. Does malformed IPC, unprivileged operator or simulation open a command path? **BLOCK**.
9. Does CI claim hardware function solely from GUI screenshot or PyInstaller build? **BLOCK**.
10. Are latency, CPU, queue backpressure, memory, protocol reconnection and OS service tests missing? **KEEP DRAFT**.
11. Are protocol dependencies/licenses not vetted for Windows/Linux? **KEEP DRAFT**.
12. Has a PR changed normative domain/schema/API without ARCH RFC and migration fixtures? **BLOCK**.

## 7. Exactly what to hand to next work thread

- Read `AGENTS.md`; accepted ADRs; `docs/CONTRACTS.md`, `docs/DELIVERY.md`; this blueprint and service/IPC/deploy/QA docs.
- State issue ownership explicitly (#8 for service/IPC); start fresh from current `main`, not unfinished Studio PR #12.
- List exact code paths, versions, test fixtures, measurable budgets and dependencies before implementing.
- PR must include complete run commands, fake-adapter lifecycle demonstration, OS service scope, negative tests, auth outcome, rollback/fail cases and **unverified** checklist.
- Never merge solely because CI is green. Upstream domain changes require accepted RFC and coordinated downstream implementation.
