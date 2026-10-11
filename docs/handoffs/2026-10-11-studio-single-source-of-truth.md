# HANDOFF — ARPY Gateway Studio: konsolidasi engineering database (2026-10-11, Windows-first)

**STATUS: P0 USER-REPORTED INTEGRATION DEFECT. THIS IS THE FIRST TASK FOR THE NEXT THREAD.**

Repository: [masarray/arpy-gateway](https://github.com/masarray/arpy-gateway). This handoff documents the verified GitHub state and user-observed screenshots on 2026-10-11, not a claim that the reported defect has been fixed. **Do not reimplement the Modbus engine, restart from old `main`, or add another "Saved Devices" page.**

## 1. Product goal and user direction

Build **one coherent, self-contained, low-overhead, reliable Python gateway** with PySide6 Studio; no pip/PowerShell prompts for end users. All engineering fields—instance/bus config, IED/slave endpoints, IEC-104 stations/authorized masters, signals, bindings, mapping—must ultimately be persisted through the canonical database, visible across all Studio workspaces with the same stable IDs. A gateway headless OS service must run independently of Studio; **Save Draft ≠ Validate/Stage ≠ authorized Deploy ≠ Active Runtime**. Windows implementation/build/QA first; defer repeated Linux packaging until Windows is stable.

**Reported by user (three screenshots, 2026-10-11):**

1. Added "Power Meter A" (192.168.1.12:502, Unit ID 1, poll 1000 ms). After closing/reopening Studio it appears only after clicking **Saved Modbus Devices (SQLite)**. This is positive evidence for persisted *draft* IED storage, **not** proof of live/deployed field communication.
2. Normal left **Protocol Explorer → Modbus TCP Master → IED Connections** still shows mock **Meter 01** / **Meter 02**, not the newly saved IED.
3. The main "IED / Slave Connections" grid is also mock Meter 01 / 02, while the SQLite IED is isolated behind a large separate full-width panel. Different buttons/screens show incompatible configuration models. **User rejected this nonstandard architecture** and explicitly requires all engineering fields to bind to the DB, like a normal gateway.

**Root cause verified in source, not a refresh-only bug:**

- `src/arpy_gateway/studio/v2_window.py` constructs `self.project = EngineeringView(demo or DemoProvider())` regardless of the configured `engineering_db_path`; the `ProtocolConfigWorkspace` constructs its original `ConnectionsModel(project,...)` for the tree-linked IED editor **and** separately constructs `SavedDevicesPanel(...)` only reachable by the extra button.
- `src/arpy_gateway/studio/project_view.py` seeds **Meter 01, Meter 02** and other demo peer/station IDs in memory; `add_point`, `update_point`, `add_station`, signal/mapping edits operate on mock collections.
- `src/arpy_gateway/studio/explorer.py` builds tree children using `project.instances()/points()/stations()`, which remain the mock `EngineeringView` source, not SQLite. It also builds workspace-specific signal scopes from mock points.
- `src/arpy_gateway/studio/persisted_devices.py` uses real `EngineeringStoreV2` but hardcodes `_PROJECT = "local-gateway"`, `_INSTANCE = "arpy-modbus-tcp-master"`; this independent owner is not the demo's `meter01` instance. The panel lazily invokes `repo.prepare()` only when `show_saved_devices()` is selected. Its Save code never updates the tree source.
- Existing `tests/test_studio_saved_devices.py` **deliberately asserts demo remains separate** and always clicks `show_saved_devices()` to load SQLite. Therefore previous 35/35 passing Windows tests do not cover, and in fact mask, the user's required single-source-of-truth UX.

**Consequence:** the previous milestone is technically correct only for a separate persisted-draft panel, **not** for the expected standard, database-backed Studio. Do not describe it as resolved until the normal Explorer and editors reflect SQLite on startup.

## 2. Verified GitHub checkpoint and dependency graph

Main remains **`4f71da59f612eb5b75ae06d6b59bebb827cb211d`**. The following PRs remain **OPEN / DRAFT / UNMERGED**. Never assume these sources are already in `main`:

| PR / lane | Branch / exact head | Verified scope |
| --- | --- | --- |
| [#12 GUI](https://github.com/masarray/arpy-gateway/pull/12) | `feat/gui-six-workspace-studio-issue4` / `fcfa19a036529558b899671b7f75dc33cc9476c6` | Six-workspace Studio shell, demo Explorer and virtual catalogs |
| [#20 GUI](https://github.com/masarray/arpy-gateway/pull/20) | `feat/gui-live-ipc-value-viewer-issue17` / `86058789b4841dc4664c13ab73311e3103399154` | Read-only IPC Value Viewer, actual TCP→runtime→Qt CI |
| **[#23 GUI CURRENT](https://github.com/masarray/arpy-gateway/pull/23)** | **`feat/gui-sqlite-device-editor-windows` / `a9143a95578949ea0ef571654d53e588b46dd3c6`** | SQLite device *separate panel*, Save/Edit/Reopen, Windows EXE smoke. **Contains reported P0 UX defect.** |
| [#22 ARCH / RFC #13](https://github.com/masarray/arpy-gateway/pull/22) | `feat/arch13-device-station-v2` / `17c4c764d6a41bc4d73bf62c79accbf4183ef3db` | Proposed v2 CommunicationPoint, DataStation, SignalBinding and strict schema/validation |
| [#21 STORE](https://github.com/masarray/arpy-gateway/pull/21) | `feat/store-revisioned-sqlite-issue3` / `8868f715f27b7eb36fd2740be24493576a27b306` | SQLite WAL v1/v2, explicit migration, CAS Save Draft, Undo/Redo, immutable Stage, indexed pages |
| [#15 MODBUS](https://github.com/masarray/arpy-gateway/pull/15) | `feat/6-modbus-async-engine` / `cb94de62d49bdb3c3151f7cd472311911442f241` | Real TCP/RTU client adapter, optional PyModbus transport, tests |
| [#18 IPC](https://github.com/masarray/arpy-gateway/pull/18) | `feat/8-headless-ipc-lifecycle` / `a3bafc387df685b303baa93bffaa7c2aeaed36ba` | Independent daemon, read-only IPC, bounded cursor/delta |
| [#19 IPC/runtime](https://github.com/masarray/arpy-gateway/pull/19) | `feat/8-modbus-runtime-integration` / `eb926024751bebdf728c6174869a4d31fdcca762` | Modbus→runtime→IPC, multi-instance fleet, device/status pages |

**Stacking:** GUI #12 → #20 → **#23**. Runtime #18 → #19. ARCH #22 and STORE #21 are separate drafts on main. The Windows GUI workflow overlays pinned #22/#21/#19/#15 sources **only in the runner checkout**, not in #23 or main. A standalone EXE artifact created this way **is not proof of a merged/releasable installation tree**.

**Last verified Windows GUI CI at #23 head:** [run 38117023624](https://github.com/masarray/arpy-gateway/actions/runs/38117023624) **SUCCESS**; Foundation Contracts [run 38117023623](https://github.com/masarray/arpy-gateway/actions/runs/38117023623) **SUCCESS**. Previous [run 38116986306](https://github.com/masarray/arpy-gateway/actions/runs/38116986306) **SUCCESS**, including **35/35 Windows GUI tests**, PyInstaller build, and actual portable EXE startup/screenshot smoke. The named preview artifact is `ARPY-Gateway-Studio-Windows-Saved-Devices-Preview`; artifact availability is time-limited. Those passing tests **do not validate database-backed Explorer behavior**. Linux GUI CI was intentionally manual-only, pending Windows stabilization.

## 3. Next-thread P0 task: ONE engineering source of truth, not another panel

**User-visible acceptance definition:** with Studio opened normally, the saved "Power Meter A" appears automatically under **Protocol Explorer → Modbus TCP Master → IED Connections** (and its standard IED grid) **without clicking Saved Modbus Devices**. Selecting it opens its exact stored IP, port, Unit ID, polling, enabled and stable ID. Editing any supported field saves via the canonical SQLite repository; closing/reopening Studio preserves both the tree and editor values. Adding a device uses the existing IED Connections UI and refreshes both views consistently. No mock IED is mistaken for a real stored IED.

**Recommended implementation strategy (GUI lane, no reimplementation of Store):**

1. Replace the persistent-production `EngineeringView(DemoProvider())` source used for normal Explorer/editor operations with **one read-only engineering projection backed by `EngineeringStoreV2`**, refreshed asynchronously via the existing bounded `WorkerHub`. Keep `DemoProvider` **only as an explicitly labeled QA/demo mode**, not a silent fallback that looks like saved configuration. Startup must hydrate project instances, communication points and stations automatically. UI should report LOADING/EMPTY/ERROR distinctly and avoid blocking the Qt event thread.
2. Build **one stable-ID based, per-project/per-instance indexed view-model** consumed by `ProtocolExplorer`, `ConnectionsModel`, General Settings, Stations, Signal Catalog, Mapping and editor selection. Do not copy/synchronize two mutable repositories, infer ownership from host/address/row order, or simply append SQLite rows to mock data. For large catalogs, use keyset pages and lazy scoped retrieval; avoid building Qt widgets for every signal.
3. Route **Add/Edit/Delete IED** and all future configuration field changes through transactional `EngineeringStoreV2.apply_v2(... expected_generation=...)`. On save success, invalidate only impacted model/tree/index pages and preserve selection by canonical `CommunicationPoint.id`. On CAS conflict, reload and inform the user (no silent overwrite). Reuse `communication_point_page`, `data_station_page`, `load_draft_v2` for appropriate-sized views; add narrowly scoped Store-lane read APIs if missing (separate coordinated PR, no direct GUI SQL).
4. Do not force a single fixed `_INSTANCE` or local project across all protocol instances. Use the **current selected canonical instance ID** and persist it. Schema migration v1→v2 remains explicit after backup and ARCH review. The old demo instance `meter01` must not be quietly equated with `arpy-modbus-tcp-master`.
5. Maintain explicit engineering labels: **Saved Draft / Validated & Staged / Deployed / Runtime Active**, with valid revision identity. Do not auto-connect to IED when saving, nor claim `Good` or `Online` without real runtime telemetry. A Stage action on device-only project without signal binding must be rejected clearly, not hidden.
6. Remove/hide the redundant "Saved Modbus Devices (SQLite)" navigation once the normal hierarchy is correctly DB-backed. Do this **after** migration of the stored IED records and end-to-end regression, so the user's existing `%LOCALAPPDATA%/ARPY/Gateway Studio/engineering.sqlite3` remains intact.

**Do not pretend "all fields are bound" until tested.** Prioritize canonical instances + TCP IEDs + tree first. Then add persistent signal table/field edits, Device→SignalBinding, IEC104 stations and mappings, each with the same Store-backed source. A single writable domain model for *all* engineering entities is the acceptance target, but it can be delivered in tested Windows-first increments.

## 4. Required regression tests — change the assertions that hid the defect

Extend `tests/test_studio_saved_devices.py` and/or introduce a focused GUI regression that:

- Opens the **real existing SQLite file** (seeded through `EngineeringStoreV2`), launches a normal `StudioV2Window(engineering_db_path=...)`, waits for asynchronous hydration, then asserts **the persisted IED exists in `tree_model` under IED Connections** without invoking `show_saved_devices()`.
- Selects that tree node and asserts the normal connection editor and IED grid show exactly the persisted field values and stable ID.
- Adds/edits via the **normal IED Connections UI**, waits for Save complete and tree/grid update, then closes/reopens to verify exact identity and new values.
- Verifies changing display name/IP/poll does not change ID, and signals or mapping referencing the ID remain valid; changing selection/workspace must not display another IED's stale values.
- Verifies no duplicate mock `Meter 01`/`Meter 02` in the **normal production mode**; explicit demo mode continues to work only when selected for QA.
- Verifies CAS conflicts, wrong-v1 schema gate, invalid field rejection, empty DB, permission/lock failures and 128/256-row keyset navigation; no Qt thread synchronous SQLite calls.
- Verifies Save **does not** mutate active runtime, initiate Modbus connection, falsely mark GOOD or silently run Stage/Deploy.
- Keeps existing **actual Modbus TCP→separate daemon→IPC→Qt read-only** tests passing, while recognizing that live value attachment to persisted signals is a later, separately secured revision/IPC step.
- Windows GUI CI + **standalone EXE startup screenshot** must both pass after the change; Windows-first only. Linux build/benchmark is final gate by user preference.

This is an **acceptance criterion update**, not a claim these tests currently pass.

## 5. Integration / review boundaries and next milestones

- Read [AGENTS.md](../../AGENTS.md), [docs/DELIVERY.md](../DELIVERY.md), [docs/CONTRACTS.md](../CONTRACTS.md), [ARCH proposed ADR-0004](https://github.com/masarray/arpy-gateway/blob/feat/arch13-device-station-v2/docs/ADR/0004-communication-point-datastation-v2-proposed.md) and issue [#17](https://github.com/masarray/arpy-gateway/issues/17).
- First continue **GUI PR #23 / branch `feat/gui-sqlite-device-editor-windows`** to fix this P0 inconsistency, with CI and user-visible proof, rather than making yet another separate SQLite device page/PR. Coordinate with GUI owner; no simultaneous edits to identical files.
- **ARCH #13 is OPEN:** v2 DTO/schema is a proposed candidate, not an accepted production contract. **STORE #21 is DRAFT:** do not invent competing SQL tables, do not merge other-lane sources into GUI and do not silently migrate existing DB. Use pinned CI overlays until proper coordinated review/merge sequence.
- After single-source engineering UI is verified, progress to persisted Signals/Bindings and mapping → immutable validated revision → **authenticated IPC authorized Deploy with CAS/idempotency/audit/last-known-good/rollback** (IPC #8/#19) → connected live Value Viewer by stable revision-bound IDs → actual Windows SCM standalone gateway service + offline bundled Modbus dependency and installer. Physical serial/IED and Linux acceptance last. HA redundancy (#16) remains a separate RFC; no premature split-brain claims.
- Maintain independent gateway runtime after Studio closes. GUI read-only telemetry IPC **must never** carry ordered IEC-104 spontaneous/command data.
- Preserve privacy: repo docs must not contain user's machine-specific secrets, token, absolute local paths or binary screenshot uploads. Screenshots are described as user observations; preserve actual local SQLite data during upgrades.

## 6. Ready-to-paste prompt for a new ChatGPT thread

> Open `masarray/arpy-gateway`. Start with `docs/handoffs/2026-10-11-studio-single-source-of-truth.md` and the USER-REPORTED P0 issue. Verify current `main` and PR #23 HEAD before editing. Continue Windows-first GUI PR #23, **do not restart from main or create another Saved Devices panel**. Eliminate the contradictory DemoProvider/EngineeringView vs SQLite IED data sources: normal Explorer → Modbus TCP Master → IED Connections and its editor must read/write `EngineeringStoreV2` and show the persisted Power Meter A automatically on startup/reopen, with stable ID, transactional Save, CAS, full field binding and no duplicate mock IEDs. Preserve old DB, no auto-Deploy and no fabricated live values. Write Windows Qt integration tests that fail before fix, then implement on bounded WorkerHub and prove tests plus portable EXE smoke. Keep ARCH #22/STORE #21/IPC #19 dependencies as pinned, separate draft lanes. Report exact commits/CI and remaining blockers. Linux final only.

## 7. Definition of done for THIS handoff task

- [x] Verified all relevant PRs, heads and `main` status from GitHub.
- [x] Inspected code determining true source of discrepancy (not a mere refresh glitch).
- [x] Documented user-observed bug and concrete new-thread test cases.
- [x] Committed handoff to current GUI branch and linked issue/PR (linkage must be checked in subsequent tool calls).
- [ ] **P0 UI fix itself is NOT implemented in this handoff.** A new thread will deliver and test it.

