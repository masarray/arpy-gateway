# Engineering references, decisions and unanswered platform questions

**Status: researched non-normative rationale, October 2026.** This file separates documented external behavior from *our proposed ARPY design*. Third-party products demonstrate patterns, not guaranteed applicability or implementation in ARPY. Do not copy proprietary code, protocols or Siemens UX artwork.

## Evidence vs proposed ARPY decisions

| Source-backed practice | What source actually supports | ARPY implication — DESIGN PROPOSAL |
| --- | --- | --- |
| Ignition Gateway (Inductive Automation 8.3) | Gateway starts as a service; OS mechanisms start/stop it on Windows and Linux; separate Designer launcher. | Headless service should be independent of engineering GUI; versioned attach/detach. This does **not** prove ARPY implementation exists. |
| Windows SCM (Microsoft) | Services can start automatically and run when nobody is logged in; services are noninteractive. | Ship a real SCM-aware headless service, not a task scheduler GUI or tray icon. |
| Windows service access security | SCM/service rights and DACL determine who can configure/start/stop it; accounts have separate privilege contexts. | Use restricted service identity and explicit IPC security, not blanket LocalSystem. |
| systemd | Service lifecycle/restarts and execution sandbox/directory directives are available on supported systemd releases. | Ship tested Linux **system** service; watchdog only if heartbeat implemented. |
| NIST SP 800-82 Rev.3 Final (2023) | OT security design balances availability, performance, safety, access control and threat exposure. | Segmented field networks; no unauthenticated admin endpoint; change impact + audit. |
| ARPY accepted ADR-0001/0002/0003 | Separate processes; stable signal identity/mappings; no implicit apply/simulation side effects. | Treat GUI review as independent from runtime readiness; restore last deployed snapshot after reboot. |

## Verified references (primary documentation)

1. [Ignition Gateway 8.3 — starting/stopping the service on Windows/Linux, Designer launcher](https://www.docs.inductiveautomation.com/docs/8.3/platform/gateway). Product documentation; ARPY is neither affiliated with nor compatible by assumption.
2. [Microsoft Win32 Services overview](https://learn.microsoft.com/en-us/windows/win32/services/services) and [interactive service caveats](https://learn.microsoft.com/en-us/windows/win32/services/interactive-services).
3. [Microsoft Windows service security/access rights](https://learn.microsoft.com/en-us/windows/win32/services/service-security-and-access-rights); [service accounts](https://learn.microsoft.com/en-us/windows/win32/services/service-user-accounts).
4. [systemd.service](https://www.freedesktop.org/software/systemd/man/latest/systemd.service.html) and [systemd.exec](https://www.freedesktop.org/software/systemd/man/latest/systemd.exec.html). Match deployment target systemd version and distro, do not assume all directives work identically everywhere.
5. [NIST SP 800-82 Rev.3 final announcement](https://csrc.nist.gov/news/2023/nist-publishes-sp-800-82-revision-3) and [NIST OT publications index](https://csrc.nist.gov/Projects/operational-technology-security/publications). NIST also lists SP 800-82 Rev.4 **Draft** (2026); don't present that draft as a final required standard.
6. Accepted ARPY `docs/ADR/0001-separated-runtime-and-studio.md`, `0002-signal-identity-and-mapping.md`, `0003-engineering-apply-and-simulation.md`.

## Decision register — unresolved; do not quietly choose in production code

| ID | Topic | Options / recommendation | Authority / required experiment |
| --- | --- | --- | --- |
| DEC-01 | Local IPC Linux | Unix socket + `SO_PEERCRED` / ACL vs loopback TLS. Prefer UDS for same-machine v1. | IPC #8 + security review; permission/race tests |
| DEC-02 | Local IPC Windows | DACL-restricted named pipe vs authenticated loopback. Evaluate supported Python packaging + peer identity. | IPC #8; impersonation/replay tests |
| DEC-03 | IPC encoding/framing | Length-prefixed JSON v1 candidate; binary/Protobuf later only if measured need. | ARCH #11 + IPC #8; max frame/depth/schema fuzz tests |
| DEC-04 | Source snapshot repository | Single immutable verified package/read model, journal under service-owned state; edit DB is separate. | Store #3 + ARCH #11/#13; power-loss test |
| DEC-05 | Connection & station schema | Separate peer connection vs local IEC104 CA data station; RFC #13 still open. | ARCH #13 approval + migrations |
| DEC-06 | Windows service host | pywin32-aware SCM implementation vs native service wrapper; do not assume GUI PyInstaller build = service. | IPC/platform packaging #8; cold boot/SCM QA |
| DEC-07 | systemd readiness/watchdog | Type=exec initially; Type=notify + WatchdogSec only with real heartbeat. | IPC #8 + QA #10 |
| DEC-08 | Python Modbus/IEC104 libraries | Evaluate protocol conformance, GIL model, support, license (including copyleft), Windows/Linux wheels. | Adapter owners #6/#7 + license review |
| DEC-09 | Hot apply/restart scope | Per-instance activate where supported, otherwise planned socket/service restart with impact preview. | IPC #8 + adapters #6/#7 + QA #10 |
| DEC-10 | Trusted clock semantics | distinguish source timestamp, receive UTC, monotonic age; freeze policy for clock jumps. | ARCH #11 + protocols + QA |
| DEC-11 | High availability | single node first; multi-node redundancy, active/standby fencing and duplicate-master protection later. | Future architecture RFC + real field topology tests |
| DEC-12 | Runtime simulation survival | default runtime-owned TTL/owner, never infer stop-on-GUI-close; no uncontrolled persistent simulation. | SIM #9 + safety review |
| DEC-13 | Windows/Linux artifact targets | Windows x64 + tested Linux x86-64 initially; ARM64 only after library and serial tests. | Platform QA #10 |
| DEC-14 | Admin remote access | disabled by default; any remote engineering plane needs TLS, identity, role isolation and OT review. | Future security RFC |

## Critical pitfalls prevented by this register

- **No naive cross-platform claim:** same Python modules run on both systems, but service hosts, permissions, dependencies, hardware serial port and packages are OS-specific and require platform tests.
- **No naive performance claim:** 500k engineering rows ≠ 500k samples/second. Device scan groups and mapping event budget must be separately measured.
- **No fake hard real-time:** CPython + generic OS scheduling is not a deterministic field I/O system. Claim soft real-time only under measured envelope; deterministic deadlines/control require architecture/hardware fit analysis.
- **No naive quality mapping:** IEC104 COT, TI, timestamp and quality are protocol semantics, not generic property labels applied identically to Modbus.
- **No naive failover:** a restarted single gateway has an outage window; high availability requires redundancy, fencing, conflict resolution and SCADA endpoint strategy.
- **No implicit physical control:** UI, IPC and simulation cannot emit a physical control command merely because a value changed.

For executable planning use `docs/implementation/SERVICE_DELIVERY_HANDOFF.md`; for release proof use `docs/quality/SERVICE_ACCEPTANCE_MATRIX.md`.
