# Delivery blueprint

State definitions: `Contracted` = agreed interface only; `Skeleton` = importable placeholders/tests; `Implemented` = demonstrated behavior under automated tests; `Field-verified` = proven against target hardware/SCADA.

| Phase | Milestone | Gate |
| --- | --- | --- |
| P0 | Normative contracts + architecture, 6-workspace specification, owners & issues | pure domain schema validation CI + reviews |
| P1 | Relational engineering store & migrations; six-workspace PySide6 shell | 100k/500k virtual grid + atomic edit + undo, independent Studio |
| P2 | mapping compiler, typed transforms, protocol signal schema registry | O(V+E) validation, fanout and collisions tests |
| P3 | versioned local IPC + independent runtime lifecycle | close GUI no loss of runtime traffic; deploy/rollback tests |
| P4 | Modbus TCP client/server + RTU client/server | protocol simulator interop, reconnect, endian/word order tests |
| P5 | IEC-104 client/server + translator | GI, spontaneous, quality, command tests with independent SCADA |
| P6 | runtime simulation + Monitoring/Diagnostics | no field device; injected source reaches real connected destination |
| P7 | hardened integration/release | CI regression, security, service install, large load, field acceptance |

## Runtime service engineering gates (implementation plan)

**Track S0–S5 is a proposed decomposition of phases P1–P7, not a renumbering or statement that phases are complete.** A fake-adapter runtime and OS service lifecycle can start in parallel with Store/Mapping contract work; actual deployment must wait for approved revisions, peer/station schema and protocol interoperability.

| Gate | Deliverable | Evidence required | Owner |
| --- | --- | --- | --- |
| S0 | Resolve peer/station model, IPC/adapter capabilities, revision schema | ARCH #11/#13 approved decisions + migration fixtures | ARCH |
| S1 | Independent daemon/service + read-only IPC health with fake adapter | Windows/Linux SCM/systemd; Studio close/kill/logout does not disconnect fake source | IPC #8, QA #10 |
| S2 | Immutable snapshot + staged apply/rollback + O(V+E) map plan | CAS/idempotency, power-loss journal, invalid drafts do not affect active revision | STORE #3, IPC #8, MAPPING #5 |
| S3 | Modbus TCP/RTU + IEC104 role/sessions | Independent protocol client/server traces including GI/COT/quality/reconnect | #6/#7 |
| S4 | Runtime simulation + Studio IPC monitoring | simulated source → actual destination adapter → external SCADA readback; physical controls blocked | SIM #9, IPC #8, GUI #4 |
| S5 | Windows/Linux installers, hardened operations and load acceptance | install/boot/logout/uninstall/rollback, 100k/500k benchmark, security & safety gates | QA #10, platform owner |

**Release is blocked** until `docs/quality/SERVICE_ACCEPTANCE_MATRIX.md` C2/C3 tests pass on target Windows and Linux builds; field-verified claims require C4 independent field evidence. GUI PR #12 is draft and must not be used as evidence that the runtime is functional.

Further detail: [headless runtime](blueprints/RUNTIME_SERVICE_V2.md), [IPC/deploy](blueprints/IPC_AND_REVISION_DEPLOY_V2.md), [platform packaging](blueprints/PLATFORM_PACKAGING_V2.md), [parallel handoff](implementation/SERVICE_DELIVERY_HANDOFF.md).

## Scope boundary
Initial Phase P0 is not a gateway runtime, not complete GUI, and contains no functional protocol handlers. Each later workstream should maintain acceptance evidence in its issue/PR.

## UX lessons from Siemens manuals (reference only)
SICAM PAS: Configuration/Mapping/Templates/Operation/Value Viewer; table freeze/filter; formula-based device creation. SICAM Device Manager: Signal List separate from RTU Signal Assignment; list/tree, bulk increments, structured/unstructured IEC-104 addresses, cross-application bindings. ARPY adapts **concepts**, not proprietary software or source code. Ownership-defined workspaces must simplify, not clone Siemens UX.
