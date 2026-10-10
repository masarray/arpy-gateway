# ARPY Gateway — Mandatory contributor contract

This file is the entry point for **every** human/AI development thread. Read before editing.

## Source of truth (ordered)
1. `docs/CONTRACTS.md` and `docs/ARCHITECTURE.md` — normative domain and runtime boundaries.
2. `docs/WORKSPACES.md` — user-approved six-workspace UX.
3. `docs/PERFORMANCE.md` — mandatory scalability and concurrency acceptance.
4. `docs/DELIVERY.md` — isolation, handoffs, PR and CI gates.
5. `docs/ADR/` — accepted architectural decisions.

Existing `feat/high-performance-signal-editor` (PR #1) is a **GUI technology preview**, NOT the architecture authority. Do not treat its flat source/target table as canonical domain storage.

## Non-negotiable invariants
- Python 3.10+; pure domain contracts **never** import Qt, sockets or protocol libraries.
- Studio GUI is a separate **process** from headless Gateway Runtime. Closing or crashing GUI never stops runtime.
- Qt UI thread may only present data; no SQLite, blocking network, device I/O, bulk validation or compilation on UI thread.
- Bounded queues/cache, cancellation and stale-generation protection. No per-cell QWidget, no full-table materialization, no busy loops, no unbounded event/history buffering.
- Identity: Project > Protocol Instance > Protocol Signal; assignments are separate many-to-many Mapping records. No address-as-primary-key.
- All fieldbus outputs / control commands disabled until explicitly authorized. Simulation is **opt-in**, labelled, bounded and blocked from physical controls by default.
- Engineering Draft != Validated Revision != Deployed Runtime. A save never deploys. Deploy requires validation + diff + explicit apply + rollback plan.
- Device-specific protocol fields belong in adapter-owned metadata/schema; no giant union with Modbus fields on IEC-104 signals.
- No direct UI-to-adapter imports, no cross-workspace database mutations, no new dependencies without license/platform/benchmark review.
- Error/quality/timestamp must never be silently overwritten with `Good`, `now` or numeric zero when communication is lost.
- Never claim functional Modbus/IEC-104 operation until hardware/interop tests demonstrate it.

## Parallel development rule
Pick exactly one ownership lane in `docs/DELIVERY.md` and link its GitHub issue. New work MUST branch from current `main` (after contract baseline) and change only owned code, corresponding tests and non-normative docs. Changes to `domain`, `contracts`, `ipc` or versioned schema require an architecture RFC issue first. Do not cherry-pick experimental GUI work into runtime.

PR checklist: issue ID; path ownership; contract revision; performance budgets; tests; failure/rollback evidence; actual vs unverified claims; screenshots for GUI changes. Missing evidence => remain draft.

## Current status
This baseline is an architecture + executable *contract skeleton*, not an industrial gateway release. Protocol adapters, transport, SQLite migrations, and the six GUI workspaces must be implemented in independently reviewed PRs.
