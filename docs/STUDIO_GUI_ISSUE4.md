# ARPY Gateway Studio v2 — GUI issue #4 implementation and verification

**Status:** operational PySide6 offline *engineering preview*, not production gateway. Architecture v1 on main is unchanged. RFC [#13](https://github.com/masarray/arpy-gateway/issues/13) proposes canonical CommunicationPoint/DataStation identity and persistence; production database [#3](https://github.com/masarray/arpy-gateway/issues/3), IPC [#8](https://github.com/masarray/arpy-gateway/issues/8) and Simulation [#9](https://github.com/masarray/arpy-gateway/issues/9) are separate workstreams.

## Run
- Python 3.10+ with PySide6: `python -m pip install -e ".[studio]"`, then `arpy-studio --demo-rows 1000`.
- Compare virtual size: `arpy-studio --demo-rows 100000` / `--demo-rows 500000`.
- CI creates unsigned standalone Windows preview EXE and 1000/500000 signal Qt screenshots. Do **not** interpret successful build as live IEC-104/Modbus operation.

## GUI anatomy

1. **Top segmented-style workspace tabs**: six task workspaces in a single horizontal navigation bar; no fixed left sidebar. Back navigation + Light/Dark theme toggle stay in compact app header.
2. **Protocol Explorer**: common stable-ID hierarchy re-projected for each workspace; only enabled instances by default; toggle Show disabled. It never renders 500k signal nodes.
3. **Right primary workspace**: dedicated configuration editor, protocol-native signal spreadsheet, mapping grid, or monitoring/simulation catalog.

### Gateway Config

Create / enable / disable protocol role instances (Modbus TCP/RTU Client or Server, IEC104 Client or Server) in **offline draft**. Does not start any protocol session. Disabling instance does not delete its signal definitions.

### Protocol Config (separate peer vs station)

Modbus TCP Master -> General Settings (timeouts, retries, scan defaults) and IED Connections table (Meter01/Meter02 with IP, port, UnitID); Modbus RTU Master -> global COM/baud/parity and station peers by UnitID. IEC104 Slave -> General Settings (bind/listen and T1/T2/T3), local ASDU Station Common Address, and **distinct Master Connections list** (authorized remote SCADA IP). Multiple masters can access the same CA signal catalog. Selecting connection shows its inline-editable point parameters. Avoid treating IP of master peer as CA ownership.

### Protocol Signals

Explorer -> Modbus Master -> Meter02 selects only Meter02 signal IDs (original 1000-row dataset: #501–#1000). Explorer -> IEC104 Server -> CA1 selects local station signal IDs. The grid includes a read-only **IED / ASDU Station ownership** column plus protocol-native fields, including Modbus register table, raw types, byte/word swapping and IEC104 IOA/TI. Device/station identity is stable and view order never acts as primary key.

Add Signal creates a new stable-ID local draft signal under current connection/station. IEC104 destination **Assign from Catalog** opens an animated non-modal right drawer. A paged 4-column source table shows actual signal name, IED, native address/FC and type; search supports signal names, `addr:1000`, 4xxxx labels and `#1234`, with Ctrl/Shift multi-row selection. A separate compact review table shows *exact selected source rows, address and destination IOA* before assignment. User selects TI and IOA start; a single serialized writer validates stable IDs, correct IED ownership, monitored TI and IOA collisions; no row-range approximation or invisible signal selection. It creates new IEC104 destination signals and explicit mapping edges, not an index-to-index link. Duplicate IOA collision for already mapped destination signals is rejected. Draft is session-only and cannot deploy.

Spreadsheet shortcuts: Ctrl+C/V TSV, Ctrl+D/R, Ctrl+Z/Y; Find exact signal index `#1234`, sort, Auto-number selected. 256 rows per fetch, 24 bounded LRU pages, 2 reader threads/1 serialized writer and generation invalidation. TSV import/edit size caps and history caps are explicit. Synthetic edit/undo is not a substitute for durable Store #3.

### Protocol Mapping

Independent mapping record list. Double-click Source or Destination signal ID -> Protocol Signals, correct protocol owner, filter down to selected signal ID. Back restores prior mapping workspace. One source -> multiple destinations is allowed; fan-in arbitrated/rejected. No physical control runs from clicking mapping.

### Protocol Monitoring

Protocol Explorer filters the full virtual signal catalog by IED / ASDU station. The right table shows protocol columns plus read-only Value, Quality, Timestamp, COT and RX/TX. **Offline mode intentionally displays — / Unavailable**, never a fabricated quality Good or current timestamp. Copy Diagnostics provides redacted offline explanation. Actual per-instance telemetered values and COT need IPC and protocol adapter runtime.

### Value Simulation

Same explorer and full signal catalog as Protocol Monitoring. Previewing selected signal value is local and labelled **NOT transmitted**. The Real↔Simulated actual source switch is disabled until independent Runtime + authorization + TTL/origin implementation exists. Real-source network connection must not be silently disconnected by toggle; physical control is blocked.

## Global design tokens

`studio/design_tokens.py` is the only owner of semantic light/dark palette, typography, density scale, row height, sidebar/explorer widths and QSS. Semantic colors for success/warning/error/focus, neutral surfaces and high-density tables are centralized. No heavy QSS on 500k individual cells, no large decorative cards, and no per-cell QWidget.

## Deliberate boundaries / unfinished integration

- **No persistent project database**: new peers/stations/signals/assignments are session-only mock records, not revisioned engineering snapshots. Full indexed arbitrary substring search, durable changes, transaction rollback and ownership reassignment remain owned by Store #3 / RFC #13.
- **No live runtime**: IEC104 values/COT, connection diagnostics, genuine spontaneous messages and simulation-to-SCADA need adapter/IPC integration. Do not interpret a GUI preview as transmitted process data.
- No user-configurable new protocol adapter field schema yet (ARCH #11/#13); mock fields are derived from known Modbus/IEC104 semantics for GUI demonstration.
- Real Windows GUI latency, 100k/500k FPS and worst-case memory need human-in-the-loop benchmarks; offscreen CI demonstrates functional Qt rendering/worker operations but is **not** a performance SLA.
- Disabling one protocol hides it from explorers but its draft records remain intact. Deleting/moving dependencies requires future impact-aware Store transactions.
- Mapping bulk demo has an explicit maximum (2,000 newly assigned destinations per operation). Production mapping assignment needs store-backed batched commits, collision validation across all server namespaces, and durable undo/redo.

## Validation
Run `QT_QPA_PLATFORM=offscreen python -m unittest discover -s tests -v`.
GitHub Actions Linux checks v1 contract, v1 grid regression and v2 explorer/mapping/monitor tests; build Windows preview EXE on success. Protocol hardware acceptance is **out of scope**.

Do not merge draft PR #12 until user validates Windows GUI and Store #3 integration with official canonical schema. The corrected workflow specification is in [STUDIO_WORKFLOW_V2_PROPOSAL.md](STUDIO_WORKFLOW_V2_PROPOSAL.md).

## Catalog search preview limitations

All unfiltered catalog pages are virtual. Demo address search uses deterministic address calculations plus changed-value overrides; arbitrary name substring query runs only on a read worker, caps the result projection at 5,000 and explicitly labels it if truncated. The production indexed name/address catalog search belongs to Store #3. Selection max 2,000 signal IDs per assignment; a Ctrl+A of 500,000 rows is rejected by range count without materializing QModelIndex lists. The right drawer animates its width and can close without losing the configured destination station; GUI runtime never drives sockets.
