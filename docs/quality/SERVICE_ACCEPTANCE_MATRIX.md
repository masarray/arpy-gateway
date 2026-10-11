# Industrial Gateway Service — test and evidence matrix

**Status: PROPOSED QA plan; none of these integration tests are claimed passed.** Owners QA issue #10, service/IPC #8, Store #3, Mapping #5, protocols #6/#7 and SIM #9. Link test logs, captures, machine metadata and commit SHA in each issue/PR. A screenshot, synthetic table benchmark, or green packaging build is **not** evidence of working Modbus/IEC-104 gateway communication.

## Evidence taxonomy and gates

- **C0 — contract-only:** pure Python schemas/fixtures, failure examples. No runtime functionality implied.
- **C1 — deterministic runtime unit:** fake adapters/clock/local IPC, controlled failure injection; verify state machine and safety.
- **C2 — OS service integration:** native Windows SCM/systemd in VM; reboot/login/logout/kill/privilege/upgrade checks.
- **C3 — independent protocol interop:** external Modbus/IEC104 client/server, packet trace and semantic assertions.
- **C4 — field acceptance:** physical IED/SCADA and sustained realistic traffic, signed operator test plan.
- **C5 — operational release gate:** packaging, security, backup/rollback, load and alarms passed on documented target OS.

To call a milestone `Implemented` require its relevant C1-C3 evidence; `Field-verified` needs C4. Passing C1 alone does not qualify the system as production gateway.

## Mandatory acceptance cases

| ID | Layer / issue | Procedure (minimum) | Pass criteria | Evidence |
| --- | --- | --- | --- | --- |
| LIFE-01 | C1/C2 #8/#10 | Start service with no Studio process | Runtime alive; `UNCONFIGURED` if no deployed revision; no field output guessed | service PID + health + active digest |
| LIFE-02 | C2 #8/#10 | Connect Modbus/IEC104 fake peers; gracefully close Studio | All runtime adapter session IDs and sample sequence continue; 0 resets caused by Studio | timeline, independent client trace |
| LIFE-03 | C2 | Force-kill Studio / kill PySide6 at arbitrary point | Protocol I/O continues; service PID unchanged | process tree, counters |
| LIFE-04 | C2 | Logout Windows user or Linux desktop session | Runtime remains service-managed, no dependency on `systemd --user` | SCM/systemd log |
| LIFE-05 | C2 | Reboot machine with deployed revision | Automatic startup with verified digest; reconnect and truthful quality/age | boot log, revision hash |
| LIFE-06 | C1/C2 | Kill runtime, observe service restart | Recovery policy operates; sessions may interrupt; no uncommitted draft deployed | before/after status |
| LIFE-07 | C2 | Stop service intentionally | Graceful deadlines, ports released, process stays stopped (not auto-restarted as crash) | OS manager state, socket checks |
| LIFE-08 | C1 | Startup after corrupted active snapshot | Safe stopped/degraded/unconfigured data plane; explicit error, no guessed config | journal and alarm |
| IPC-01 | C1/C2 #8 | Negotiate `1.x` and incompatible major version | supported case works, major mismatch rejects mutations | captured envelopes |
| IPC-02 | C1 | Send oversized/partial/truncated/invalid UTF-8/unknown-key frames | bounded memory/time, structured failure, no executor crash | fuzz logs and RSS |
| IPC-03 | C1/C2 | Read-only principal attempts apply/rollback/simulate | denied and audited; no state change | ACL/token tests |
| IPC-04 | C1 | Two deploy attempts with same expected active revision | CAS accepts at most one, other explicit conflict | operation ledger |
| IPC-05 | C1 | Replay same idempotency key and different payload | same payload yields same result; different payload rejected; no duplicate apply | durable journal |
| IPC-06 | C1 | Slow telemetry subscriber and 10k updates/s | bounded server queue; coalesce/drop count and gap/resync; protocol data path unaffected | queue depth, seq continuity |
| IPC-07 | C1 | Disconnect Studio mid async deploy; reconnect | operation continues to bounded outcome independent of socket; client rehydrates status | operation ID trace |
| CFG-01 | C1/C2 #3/#8 | Save invalid draft while runtime online | no impact on active revision, protocol sessions or published data | before/after digest |
| CFG-02 | C1/C2 | Wrong mapping/address conflict at stage | stage rejected, active rev intact | diff + diagnostics |
| CFG-03 | C1/C2 | Force-crash at each activation journal boundary | last committed snapshot or explicit safe-stop; never half-applied unknown state | fault-injection matrix |
| CFG-04 | C1/C2 | Roll back revision needing endpoint restart | compare impact shown; previous valid digest restored; outage recorded | service/revision logs |
| CFG-05 | C1/C2 | Upgrade and re-open project with version mismatch | migration refuses destructive path; backup/recovery tested | snapshots and hashes |
| PROT-01 | C1/C3 #6 | Modbus TCP Master reads mixed Coil/Input/Holding ranges | grouping respects FC, Unit ID, max quantities, decode widths/endian | independent slave + PCAP |
| PROT-02 | C1/C3 #6 | RTU serial failures: unplug/replug, timeouts, noise | no busy loop, bus serialization, bounded retry; only affected signals stale | RTU capture/metrics |
| PROT-03 | C3 #6 | Modbus Server read/write register mapping, zero-based PDU | stable scoped addresses; unauthorized writes rejected | external master capture |
| PROT-04 | C1/C3 #7 | IEC104 Server accepts two SCADA masters for one CA | peer connection distinct from local station signal namespace | two client traces |
| PROT-05 | C3 #7 | IEC104 GI + spontaneous + reconnect | correct TI/COT/quality, window/sequence timers, no hidden polling-only substitute | external SCADA client PCAP |
| PROT-06 | C3 #7 | TI 35 timestamped measured vs TI 45 single command | correct type categories/CP56Time2a and command authorization | positive/negative traces |
| PROT-07 | C1/C3 | Stale/invalid and wall clock skew/jump | timestamps not invented; monotonic age preserved; quality policy visible | injected clock/link trace |
| MAP-01 | C1 #5 | 100k signal/edge graph with fan-out | deterministic O(V+E) compile, stable edges under sort/filter | benchmark details |
| MAP-02 | C1 | Multiple writers, loop, type mismatch, bad IOA | reject or explicit arbitration; no "first writer wins" | negative fixtures |
| MAP-03 | C3 | Modbus source → mapped IEC104 Server → remote SCADA | actual values, quality and timestamp semantics match mapping | both adapter capture ends |
| SIM-01 | C1/C3 #9 | No source device, inject manual simulated value | real downstream adapter transmits, independent SCADA observes origin/quality policy | IPC audit + protocol trace |
| SIM-02 | C1 | TTL expires / GUI closes / runtime restarts | predictable safe stop or expiry; no lingering physical output | provenance/clock trace |
| SIM-03 | C1/C3 | Simulation attempts physical control route | default reject, explicit audit, no transmitted unsafe command | negative PCAP |
| SCALE-01 | C1/C2 #10 | 100k/500k configured signals | page/cache bounded, memory slope documented, no 500k Qt objects in runtime | RSS/CPU/handles |
| SCALE-02 | C1/C2 | 10k changed values/s and 100k mapping edges | bounded queues, p50/p95/p99, no GUI subscriber backpressure onto adapters | pipeline counters |
| SEC-01 | C2 #8/#10 | unprivileged user accesses local IPC socket/pipe | denied for privileged actions; peer auth proven | ACL/peer creds checks |
| SEC-02 | C2 | install/upgrade/uninstall on both OS, secrets in log | least privilege, no unmanaged remote admin listener, no sensitive log material | install audit |
| OPS-01 | C2/C4 | LAN unplug/replug, peer server restarts, DNS down | per-instance isolation, bounded reconnect, clear alarm and recovery | long-run log |
| OPS-02 | C2/C4 | sustained soak (duration agreed with field owner) | no monotonic RSS/handles/thread growth, stable latency and reliable recoveries | environment + dashboards |

