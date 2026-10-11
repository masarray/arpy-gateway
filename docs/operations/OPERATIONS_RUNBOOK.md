# ARPY Gateway Operations Runbook — Windows / Linux

**Status: PRE-IMPLEMENTATION RUNBOOK. No production service or CLI is presently shipped on `main`.** Commands below are **examples for future installations**; do not run them against an uninstalled gateway. Adapt service name, account, paths and privileges after platform packaging approval.

## 1. Separation of duties

- Gateway Runtime: OS-managed service; owns connections, active immutable revision, quality/time/provenance, durable recovery journal and audit.
- Studio: interactive engineering/monitoring client. Closing it never stops runtime. Save edits to draft; validate/stage/deploy explicitly.
- Operator: observes health, alarms and diagnostics; no default write permissions. Engineering/Deployer roles must be distinct from Service Administrator.
- Deployment owner: coordinates protocol-impacting changes; confirms maintenance window and rollback path.
- Safety/OT network owner: approves field endpoints, allowed peers, control/simulation policy, firewall/security plan.

## 2. Normal first boot

1. Verify installer completed with compatible platform artifact and least-privilege service account; log installer version/digest and service registration.
2. Start service (or verify automatic startup). **Without an active deployed revision**, health must show `UNCONFIGURED`, no spontaneous outputs, no guessed communication endpoints. This is a safe state, not evidence of a broken GUI.
3. Studio connects to local authenticated IPC and performs version/feature handshake. Studio may be installed later, closed, or operated from another session **without** reinitializing runtime sessions.
4. Engineer opens/creates draft, defines protocol instance → connection / ASDU station → signals → typed mapping, validates; licensed/permitted protocol libraries and adapter capabilities must be present.
5. Stage and review diff and outage class. Authorized deployer explicitly applies validated revision. Record active digest, status, per-instance health and operator audit ID.
6. Use independent Modbus/IEC104 clients to verify actual field values before accepting commissioning.

## 3. Future OS checks

**Windows:** open `services.msc`, inspect dedicated ARPY Gateway Runtime status and startup/recovery policy. Future package may support:
```powershell
Get-Service -Name "ARPYGateway"
Get-WinEvent -LogName Application -MaxEvents 50
```
This is **not a guaranteed service name/event provider** until Windows installer ships. For restart, require change authorization and plan temporary loss of protocol sessions.

**Linux:** future unit may provide:
```sh
systemctl status arpy-gateway.service
journalctl -u arpy-gateway.service --since "1 hour ago"
systemctl show arpy-gateway.service -p ActiveState -p SubState -p NRestarts
```
This is not currently installed on `main`. A system service must not live in a logged-in user's `systemd --user` manager.

**Cross-platform future CLI:** `arpyctl health`, `arpyctl instances`, `arpyctl revision active`, `arpyctl diagnostics --redact` should use the same authenticated IPC, not open snapshot DB files directly. These names remain **proposals** pending issue #8.

## 4. Standard troubleshooting sequence

1. **Is process alive?** Consult SCM/systemd state, PID/restarts, exit codes and logs. `STOPPED`/`STARTING`/`UNCONFIGURED`/`RUNNING`/`DEGRADED` are not interchangeable.
2. **Is IPC accessible?** Verify local socket/pipe presence, ACL/peer identity, major version and connection errors. A Studio "Runtime disconnected" banner does not imply protocol links died.
3. **Which active revision?** Verify digest, version, stage/apply/recovery history and whether an edited draft is mistakenly expected to be live.
4. **Which instance is unhealthy?** Inspect IED peer and station separately: remote Modbus IP/Unit ID, IEC104 listen bind, allowed SCADA peer list, CA/IOA namespace, serial port ownership, last transition, reconnect attempt.
5. **What happened to values?** Compare source timestamp, observation time, monotonic age, quality stale/invalid, source origin real/simulated and per-source sequence. Missing data is never silently "0 Good".
6. **Is mapping valid?** Verify source/destination IDs, TI/COT, quality conversion, fan-out vs prohibited fan-in, enabled peers and deployment digest.
7. **Were packets truly transmitted?** Use independent readback and sanitized packet captures with operator approval; do not infer transmission from GUI display or test preview.
8. **Is system overloaded?** Inspect queue high-water, dropped/coalesced **monitoring** events, protocol event queue overflow, pending worker/IPC tasks, poll interval, CPU/RSS/files/handles.
9. **Is the diagnosis actionable?** Copy redacted structured diagnostics with timestamp/revision, exception/error code, affected instance and last transition; avoid secrets.

## 5. Specific failure runbooks

### GUI closed, runtime still communicating
Expected normal state. Do **not** reopen GUI simply to "keep gateway alive". Verify service PID and independent protocol-client RX/TX/heartbeat before and after. If connection drops when Studio exits, treat as **release-blocking architectural bug**, not a user mistake.

