# Windows + Linux gateway service deployment and packaging plan

**Status: PROPOSED, examples are NOT shipped installers/services.** Windows/Linux runtime entrypoints, installer contents and hardware integration must pass platform CI and signed-off smoke tests. The service cannot be coupled to the desktop GUI or to an interactive user login.

## 1. Common release artifact contract

Ship separate logical applications (they may share pure Python packages):
- `arpy-gatewayd`: headless, no Qt dependency, launched by OS service manager, owns live protocol sockets and active snapshot.
- `arpy-studio`: optional PySide6 desktop; allowed to connect/disconnect; never controls runtime process lifetime implicitly.
- `arpyctl`: proposed minimal CLI for health/diagnostics/revision status through same authenticated IPC (no direct service DB mutation).

Version all three with compatible contract/API versions. Separate paths for immutable program files, runtime state/journals, engineering projects, caches/logs and secrets. Service account owns only necessary state/device access; Studio's desktop user is not automatically service owner or deploy admin.

No shipped package should silently grant inbound firewall access, elevate Studio permanently, auto-start physical command outputs, overwrite validated active config during upgrade, or remove backups on uninstall. Offline operation must not generate fatal errors from checking updates.

## 2. Windows (Service Control Manager)

- Install a **real Windows Service**, not a task-scheduler GUI/Startup-folder workaround. Use an SCM-aware service host/wrapper with timely start/stop status callbacks and a single compositional runtime entrypoint. Choose and benchmark pywin32 service host vs native compiled host in a separate packaging decision; **do not** assume PyInstaller --windowed EXE alone is a Windows Service.
- Default startup: automatic only after a valid installation; on first run without deployed revision, enter safe `UNCONFIGURED`, keep field outputs disabled.
- Service identity: least privileged dedicated account or appropriately restricted managed service identity; audit privileges needed for inbound device listeners and serial/COM access. Prefer service SID + ACL-controlled state/IPC, not blanket LocalSystem privileges.
- Suggested paths (final names require installer decision):
  - binaries: `%ProgramFiles%\\ARPY Gateway\\` (read-only to ordinary users)
  - service state, verified snapshots, recovery journal: `%ProgramData%\\ARPY Gateway\\runtime\\`
  - engineering drafts/projects: user or shared engineering directory with correct ACL (not an executable's directory)
  - logs/audit: ProgramData with bounded retention, optionally Windows Event Log integration
- Runtime registration, recovery action thresholds and service upgrade are part of privileged installer/uninstaller, not called every time Studio launches. Recovery policy must avoid infinite crash/restart storms; record failure reason and use backoff or intervention state.
- GUI-only update may replace Studio while runtime continues. Runtime binaries or schema update is an **explicit** maintenance action: verify compatibility, backup LKG and journal, stop service if necessary, replace, start, health check and rollback on failure.
- Distinguish stop by authorized operator (no automatic restart) from unexpected crash, and distinguish login/logout events from service lifetime.

**Windows acceptance:** install with admin once; reboot, close/log out desktop, kill Studio, GUI update, crash runtime and restart via SCM; verify service PID/socket ownership, status, revision hash and protocol sequence.

## 3. Linux (systemd system unit)

Illustrative **uninstalled** unit for a future Linux artifact, not a functional current service:

```ini
[Unit]
Description=ARPY Gateway Runtime (headless)
After=network.target

[Service]
Type=exec
User=arpy
Group=arpy
ExecStart=/opt/arpy-gateway/bin/arpy-gatewayd --service
Restart=on-failure
RestartSec=5s
TimeoutStopSec=30s
StateDirectory=arpy-gateway
RuntimeDirectory=arpy-gateway
LogsDirectory=arpy-gateway
UMask=0077
NoNewPrivileges=true
ProtectSystem=strict
ProtectHome=true
PrivateTmp=true

[Install]
WantedBy=multi-user.target
```

- `Type=exec` assumes tested systemd support on the target distributions; choose `Type=notify` **only after implementing sd_notify readiness**. `WatchdogSec=` requires a real heartbeat (`WATCHDOG=1`) from the service; otherwise it can kill healthy runtime. No fake watchdog option.
- `network.target` alone does not guarantee IEDs reachable; the gateway must manage LAN/IED reconnect internally. Do not block boot indefinitely waiting for `network-online.target` or a particular SCADA host.
- `StateDirectory`/`RuntimeDirectory` allow systemd-managed directories; set parent socket ACL/owner and remove stale socket only when owner is validated. Logs may use journald plus bounded optional files; do not log passwords/tokens.
- Serial RTU on `/dev/ttyUSB*` may require a documented udev policy/dialout access and tested device path stability; distribution-specific user/group names vary. Additional sandbox controls (`DevicePolicy`/`DeviceAllow`) require real device access tests before enabling. Never run as root solely to access COM/TTY.
- Do **not** install as a `systemd --user` unit if gateway must survive logout.
- Unit must pass a noninteractive boot/restart/stop test, process exit code mapping, cleanup and journal inspection.

Example future operator checks (not working ARPY commands until package exists):
```sh
sudo systemctl status arpy-gateway.service
sudo journalctl -u arpy-gateway.service --since "1 hour ago"
sudo systemctl restart arpy-gateway.service
```

Restarts interrupt real sessions. Operational documentation must warn before explicit restart and require maintenance authorization when appropriate.

## 4. Install / upgrade / uninstall transaction

**Install:**
1. Check OS/CPU/distro and Python/lib/Qt/protocol stack compatibility; do not claim Linux ARM from a Windows x64 build.
2. Install immutable runtime binaries and Studio independently; create service user/directory ACLs and local IPC transport.
3. Verify packaged SHA-256/signature where available, integrity of dependencies/licenses (including IEC-104 library terms).
4. Register service with native manager, default safe settings/health without a deployed revision.
5. Optionally install Studio/CLI shortcuts; avoid extra management listeners.
6. Validate cold boot and GUI attach before calling installer successful.

**Upgrade:**
1. Verify target contract major, explicit migration plan, backup active snapshot, journals and engineering data; record old binary version.
2. Upgrade Studio alone if only UI changes. Do not restart runtime just because Studio upgraded.
3. For runtime update, plan change window; ensure active revision can be read by new engine; stage binaries, stop gracefully, replace atomically where platform permits, restart and observe health.
4. On failure restore old binaries plus compatible DB/snapshot; never silently downgrade schema or destroy projects.

**Uninstall:** stop and remove the service **with approval**; do not assume deleting Studio should uninstall a running gateway. Default preserve active configs, audit records and backups with a documented opt-in destructive cleanup.

## 5. Cross-platform continuous verification

CI matrix at minimum: Windows latest supported x64, Ubuntu supported LTS x64; add representative distro and ARM64 only after dependencies/package tests. Validate:
- no Qt imported by daemon (`python -c ...` import and dependency checks);
- service registration in administrative test VM, cold boot without GUI, logout/kill GUI isolation;
- IPC authentication and restart reconnect, active revision rehydrate;
- package paths/permissions and clean uninstall (including recovery options);
- 100k/500k signal capacity with benchmark environment, not just synthetic Qt screenshot;
- unsigned binaries may show OS warnings; avoid claiming guaranteed no SmartScreen alerts without signing/reputation evidence.

## 6. External engineering evidence (not code dependencies)

- [Microsoft: Windows services can run without user logged on](https://learn.microsoft.com/en-us/windows/win32/services/services)
- [Microsoft: interactive services are not desktop UIs](https://learn.microsoft.com/en-us/windows/win32/services/interactive-services)
- [Microsoft: service access rights and DACL](https://learn.microsoft.com/en-us/windows/win32/services/service-security-and-access-rights)
- [systemd.service man page](https://www.freedesktop.org/software/systemd/man/latest/systemd.service.html)
- [systemd.exec man page](https://www.freedesktop.org/software/systemd/man/latest/systemd.exec.html)
- [Ignition Gateway service on Windows and Linux](https://www.docs.inductiveautomation.com/docs/8.3/platform/gateway)

**See:** `docs/operations/OPERATIONS_RUNBOOK.md` and `docs/quality/SERVICE_ACCEPTANCE_MATRIX.md`.
