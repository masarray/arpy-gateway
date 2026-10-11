# ARPY Engineering Store — normalized SQLite v1 (issue #3)

**Status:** persistent engineering draft with durable journaling and immutable staging, **not** runtime deployment or production Add IED yet.

Uses existing accepted `ProjectSnapshot`, `ProtocolInstance`, `ProtocolSignal`, `MappingDefinition` DTOs and canonical JSON codec. Tables: `projects`, `instances`, `signals`, `mappings`, `edit_journal`, `edit_journal_rows`, `revisions`; foreign keys, protocol/owner-scoped indexes, SQLite WAL and explicit version check. It is forbidden to infer signal identity from address/row position.

**Key operations:** `create_project`, `apply(...,expected_generation=N)` (transactional 500-record batch UPSERT, all-or-nothing with FK and CAS), `undo/redo` (durable after app restart), `signal_page(instance_id, after_id, limit<=256)` (indexed keyset, O(page size) rather than OFFSET traversal), `load_draft` (worker-only full snapshot for export), `stage(expected_generation=N)` (canonical validation + immutable content-digest compressed revision), `revision(digest)` (verified historical snapshot). Save **never** deploys. Versioned revision SHA-256 is an integrity identifier, not signature or approval. Default max two independent SQLite readers and single serialized writer.

**ARCH #13 requirement:** v1 does not yet define CommunicationPoint or DataStation; no guessed database ownership schema is introduced. This store rejects foreign-key-invalid ownership but allows incomplete *attribute*-level drafts (e.g. empty signal name) that validation will block from staging. User-facing Add IED + Save + Deploy requires approved ARCH RFC #13 schema/migration. GUI must call store only from existing bounded WorkerHub, runtime must consume a **separately authorized immutable revision** over protected IPC, never share Studio SQLite handles.

CI runs on Windows/Linux Python 3.10/3.12 and large 100k/500k fixtures. Hardware, installed OS service, real Save→Deploy, secure permissions and 500k Qt FPS are **not** proven by Store CI.

## Proposed RFC #13 Store v2 (Windows-first, NOT activated)

`engineering/store_v2.py` adds an **explicit** `migrate_v2()` operation; no silent database migration. It upgrades the v1 SQLite file additively inside one transaction, preserving all original IDs, signal records, mapping edges and journal history. New tables are `communication_points`, `data_stations`, `signal_bindings`, `edit_journal_rows_v2`, and `revisions_v2`, with composite project-scoped foreign keys and owner-scoped indexes. Opening a v1 file never automatically triggers migration; a production installer must back up before approved upgrade.

`apply_v2(...,expected_generation=N)` saves **instance + IED/Slave + IEC104 station + signal + binding + mapping in one atomic SQLite transaction**, including durable shared Undo/Redo journal, optimistic generation CAS, and complete rollback on FK or validation errors. `load_draft_v2` reopens full immutable DTOs for worker-side validation. `communication_point_page` and `data_station_page` use indexed stable-ID keyset pages (limit ≤256) for future GUI Device/Station Explorers. `stage_v2` runs proposed ARCH #13 validation, freezes a digest-verified immutable v2 revision and **never deploys** it; calling inherited `stage()` against an upgraded v2 DB deliberately rejects incomplete v1-only staging.

**Dependencies:** experimental RFC candidate [ARCH PR #22](https://github.com/masarray/arpy-gateway/pull/22) remains DRAFT, therefore these new v2 Store APIs are also *review candidates*. The dedicated Windows v2 CI gate temporarily overlays the exact candidate ARCH commit `17c4c764d6a41bc4d73bf62c79accbf4183ef3db` only in the test checkout. No cross-lane source is merged or silently copied into this Store branch.

**Still missing:** approved schema/backup UX, GUI WorkerHub Save Device binding, secured runtime Stage→Deploy CAS/LKG/rollback, Windows native OS service installer, and actual hardware interoperability. Windows-first is the current priority; Linux final release validation follows approval.
