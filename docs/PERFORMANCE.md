# Non-naive engineering/performance contract

Targets are **budgets to verify**, not benchmarks already achieved. A feature which does not demonstrate correctness under concurrency and large data must remain draft.

## Hard engineering rules
1. Avoid full-memory copies for every visible render/filter; paged model and LRU cache with quantified memory ceiling.
2. No blocking SQLite/network/validation/file I/O in Qt main thread. Separate ordered write worker; bounded parallel read workers with dedicated SQLite connections; cancellation and view-generation token invalidate old responses.
3. Avoid one signal widget or DB connection per point. No synchronous 100k-cell loops emitting one Qt signal per cell.
4. Enforce a write queue limit, batching, atomic transactions, stable row IDs, schema-validated writes and durable error paths.
5. Runtime adapter tasks isolated by protocol instance; bounded protocol request queues, backoff with jitter, per-session deadlines, context-aware LAN recovery, clean stop when disabled. Do not poll when no enabled clients.
6. Telemetry updates coalesced by signal ID and viewport, max 5–10 refreshes/s to GUI by default; runtime acquisition timestamps and processing rate remain independent.
7. Use incremental validation indexed by affected scopes; full validation prior deploy; O(N+E) graph checks; address uniqueness per adapter scope (native index). No quadratic nested comparisons or giant string joins.
8. Structured logs circular retention and redact secrets. Bound simulation rates, sample/event buffers and diagnostic fanout.
9. Async worker safety: no Qt object access from worker, no unsynchronized SQLite connections, no stale edits after selection/sort/filter change.
10. Separate runtime crash/restart from Studio; health/timeouts and fail-state quality propagation never depend on GUI heartbeat.

## Proposed machine profile / benchmark harness
Windows 11 x64, 4–8 logical cores, 16 GB RAM, SSD, normal engineering desktop; Linux equivalent. Capture machine/OS/SQLite/Python/Qt versions, warm/cold cache, dataset seed and run command. CI storage benchmarks are not GUI FPS.

| Scenario | Candidate acceptance budget (to measure) |
| --- | --- |
| Project 100,000 signals | scroll visually smooth; 95th percentile input-to-render < 100 ms |
| Project 500,000 signals | bounded cache, memory steady-state; no UI blocking > 150 ms |
| Search by indexed exact field | p95 < 200 ms warm on 100k, report cold separately |
| Bulk paste 10,000 cells | atomic, cancellable before commit, p95 < 2 s on reference hardware |
| Auto-number 100,000 points | worker-bound, p95 < 5 s with collision validation |
| 100k graph mappings | O(V+E) validation; completion < 2 s in benchmark hardware |
| 10k live changing values/s | bounded coalescing, no GUI full refresh, no uncontrolled backlog |
| GUI close/restart while runtime active | zero protocol instance disconnects attributable to GUI |

These are provisional targets, tune only using reproducible measurement and sign-off. Never reject correctness to meet superficial speed budget.

## Test layers
- `test_contracts` pure stdlib, no Qt/adapters.
- `test_store` SQLite migration, 100k/500k rows, transaction atomicity, sorted selections and cancel race.
- `test_gui` Qt offscreen smoke and desktop integration: key shortcuts/clipboard/focus/stale generation.
- `test_runtime` fake adapter and deterministic virtual clock: reconnect, command safety, backpressure, simulation ownership/TTL.
- `test_adapter_modbus`/`iec104` protocol conformance with simulator and packet traces; actual interoperability/hardware bench before claiming protocol readiness.
- `test_failure` LAN yank/reconnect, partial frames, invalid payload, retries, disabled all instances, abrupt GUI crash.
- Native package smoke on Windows/Linux; code-signing separately planned, not promised.

Every performance PR must attach before/after measurements and environment, not an adjective like 'optimized'.
