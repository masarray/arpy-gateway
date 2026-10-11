# ARPY Engineering Store — normalized SQLite v1 (issue #3)

**Status:** persistent engineering draft with durable journaling and immutable staging, **not** runtime deployment or production Add IED yet.

Uses existing accepted `ProjectSnapshot`, `ProtocolInstance`, `ProtocolSignal`, `MappingDefinition` DTOs and canonical JSON codec. Tables: `projects`, `instances`, `signals`, `mappings`, `edit_journal`, `edit_journal_rows`, `revisions`; foreign keys, protocol/owner-scoped indexes, SQLite WAL and explicit version check. It is forbidden to infer signal identity from address/row position.

**Key operations:** `create_project`, `apply(...,expected_generation=N)` (transactional 500-record batch UPSERT, all-or-nothing with FK and CAS), `undo/redo` (durable after app restart), `signal_page(instance_id, after_id, limit<=256)` (indexed keyset, O(page size) rather than OFFSET traversal), `load_draft` (worker-only full snapshot for export), `stage(expected_generation=N)` (canonical validation + immutable content-digest compressed revision), `revision(digest)` (verified historical snapshot). Save **never** deploys. Versioned revision SHA-256 is an integrity identifier, not signature or approval. Default max two independent SQLite readers and single serialized writer.

**ARCH #13 requirement:** v1 does not yet define CommunicationPoint or DataStation; no guessed database ownership schema is introduced. This store rejects foreign-key-invalid ownership but allows incomplete *attribute*-level drafts (e.g. empty signal name) that validation will block from staging. User-facing Add IED + Save + Deploy requires approved ARCH RFC #13 schema/migration. GUI must call store only from existing bounded WorkerHub, runtime must consume a **separately authorized immutable revision** over protected IPC, never share Studio SQLite handles.

CI runs on Windows/Linux Python 3.10/3.12 and large 100k/500k fixtures. Hardware, installed OS service, real Save→Deploy, secure permissions and 500k Qt FPS are **not** proven by Store CI.