## Performance budgets (hypotheses, not measured results)

Reuse the current targets in `docs/PERFORMANCE.md` **without presenting them as achieved**:
- 100k signals: interactive p95 input-to-render <100 ms on reference engineering desktop;
- 500k signals: bounded cache, no Qt main-thread blocking >150 ms;
- indexed search p95 <200 ms warm cache (report cold separately);
- 10k-cell paste p95 <2 s; 100k point auto-number <5 s; mapping 100k graph <2 s **candidate budgets**;
- 10k value updates/s: bounded monitoring coalescence with no backlog growth;
- GUI close/restart: **zero gateway connection drops caused by GUI process exit**;
- runtime protocol throughput and latency **must be specified by workload** (number IEDs/SCAN interval, TI/COT, poll mixed ranges, expected SCADA peers); don't invent "100k signals/s" guarantee from count alone.

Measure warm/cold state, network loss, CPU saturation, disk-full, subscriber slow-path. Report each OS & architecture separately. Cross-process signal queue throughput and long-lived session stability are different from table scrolling FPS.

## Test harness recommended layout (owned by QA lane)

```text
tests/unit/test_runtime_lifecycle.py       (fake clocks/sockets)
tests/unit/test_revision_activation.py
tests/unit/test_ipc_security.py
tests/unit/test_protocol_semantics.py
tests/integration/test_gui_detachment.py
tests/integration/test_windows_scm.py        (Windows runner/VM)
tests/integration/test_linux_systemd.py      (privileged VM)
tests/integration/test_modbus_to_iec104.py
tests/integration/test_failure_injection.py
benchmarks/runtime_samples.py
benchmarks/mapping_graph.py
artifacts/reports/{commit}/{os}/{environment}/
```

Paths here are **target layout**, not files that already exist. Test layers must not import optional PySide6 in runtime-only tests. CI can perform deterministic unit and headless integration on every PR; service installation, reboot/logout and hardware interop may require secured dedicated test runners. Do not fake an OS service with an ordinary background command and claim LIFE-04/05 satisfied.

## Recording evidence / release checklist

Every case must track `NOT RUN / PASS / FAIL / BLOCKED`; never default PASS. Evidence record contains test ID, git SHA/PR, contract version, OS/build, CPU/RAM, Python & protocol library versions, exact device/simulator model, topology diagram, date/time, pcap/log links, per-IED counts, retries, disconnects, elapsed, pass/fail rationale and tester. **Any unresolved fail-closed, unauthorized command or revision recovery test blocks production release.**

See `docs/implementation/SERVICE_DELIVERY_HANDOFF.md` and `docs/operations/OPERATIONS_RUNBOOK.md`.
