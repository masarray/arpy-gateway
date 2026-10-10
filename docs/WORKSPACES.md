# Six-workspace UX contract (approved direction)

Single PySide6 Studio window with navigable **separate workspaces**, not one table doing everything. Slim navigation, compact rows, keyboard-first engineering. Space devoted to data, no oversized cards/bold headings; docking/split panes only where they simplify the task. Each protocol has its own subviews/column schema.

| Workspace | Responsibility | Explicit non-responsibility |
| --- | --- | --- |
| 1 Gateway Config | Enable protocols and create/delete/enable protocol **instances** with client/server roles; overall topology and readiness | Native point editor |
| 2 Protocol Config | Per-instance IP, port, serial baud/parity, Unit ID/CA, timeouts, reconnect, scan groups and credential references | Signal remapping |
| 3 Protocol Signals | Per-instance signal definition and adapter metadata; binary/analog/counter/command, Modbus raw formats/byte swapping, IEC-104 TI; bulk spreadsheet | Cross-protocol relationship |
| 4 Protocol Mapping | Bind source signal → destination signal; auto assign, address increment, typed conversion, preview/collision detection, many-to-many | Communication sockets |
| 5 Protocol Monitoring | Per-instance connection health, RX/TX, quality/time, last received/transmitted, device errors, copy diagnostics | Mutating engineering draft |
| 6 Value Simulation | Adjacent to / tab within Monitoring; per-signal simulation values and modes; observe onward propagation | Live control of physical field devices by default |

## Primary workflow
1. New project → Gateway Config: enable `Modbus TCP Client` and `IEC-104 Server` and instantiate endpoints.
2. Protocol Config: specify connection/port/CA/Unit ID and polling.
3. Protocol Signals: import, generate or edit source Modbus point descriptors; separately define destination IEC-104 points.
4. Protocol Mapping: select source and destination instance, join signals, bulk auto-address, review transform, mapping validity, save draft.
5. Validate → staged deployment diff → explicit deploy to independent Runtime (when implemented).
6. Protocol Monitoring: see live values & source-of-quality per endpoint, copy diagnostic details.
7. Simulation: without physical meter, enable manual point simulation for authorized testing; verify IEC-104 output with actual connected client and confirm simulated origin/quality.

## Protocol-aware editors
- `Modbus`: coils/discrete/input/holding; PDU-based native address **and optional human 4xxxx label** (not conflated), Unit ID, raw unsigned/signed 16/32/64, Float32/64, byte order, word order, scaling, polling group.
- `IEC-104`: CA/IOA numeric with optional display of byte components, TI type with user-friendly description, monitoring vs command separation, spontaneous/GI, quality/timestamp policy.
- `Future`: IEC 61850 MMS dataset/FC/RCB, OPC UA NodeID, DNP3 index etc. Editor columns supplied by protocol plugin metadata.

## Interaction principles
- Left navigation: workspace + protocol instance list; center large virtual grid; optional right inspector; bottom collapsible errors/diagnostics.
- Single click select, type edit, Ctrl+C/V TSV multi-cell, Ctrl+D/R, Ctrl+Z/Y, multi-row/column selection, batch replace, context-aware auto fill.
- Filter and sorting are **view-only** and never alter signal IDs. Composite filters are visible removable chips. Numeric address sorting must be numeric (1,2,10; not 1,10,2).
- Avoid popup per signal. Bulk actions show preview when potentially destructive, validation before commit, progress/cancel, single undo unit. Persist undo appropriately.
- Search 500k rows via worker-owned indexed queries and bounded virtual model, never 500k Python QObjects.
- No silent field engineering changes from monitoring actions. Monitoring is read-only except clearly separated simulation/debug controls.

## Acceptance scenarios
- User configures Modbus TCP source + IEC-104 server from blank project without writing YAML or scripts.
- User pastes 10k signals, auto-assigns 10k IOAs, filters, reverses sort, undoes—all stable, no overwritten row.
- One Modbus signal feeds two different destination protocol instances.
- Address conflict is diagnosed in context and blocks deploy, not save of draft.
- GUI closes while runtime continues; reconnecting Studio rehydrates current revision and live quality.
- Simulation sends actual values through runtime (when adapters exist); graphic placeholder alone is NOT pass.
