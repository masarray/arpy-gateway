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

## Scope boundary
Initial Phase P0 is not a gateway runtime, not complete GUI, and contains no functional protocol handlers. Each later workstream should maintain acceptance evidence in its issue/PR.

## UX lessons from Siemens manuals (reference only)
SICAM PAS: Configuration/Mapping/Templates/Operation/Value Viewer; table freeze/filter; formula-based device creation. SICAM Device Manager: Signal List separate from RTU Signal Assignment; list/tree, bulk increments, structured/unstructured IEC-104 addresses, cross-application bindings. ARPY adapts **concepts**, not proprietary software or source code. Ownership-defined workspaces must simplify, not clone Siemens UX.