### Modbus TCP IED unreachable
Identify only affected instance/IED and expected scan group. Check network link, IP/DNS, Unit ID, TCP port, firewall, response deadline and retry budget. Runtime should mark those signals stale/invalid and continue others. Restore link and verify bounded reconnect and register refresh; no endless aggressive reconnect.

### RTU COM/TTY removed
Check OS serial device path, permissions, USB topology/udev, bus serialization and baud/parity match. After detachment, runtime closes invalid file handle, pauses affected poll and reopens only when device is ready; physical bus may require operator inspection. Confirm no busy loop.

### IEC-104 SCADA Master offline
Inspect remote peer session, T1/T2/T3/window settings, listening endpoint and allowed master policy; distinguish SCADA peer connectivity from *local ASDU station CA*. A master disconnect must not delete CA signals. After reconnection, verify GI/spontaneous behavior and COT/time semantics with client capture.

### Values frozen but network connected
Check sequence/observation age, quality transitions, receive/publish queues, transform errors, downstream client snapshots, and whether any manual simulation override is active. Do not create a new timestamp on cached value just to remove "stale".

### Apply/deploy failure
Capture operation ID, expected old revision hash, staged content digest, impact diff and rollback/error reason. Do not restart all adapters as reflex; evaluate whether the old live graph still operates. Retry only after confirming stage validity and idempotency status, no concurrent deployer and no partial activation.

### Runtime crash or boot failure
OS service manager may restart. Inspect crash counter, journal integrity, last committed revision, schema migration status, credentials and ports. If recovery cannot prove safe last-known-good, keep data plane blocked until operator selects reviewed recovery; avoid applying draft to "make it work."

### Simulation accidentally left enabled
Authorized operator must use Stop All via runtime API (when implemented), verify TTL/owner and physical control isolation, confirm downstream SCADA receives live origin/quality interpretation. Closing Studio **must not be assumed** to safely stop an established runtime simulation unless explicit session policy says so; default TTL expiration is mandatory.

## 6. Upgrade, rollback, backup and recovery

- Schedule service update separately from GUI update; GUI upgrade does not imply engine restart.
- Back up **engineering DB**, verified deployed snapshot, revision ledger, migrations, local service metadata, access-policy reference and redacted audit per retention policy. Protect backup with access control/encryption as required by OT policy; test restore periodically.
- Backups and operation journals are **not** mere log files and should not be discarded by updater/uninstaller.
- Confirm new package matches schema/API/adapter capabilities and field-license constraints; store original installer/binary digest.
- Hot-swap if proven safe for exact change; otherwise display outage estimate and obtain operational approval. Monitor after apply, check independent clients and rollback if criteria fail.
- **Power failure vs graceful shutdown:** crash may interrupt TCP/IEC104 sessions; last-good digest must determine restart config. Any half-committed activation is rejected or rolled back according to journal state.
- Restoring a backup onto a different machine requires rechecking network/serial/credentials, safety permissions and potentially duplicated address/CA conflicts. Never auto-activate imported config on live OT network.

## 7. Observability minimum catalog (proposed)

- Process: uptime, state, active revision digest, restart reason, healthy/total instances, IPC sessions, memory RSS, FD/handle count.
- Per instance/IED/peer: protocol role, `CONNECTING/ONLINE/OFFLINE/DEGRADED`, last RX/TX UTC, monotonic age, timeout/reconnect counts, last stable error, poll queue/latency.
- Signals: ID, native address scope, data type, current value, quality, origin, device timestamp if available, receive timestamp, sequence.
- Mapping: source/destination signal IDs, compile/validation version, conversion error counters, fanout count, rate.
- IEC104 sessions: peer identity, CA coverage, GI/spontaneous counters, TI/COT categories, confirmation/retransmit errors when adapter provides them.
- Deploy/simulation: operation ID, caller role, revision before/after, impacted sockets, rollback, injection owner/expiry, Stop All records.
- Bounded cardinality metrics; avoid one Prometheus time-series per half-million signal by default. Detailed per-signal drilldown is paged/read-on-demand; logs ring bounded and secrets redacted.

## 8. Escalation and change gates

- Unauthorized command/simulation delivery: **halt dangerous outputs / incident path**, preserve audit and packet trace, do not bypass protection to pass a test.
- Repeated recovery crashes or missing active snapshot: flag safe-unconfigured state; require administrator-reviewed action.
- Field device failure: isolate fault to that instance; do not restart entire gateway unless evidence demands.
- Contact ownership by issue: IPC/service #8, Store #3, protocol #6/#7, Mapping #5, Simulation #9, QA #10, ARCH #11/#13.
- Release acceptance cannot be self-declared from GUI build or mock data. See `docs/quality/SERVICE_ACCEPTANCE_MATRIX.md`.
