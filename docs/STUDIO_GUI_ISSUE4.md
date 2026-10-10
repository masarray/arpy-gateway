# Issue #4 — ARPY Studio GUI engineering preview

Status: **OFFLINE, session-only GUI. No protocol stack, deployed configuration, IPC runtime or physical value simulation yet.** Based on normative main architecture v1, not PR #1's old flat signal-table domain.

## Start
- Windows/macOS/Linux, Python 3.10+: `pip install -e ".[studio]"` then `arpy-studio`.
- `arpy-studio --demo-rows 100000` or `--demo-rows 500000` generates *virtual* rows in offline demo provider.
- CI builds an unsigned portable Windows .exe artifact. Unpack and open; **never interpret it as a functioning gateway**.

## Six independent workspaces
1. **Gateway Config** — add a Modbus TCP/RTU or IEC-104 instance; choose role Client/Master or Server/Slave; enable in offline project draft.
2. **Protocol Config** — per endpoint IP, port, serial parameters, Unit ID and CA; edit field + leave focus to apply to current **session only**.
3. **Protocol Signals** — choose endpoint and edit protocol-specific fields. Modbus shows PDU 0-based address, Unit ID, register table, byte/word order, scale. IEC-104 shows CA, IOA, TI (including TI 35 measurement vs TI 45 command), transfer and quality policies. Virtualized 256-row pages, max 24 cached pages. Stable IDs independent of sorting.
4. **Protocol Mapping** — two different protocol instances and source index/count; Preview then Auto-assign generates telemetry mapping edges to existing destination signal IDs, without writing protocol address fields.
5. **Protocol Monitoring** — connection status explicitly **Not connected**, data/quality **Unavailable**. Copy Diagnostics copies offline status.
6. **Value Simulation** — local value preview only. Runtime Start disabled; data **never forwarded**. Actual simulation belongs to Runtime issue #9 after IPC issue #8.

## Spreadsheet keys
`Ctrl+C` rectangular TSV to Excel; `Ctrl+V` rectangular TSV from Excel; `Ctrl+D` fill down; `Ctrl+R` fill right; `Ctrl+Z` undo; `Ctrl+Y` redo; type to edit; header sorting; search exact sequence number, e.g. `#12345`. Select integer address column and press **Auto-number selected**. Max 150,000 cells per clipboard/fill action and 150,000 edits per writer transaction. Clipboard TSV parsing is streamed in the writer worker (not the Qt main thread). WorkerHub has 2 read workers and 1 ordered writer, max 24 outstanding jobs.

## Known limits
- Demo provider is replaceable and deliberately does not create SQLite tables (owned by STORE issue #3). Edits, endpoint settings and mapping edges disappear on Studio exit. Records generated lazily, not a 500k-row materialization.
- Demo searching is exact sequence or general voltage/analog/all; arbitrary substring and edited-value-aware sort/filter require indexed STORE implementation. Address sorting uses generated numeric PDU order with Unit ID tie-break; edited addresses do not reorder in preview.
- Undo history is bounded at 16 batches **and 200,000 cell changes combined**, with at most 300,000 changed demo cells in memory; all **session-only**, not durable. Visual FPS requires real Windows test; offscreen tests are not display-speed measurements.
- Mapping preview does not edit IOA or register addresses; those belong in Protocol Signals. Runtime mapping compilation and full address collision checks are not yet connected.
- No protocol sockets and no fabricated live values. Actual command simulation deliberately unavailable.

## Ownership
All issue #4 code stays in `src/arpy_gateway/studio/` with Qt tests, workflow, optional Qt packaging bootstrap and this non-normative guide. No edits to `domain/`, `contracts/`, `runtime/`, `adapters/` or `ipc/`. Replace DemoProvider with async engineering repository from issue #3 and authenticated runtime IPC from issue #8.

## Test
`QT_QPA_PLATFORM=offscreen python -m unittest discover -s tests -p 'test_studio_gui.py' -v`

CI runs pure contracts and GUI smoke/unit, captures Linux screenshot, and builds an unsigned Windows portable EXE. PR remains **draft** until validation and user workflow testing.
