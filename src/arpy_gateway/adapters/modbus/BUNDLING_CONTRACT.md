# ARPY Modbus — Embedded offline transport packaging contract

**Status:** MODBUS adapter packaging handoff; no Windows Service/Linux installer or Studio-to-runtime connection is shipped by this PR.  
**Owner:** Modbus lane (#6) specifies protocol dependencies and startup checks. Platform/service/installer lane (#8/#10) builds and validates customer releases; avoid writing installer or service files from this lane.

## User-facing requirement

An end user installs one ARPY application package and operates **Add Device → Test Connection → Add Signals → Save/Deploy → Live Value Viewer** entirely from PySide6. Users never see `pip install`, `python -m`, a dependency download dialog, or a probe JSON path. Source-developer probe commands stay in engineering docs only; they are **not** an installation procedure.

Studio and gateway daemon are distinct processes. Installer must register OS-managed gateway runtime for auto-start, and packaged Studio must connect to that runtime over authenticated IPC. **GUI close/logout cannot stop communication.** The Modbus lane supplies headless transport, not the GUI/store/IPC itself.

## Embedded transport dependency manifest (version locked)

| Bundle component | Pinned/tested policy | Why |
|---|---|---|
| `pymodbus` | **3.15.0 exactly** | Tested v3.15 async TCP/RTU protocol transport APIs, including `device_id` |
| `pyserial` | Included via `pymodbus[serial]==3.15.0`; exact resolved version/hash locked in platform build | COM/TTY serial transport needed for RTU |
| CPython | Compatible packaged runtime per OS build | Users do not separately install Python |
| ARPY Modbus adapter | Same tested release manifest as daemon | Device supervisor, polling, planner, decoder, state projection |

PyModbus licensing: BSD-3-Clause; redistributor must preserve appropriate license/copyright notice, the PySerial license and notices for all bundled transitive dependencies. Vendor and version metadata should appear in the installer SBOM and About/Diagnostics, **not** as commands for end users.

**Rule:** the daemon never executes `pip`, downloads protocol packages from the internet, or changes transport versions while running. Runtime protocol version is verified *before* any Modbus device socket/task starts. Missing/broken bundle raises `ModbusBundleError` with an actionable **Repair installation** message; it does not cause an infinite IED retry loop. Use installer repair or a signed, staged updater while the service is safely stopped/upgraded; preserve validated active revision.

## Build pipeline (installer team, not implemented here)

1. Build reproducible OS-specific runtime artifacts on **Windows x64 and Linux x64**; include PyModbus v3.15.0, serial extra, transitive dependencies and third-party license notices. Create dependency/SBOM manifest with resolved SHA-256 hashes. Do **not** download on the target gateway.
2. Freeze dependencies during build from reviewed lockfile/wheelhouse; review license/security and import compatibility after any version change. **Do not** use `pip install -U` during service startup.
3. Freeze/compile the daemon separately from Qt Studio. PyInstaller/Nuitka/cx_Freeze approaches require measurement and platform testing. Import collection should include async TCP and async serial, PySerial and platform serial backends. Never assume a Studio-only onefile EXE provides service dependencies.
4. Package daemon/Studio together via OS-appropriate installer, but with **independent lifecycles**. Install privileged SCM/systemd service once with strict ACL and least privilege; Studio remains optional user process.
5. Verify bundled import checks (`verify_embedded_transport('modbus_tcp')` and `('modbus_rtu')`) **offline**, after packaging, and before attempting IED connectivity. Broken transport is an installer defect, not an IED fault. Verify all required modules from the **frozen runtime binary**, not just the build-host virtual environment.
6. Integration tests: clean Windows/Linux VM **without Python/pip/internet**; install ARPY; reboot/start service without Studio; attach GUI, add simulated Modbus IED and signals, deploy via revisioned store; see live values; close/kill GUI; independent Modbus client confirms runtime continuity. Unplug/replug serial and measure recovery.
7. Run automated protocol interop from the packaged runtime, including malformed/exception/timeout cases. Only sign and distribute an installer after protocol and OS-service test evidence.

## Performance/reliability contract

- One asyncio supervisor per TCP IED, not a thread or timer per signal. Plan legal contiguous FC01/02/03/04 reads with Modbus PDU limits and native zero-based addresses.
- One serial owner per RTU port; all devices/slaves serialized. A `ModbusNoResponseError` for one silent RTU slave must mark **only that slave** offline and back off attempts; other slaves continue. Physical serial-port/read cancellation errors restart/resynchronize the bus so stale frames cannot be misattributed.
- Health and data are separate: report last error, connection state, read counts and real sample quality/timestamps; no fabricated `Good`, timestamp or numeric zero on disconnected IED.
- All retries/backoff and sample queues bounded; no user-visible package installation or unbounded version-retry loops.
- Unsupported Modbus write/control functions blocked by default. OT deployment needs proper segmentation and authorization; reading data does not imply control authority.
- Quality, typed decoding, byte/word order and scaling are determined by configured register-map policy. Modbus standard does **not** discover vendor signal names/data types automatically.

## Release gate

Tests in `tests/modbus/` + workflow `.github/workflows/modbus.yml` validate the transport, fake RTU bus fault isolation and independent TCP interoperability. Production approval **still requires** real serial hardware/RS-485 CRC/framing timing, a real OS service installer, Store #3, IPC #8, Studio Value Viewer and OTA/OT security reviews. Documentation alone is not evidence of an embedded working Windows/Linux installer.
