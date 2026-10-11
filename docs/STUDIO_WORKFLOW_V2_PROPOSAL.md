# GUI issue #4 — Protocol-centric Explorer & Signal Binding redesign proposal

**Status: PROPOSAL / user review required.** Corrects workflow of draft PR #12 without changing normative v1 domain. **Architecture RFC [#13](https://github.com/masarray/arpy-gateway/issues/13) is a prerequisite to canonical Station/CommunicationPoint persistence.** Legacy demo GUI must not be merged as finished gateway software.

## Design objective

Engineers configure protocols, communication peers, signal ownership, mappings and live tests as one connected engineering project. The six separate workspaces remain, each with a context-aware protocol explorer on the left and task-specific tables/panels on the right. The enabled Protocol Instance is the common key for all workspaces; the same protocol may have several instances of the same role.

Avoid a single all-purpose signal table, dialog-per-signal, or fabricated signal counts. All panes project the **same stable identities** and repository revisions, not duplicate independent lists.

## Explorer specification and content

### 1. Gateway Config

Grid of enabled/disabled protocol role instances (Modbus TCP Client/Master, RTU Master, TCP Server, IEC-104 Server/Slave, IEC-104 Client/Master). Create/enable/disable/clone. Instance enable only changes engineering draft; it must not silently deploy and disabling must not delete connections/signals/mappings. Other workspace explorers show enabled instances by default; reveal disabled via explicit toggle for maintenance.

### 2. Protocol Config

```text
Modbus TCP Master
  General Settings                 (timeouts, retry, scan group defaults)
  IED Connections                  (bulk connection-point editor)
    Meter 01                       (192.168.1.10:502, Unit ID 1)
    Meter 02                       (192.168.1.11:502, Unit ID 1)
Modbus RTU Master
  General Settings                 (COM port, baud, parity, framing)
  Slave Stations / Connections     (Unit ID, poll profile)
IEC-104 Slave / Server
  General Settings                 (bind/listen, IEC104 timers, defaults)
  ASDU Stations                    (CA / local data ownership)
    Station CA=1
  Master Connections               (SCADA peer IP allowlist, limits, session policy)
    SCADA Master A
    SCADA Master B
```

Selecting a protocol root shows overview; selecting `General Settings` shows a concise typed parameter editor; selecting `IED Connections` or `Master Connections` shows an **Excel-like multi-row table** (add, duplicate, batch edit, import). Selecting a peer shows its detailed parameters and linked signals (except IEC-104 master sessions are not owners of CA/IOA data).

**Technical semantics:** Under IEC-104 Server, *allowed SCADA master IP* is an authorized communication peer, **not** an ASDU/Common Address station. Many masters may read the same local CA/IOA signal catalog. The CA data-station child and remote master connection child must not be conflated. Modbus TCP master binds each source signal to a configured IED connection. Modbus RTU global serial-port config belongs to the serial bus; Unit ID/station list belongs to peers.

### 3. Protocol Signals

Explorer shows only enabled protocol instances by default, with per-protocol child groupings:

```text
Modbus TCP Master
  All Signals
  Meter 01                       [ref configured CommunicationPoint]
  Meter 02
IEC-104 Slave
  All Signals
  Station CA=1                   [ref configured DataStation]
  Unassigned / Validation Issues
```

Right grid is protocol-specific. Modbus fields: linked Device/Connection selector (ID-backed dropdown, not free-text), register table FC (Coils, Discrete, Input, Holding), native 0-based register address and optional 4xxxx display label, Unit ID if applicable, type UInt/Int/Float, register count, byte/word swap, scaling, poll group, enabled. IEC-104 fields: data-station / CA, IOA, TI and its semantic category (TI 35 measured scaled with CP56Time2a vs TI 45 single command), direction, timestamp/COT policy, quality defaults. Hide irrelevant fields.

**Assign from Signal Catalog:** select source signals from another protocol/connection; destination IEC-104 station and default CA; choose TI/type conversion; preview auto IOA increment/address conflicts; atomic creation/reuse of IEC-104 destination signals + mapping edges. Clicking the newly assigned destination displays/edit its native IOA/TI. Source is not copied into a second source catalog, and no mapping depends on row position.

### 4. Protocol Mapping

Two linked virtual panes (Source Catalog / Destination Catalog) plus mapping list. Filter route by source protocol, connection, destination protocol, destination station and mapping type. Table shows Source instance, IED, source signal and address, Destination instance, station, IOA/TI, transforms/quality, validation state. Double click either side navigates to Protocol Signals at the exact immutable `signal_id`, correct protocol and connection/station filter, selects/focuses row; Back returns to mapping with selection intact. One source -> many destination signals; multiple sources -> same destination rejected unless policy explicitly approved. Auto-assign by stable ID/name/template and explicit rule with preview/confidence; no default index-to-index matching.

### 5. Protocol Monitoring

