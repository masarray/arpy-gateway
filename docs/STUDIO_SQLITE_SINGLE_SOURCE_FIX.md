# Single-source SQLite engineering UI — correction to PR #23

**Reason for change:** The former normal preview incorrectly used two independent engineering sources. The button **Saved Modbus Devices (SQLite)** showed persisted `tcp-...` records; however the Explorer and IED Connections still showed preseeded `DemoProvider` Meter 01/Meter 02. Closing/reopening Studio could make it appear that a saved device had vanished. This was an architecture/UX error, not an expected gateway workflow.

## Corrected installed Studio workflow

The **normal executable** now opens `SQLiteStudioWindow` from `studio/sqlite_window.py` with **one project database** at the existing user profile path: `%LOCALAPPDATA%/ARPY/Gateway Studio/engineering.sqlite3`. It opens the database in place and **does not clear/migrate/reseed existing v2 records**. The prior IED `Power Meter A` retains the same `tcp-...` ID, name, host, port, Unit ID, polling interval and enabled status.

- **Protocol Explorer** derives instances, IEDs/peers and data stations from the same SQL project-scoped tables. IED Connections auto-expands on first open, without pressing a Saved Modbus button.
- **Protocol Config → IED Connections** is the *only* Modbus device editor. Add/Edit uses Store v2 `apply_v2` and revision CAS; after commit it refreshes both the Explorer and the table. General instance name, status and existing settings are edited through the same transactional Store; IEC-104 data stations use persisted `DataStation` identity.
- **Protocol Signals** reads indexed keyset signal pages (max 128 items) joined in one read snapshot to explicit `SignalBinding`; Add/Edit persists native Modbus address, type, table, unit, name and enabled state using the same project generation. The old `meter01:signal:...` generated rows are never treated as field signals in this mode.
- **Protocol Mapping** reads paged stable-ID edges from SQLite; Add Mapping writes a draft edge to the same Store (not to memory). Save ≠ Validate/Stage ≠ authenticated Deploy.
- **Protocol Monitoring** shows only IPC-provided values if explicitly attached to an independent headless gateway. **Value Simulation** does not generate fake Good values or activate physical outputs.
- The prior 500k-cell **demo** remains explicitly available via `--demo` for test/legacy spreadsheet QA. It is never instantiated on a normal installed Studio launch.

All DB calls occur on bounded `WorkerHub` workers. The per-connection reader uses one consistent SQLite snapshot and keyset indexing. UI never owns Modbus sockets. Duplicate draft identities, FK errors and stale revisions fail closed; no phantom Save success and no silent live Deploy.

## Windows CI acceptance criteria

`tests/test_studio_sqlite_authoritative.py` seeds the existing Store v2 record exactly as the previous release did, launches the **new default** window, and asserts:
1. The saved IED appears in IED Connections and Explorer automatically; the two demo Meter 01/Meter 02 entries are absent.
2. Editing IP/name via the normal connection table preserves its stable ID and is reflected after closing/reopening the Studio.
3. Adding a signal in the selected IED creates a real v2 `SignalBinding` to that exact ID, and it remains available after restart.
4. Mapping view shows actual SQLite mappings rather than demo-generated routes.

The normal Windows exe is smoke-tested by actually starting the one-file PyInstaller artifact without runtime Python/PYTHONPATH and capturing a screenshot. Windows CI runs first; Linux QA and installer/release tests are deferred until Windows acceptance.

## Release boundaries

ARCH #13, Store #21, headless runtime #19 and GUI #20 remain independent draft proposals. The editor is a **persisted engineering preview**, not yet a fully deployed gateway. IEC-104/RTU signal editors, large-scale spreadsheet bulk persistence, production authenticated IPC, OS service auto-start, rollback/LKG and real hardware field QA are still open. This change deliberately does not invent adapter deployment or claim full 500k-signal UI throughput.

**No SQL schema changes, no automatic migration, no data reset.** Do not merge to main before review of the dependent contract RFC and downstream service integration.
