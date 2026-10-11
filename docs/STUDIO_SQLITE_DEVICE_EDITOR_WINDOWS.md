> **P0 USER FEEDBACK / BLOCKED FOR STANDARD GATEWAY UX (2026-10-11):** This preview's separate SQLite Saved Devices panel is **not integrated with the standard Protocol Explorer → IED Connections**. The tree, normal connection grid, General Settings, and other engineering editors still use `EngineeringView(DemoProvider)`, so saved devices appear only after pressing the extra Saved Devices button. This is a verified architectural source-of-truth defect, **not** an accepted gateway workflow. **Start next work with [the single-source-of-truth handoff](handoffs/2026-10-11-studio-single-source-of-truth.md)**. Do not present this standalone preview as complete persisted device configuration, and do not merge PR #23 before the P0 is fixed and Windows GUI/EXE regression passes.

# Windows-first SQLite Device Draft Editor — Issue #17

**Engineering preview candidate; not an installed gateway or active Deploy.**

This GUI-lane implementation is stacked on [live IPC Studio PR #20](https://github.com/masarray/arpy-gateway/pull/20). It uses [RFC #13 ARCH draft PR #22](https://github.com/masarray/arpy-gateway/pull/22) and [SQLite Store draft PR #21](https://github.com/masarray/arpy-gateway/pull/21) through pinned CI overlays, not unreviewed cross-lane source copies or automatic merges.

## User workflow (no terminal)

Open the Windows Studio engineering preview, choose **Protocol Config → Saved Modbus Devices (SQLite)**, then **+ Add TCP IED**. Enter device name, host/IP, port, Unit ID, poll interval and enabled state in a normal dialog. The **Save** action stores a `CommunicationPoint` using its own immutable ID in the local user database, and explicitly reports **SAVED DRAFT · NOT DEPLOYED**.

Select an IED and **Edit Selected** to modify connection settings without changing the original stable ID. Close/reopen Studio and use the same Saved Devices screen to load persisted entries. The backend saves using optimistic draft generation CAS, so a second stale Studio cannot silently overwrite a newer draft. Paging is indexed by stable ID (128 per page). **Validate & Stage** is separate from Save; it deliberately refuses to stage a device-only project with no configured signals. Staging does not activate anything.

The database path is chosen automatically from the current OS account: Windows `%LOCALAPPDATA%/ARPY/Gateway Studio/engineering.sqlite3`; Linux/XDG only when final Linux verification is requested. The engine and transport dependencies must be embedded in a future signed installer; the preview executable bundles the Store components offline and never requests users run pip/PowerShell.

## Safety and integrity boundaries

- The existing Protocol Explorer and Signal Editor demo data remains **visibly separate** and is not copied or falsely joined to Saved Devices; its mock identities are not authoritative.
- No PyModbus, field I/O, deployment, simulation or protected OS-service mutation runs in the Qt editor. Studio submits SQLite work through its existing single bounded `WorkerHub` writer; the GUI thread performs only view/model updates.
- The editor uses existing canonical `EngineeringStoreV2.apply_v2()`, `communication_point_page()` and `stage_v2()` interfaces, not a second database format. SQLite opens/closes its connection per worker operation; no database handle is passed to the runtime.
- New databases are initialized with reviewed candidate v2 **only when first created**. Existing v1 databases are **not silently migrated**: they require explicit backup, ARCH acceptance and upgrade. The Studio source never auto-deploys a staged revision or touches Windows SCM.
- This milestone currently edits **Modbus TCP Master IED connections**. Physical RTU serial port selection, signal editor persistence, active revision deployment, real auto-discovery of authenticated IPC, redundancy and signed native installer remain separate acceptance gates.

## Windows-first CI and evidence

`.github/workflows/gui-studio.yml` now gates Windows GUI tests before packaging. It overlays pinned PR #19 read-only IPC/runtime, PR #15 Modbus adapter, PR #22 ARCH schema and PR #21 Store **in the CI checkout only**. It exercises the prior Qt/navigation/500k virtual catalog, real TCP→daemon→IPC→Qt tests and new `tests/test_studio_saved_devices.py` for two IEDs, stable IDs on rename, Save/close/reopen, stale CAS, invalid host/unit, refusal of silent v1 upgrade, and no active Deploy. Windows preview adds Store v2 hidden imports to PyInstaller. Linux GUI job is manual only until Windows is stable, per maintainer instruction.

**Not claimed:** installed gateway service, production IPC authorization, explicit active Deploy with rollback, actual field hardware, physical RTU test or full GUI signal database editor. Both Store/ARCH upstream PRs remain draft; release requires review/merge, security QA and native service integration.