Explorer: enabled protocol instance -> All Signals / connections or stations / connection diagnostics. Right read-only virtual grid has protocol-relevant fields, not fabricated values. Modbus source: register, decoded value, raw words, quality, receive time, age, read errors, polling status. IEC-104 server: CA/IOA/TI, current value, quality, device/source timestamp where present, last COT *if actually observed/sent*, TX/confirmation/per-master session statistics where supplied by adapter. Live state is a runtime subscription (read-only), coalesced to viewport; connection-down -> Stale/Invalid, never fake Good or 0. Diagnostics pane can copy redacted instance/signal/session details and recent errors.

### 6. Value Simulation

Same enabled protocol explorer and per-protocol full signal table. Per-signal visible **Real ↔ Simulated** toggle, bulk enable/disable and Stop All, clearly show original last-received real value beside active injected value. Switching to simulation overrides a bounded runtime route input, *not* physically disconnects the IED socket or deletes live input. The sample must have origin=simulated, TTL, owner and rate limit; physical controls default blocked. Source simulation flows through Mapping translator to actual destination adapter. Destination IEC-104 server signal simulation feeds controlled outbound value for genuine SCADA readback when an independent runtime exists. Until IPC #8/SIM #9 implementation: no functioning toggle, label Preview Only / Not Delivered.

## Shared interaction state

Implement `SelectionContext` for project_id, revision_id, workspace, instance_id, optional connection_id/data_station_id, signal_id, mapping_id, table filters and navigation-back stack. Internal IDs never come from display names, addresses or visible row index. A project repository publishes changes to all workspace projections using typed change events and invalidate-by-scope; editors use optimistic revision checks and ordered writes. If a connection/instance is disabled or renamed, preserve its IDs; workspace references remain stable. A deleted dependency must trigger explicit preview/impact inspection.

## Performance and concurrency non-negotiables

- `QTreeView + QAbstractItemModel` for potentially large connection trees, lazy children / stable node IDs, not thousands of QTreeWidgetItem objects.
- `QTableView + QAbstractTableModel` paged 256-row virtual grid, LRU cap, server/SQLite indexed filtering and sorting on workers; no per-cell QWidget.
- Separate bounded read workers, single serialized write worker for repository mutations, cancellation/version generation per tree/table; stale callbacks never mutate new selection.
- 100k/500k signals must not be materialized into UI; **connection filtering queries use IDs + indexed connection scope**. Use stable row keys and reposition by ID after sort/filter.
- Monitoring: viewport subscriptions, per-signal coalescing to <= 10 UI refreshes/s by default; full runtime acquisition independent. No 500k row periodic repaint.
- Bulk Import/Assign/IOA increment: preview, collision detection, atomic revision edit/undo, progress & cancel before commit. Command routes isolated.
- Draft editable when inconsistent; deploy blocked until validation + explicit activation, never automatic.
- Native protocol editor field schemas must be provided by adapter capabilities/ARCH contract; GUI can temporarily show mock schema in issue #4 but must not silently add domain fields.

## Acceptance gate / mandatory test scenarios

1. Enable Modbus TCP Master + IEC104 Server; other disabled protocols hidden by default, reveal on request.
2. Modbus Master -> IED list add Meter 01, Meter 02 with separate IPs. Modbus RTU Master -> global COM/baud + slave Unit IDs. IEC-104 Server -> global bind/ASDU profiles + 2 allowed master IP connections + CA=1 data station.
3. In Protocol Signals select Meter 02; only its signals shown and Add Signal binds to its communication point ID. IEC-104 Server station CA=1 -> Assign from Catalog; pick Meter 02 points, auto IOA+TI with collision preview; both source and destination identity remain stable.
4. Mapping lists exactly those signal IDs; double-click source/destination opens correct Protocol Signals child/row and Back returns selection. One source may fanout.
5. Monitoring filtered by protocol/IED; unknown runtime explicitly Unavailable, active runtime shows protocol-aware value/quality/timestamp/COT only if supplied.
6. Simulator lists all selected-protocol signals, marks origin Simulated and TTL when online; real Rx remains observable; source injected sample passes through runtime translator to destination server and is read by external client. No GUI-only animation can satisfy protocol acceptance.
7. 100k/500k: tree selection/search/filter/scroll, 10k-paste, auto-assign, address edit and double-click navigation remain responsive using worker/virtual grid; measure actual Windows input latency/memory rather than relying on synthetic storage metrics.
8. GUI close must not interrupt running runtime; disable/rename one connection must not delete its signal catalog or silently change mappings.

## Handoff / blockers

**Architecture RFC #13** must define CommunicationPoint and DataStation stable identities and signal ownership; **STORE #3** must persist scoped relations/revisions and indexed queries; **IPC #8** + **SIM #9** deliver real Monitoring/Simulation. GUI #4 can build the explorer and mock provider without changing domain or pretending real telemetry exists. Refactor PR #12 on its own branch; keep it DRAFT and unmerged until gates pass. Preserve reusable virtual table/worker components; replace flat/index-driven routing logic and one-instance form navigation.
