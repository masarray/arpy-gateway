# ARPY Gateway

**Universal Industrial Protocol Gateway** — Python-based, cross-platform runtime and professional PySide6 engineering Studio.

> **Status: Architecture foundation / contract v1. Not yet a functioning Modbus or IEC 60870-5-104 gateway.** The previous high-performance spreadsheet GUI prototype remains isolated in [draft PR #1](https://github.com/masarray/arpy-gateway/pull/1). Its flattened data model is not the final design.

## Six purpose-built workspaces

1. **Gateway Config** — activate protocol types and create client/server instances.
2. **Protocol Config** — configure endpoint network/serial parameters, timeouts and reconnection.
3. **Protocol Signals** — edit protocol-native point types and addresses using virtualized, Excel-grade tables.
4. **Protocol Mapping** — join source signals to one or many destination signals, with safe auto-assign and address increments.
5. **Protocol Monitoring** — per-protocol values, RX/TX, quality, timestamps, actionable copy diagnostics.
6. **Value Simulation** — in monitoring, opt-in source injection to exercise real downstream protocols when hardware is unavailable; physical controls remain blocked by default.

**Separate processes:** Studio GUI (optional) and a headless Gateway Runtime service. A GUI crash/exit must never interrupt field communication. Draft configuration never silently deploys.

## Start here before writing code

- [AGENTS.md](AGENTS.md) — mandatory contributor contract for every development thread.
- [Architecture blueprint](docs/ARCHITECTURE.md) — runtime/data/control planes, workers and process separation.
- [Normative domain contracts](docs/CONTRACTS.md) — instances, signals, mapping, quality, simulation and v1 IPC.
- [Workspace UX specification](docs/WORKSPACES.md) — navigation/workflow and protocol-aware editing.
- [Performance and anti-naive coding rules](docs/PERFORMANCE.md) — bounded workers/cache, O(N+E) graph, acceptance benchmarks.
- [Parallel delivery/ownership](docs/DELIVERY.md) and [roadmap](docs/ROADMAP.md).
- [Architecture Decision Records](docs/ADR) and [v1 JSON schemas](contracts).
- [Merged foundation PR #2](https://github.com/masarray/arpy-gateway/pull/2) with pure Python domain skeleton and contract tests.

## Headless service architecture — proposed implementation plan

Industrial deployment will use **three independent entrypoints**: `arpy-gatewayd` (OS-managed Windows Service / Linux systemd **system** service), `arpy-studio` (optional PySide6 engineering client), and a proposed `arpyctl` (diagnostic CLI). Studio closing, crashing or user logout **must not** stop protocol communication. A config Save cannot activate live outputs: engineering revisions require Validate → Stage → explicit authorized Deploy, with last-known-good recovery.

**Engineering blueprint and implementation references (documentation only; no service shipped yet):**

- [Service architecture & runtime data path](docs/blueprints/RUNTIME_SERVICE_V2.md) — independent lifecycle, per-instance supervisors, bounded queues and protocol quality.
- [Authenticated IPC and safe revision activation](docs/blueprints/IPC_AND_REVISION_DEPLOY_V2.md) — auth, compatibility, idempotency, CAS, recovery journal and rollback.
- [Windows/Linux service and packaging](docs/blueprints/PLATFORM_PACKAGING_V2.md) — SCM/systemd, least privilege, service vs Studio upgrades.
- [Operational runbook](docs/operations/OPERATIONS_RUNBOOK.md) — boot, alarms, troubleshooting, backup and recovery.
- [Runtime acceptance test matrix](docs/quality/SERVICE_ACCEPTANCE_MATRIX.md) — OS/failure/interop load gates with evidence levels.
- [Parallel implementation handoff](docs/implementation/SERVICE_DELIVERY_HANDOFF.md) — strict ownership issues #3–#13, merge dependencies and blockers.
- [Industry references and unresolved decisions](docs/blueprints/INDUSTRY_EVIDENCE_AND_OPEN_DECISIONS.md) — documented evidence, alternatives and RFC gates.

**Current implementation:** `main` remains foundation contracts; the GUI-only implementation is [draft PR #12](https://github.com/masarray/arpy-gateway/pull/12) and does **not** provide live Modbus/IEC104 communication, OS services or runtime simulation. The service/IPC implementation is [issue #8](https://github.com/masarray/arpy-gateway/issues/8), and peer/station persistence still needs [architecture RFC #13](https://github.com/masarray/arpy-gateway/issues/13). Documentation does not certify a field-ready gateway.

## Contract test (no Qt required)

```shell
PYTHONPATH=src python -m unittest discover -s tests -p 'test_contracts.py' -v
```

Run on Python 3.10+; CI also validates JSON Schema and a demo project at `examples/projects/modbus-client-to-iec104-server.v1.json`.

## Scope and ownership

Only structural code and tests are implemented in the architecture foundation on `main`. **No Modbus client/server, IEC-104 networking, GUI six-workspace shell, transport server, or field-data simulation is functional yet.** All implementations must be developed in issue-linked isolated PRs after the contract baseline reaches `main`. The [workstream issues](https://github.com/masarray/arpy-gateway/issues) enumerate dependencies and non-overlapping code areas.

Qt is not a runtime dependency. Python domain contracts cannot import Qt/protocol libraries. Any control capability requires explicit safety review and field-interoperability tests.
