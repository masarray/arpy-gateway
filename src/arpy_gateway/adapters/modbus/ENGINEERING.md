# ARPY Modbus TCP/RTU engine — issue #6 implementation notes

**Status: draft adapter implementation; NOT an installed Gateway Service, persisted Engineering Store, or real Windows/Linux field-certified driver.** Modbus-specific source lives in `adapters/modbus/` and tests in `tests/modbus/`; no Qt or IEC104/domain/contracts mutations. An unlocked #13 ConnectionPoint/DataStation model is **not** silently replaced here.

## Global technical baseline

- [Modbus Organization Application Protocol V1.1b3](https://www.modbus.org/docs/Modbus_Application_Protocol_V1_1b3.pdf): FC01/02 bits up to 2000, FC03/04 registers up to 125, native PDU addresses 0..65535. `40001` is an external **display label** for holding native address 0, not a field value sent in the frame.
- [Modbus Serial Line Protocol V1.02](https://www.modbus.org/file/secure/modbusoverserial.pdf): CRC16 low-byte-first RTU, frame t3.5 silence and t1.5 receive gap; high baud timing considerations; half-duplex bus. Framing/timing delegated to external transport PyModbus, not reimplemented by polling scheduler.
- [PyModbus 3.15.0](https://pypi.org/project/pymodbus/): BSD-3-Clause, cross-platform asyncio TCP/serial transports. Pin `pymodbus[serial]==3.15.0` for this lane; version APIs change (`device_id=` differs from legacy `slave=`). Audit license and platform wheels in release CI.

## Device → connection → signal → value lifecycle

- The **GUI** creates a stable `ConnectionPoint`/device ID via Store #3 and RFC #13 when approved; TCP peer has host, port, Unit ID, scan/timeout; RTU instance owns physical COM/TTY and framing, each slave connection owns Unit ID/scan/timeout. Duplicate Unit IDs on same RTU bus rejected. The adapter consumes `DeviceConfig` injection by service runtime; it **does not** write project files or invent IED identity from IP.
- Signals reference `parameters.device_id`, `register_table=coil|discrete|holding|input`, `native_address` 0-based, value type, byte/word order, scaling. This is an adapter-owned provisional metadata shape; ARCH #11/#13 must approve public field schemas and migration.
- On an instance start: validate **all** enabled signal/device references before opening any socket; compute read groups per device and FC (O(S log S)), adjacent/overlapping and legal PDU sizes. No cross-FC, cross-IED, cross-serial-bus batching. Do **not** request undefined hole registers.
- TCP Master: independent async client/supervisor per configured IED with bounded retries/timeout/backoff, no signal thread. RTU Master: one shared async PyModbus SerialClient, one bus-owner task, all slave requests **sequential**, independent per-slave scan deadlines. Serialized RS-485, not one serial instance per device.
- Decode bit or signed/unsigned 16/32/64/Float32/64 with deterministic byte/word swaps; apply scale+offset after decode. Invalid response/timeouts generate explicit `INVALID` or `COMMUNICATION_LOST` sample, never last-good-as-fresh or fabricated 0. Source device timestamp is absent for conventional Modbus read; `observed_at` means gateway observation time, not device time.
- `latest_snapshot()` and `device_statuses()` are non-Qt read projections for the future Runtime state/IPC. `samples()` is a bounded, **loss-tolerant latest-value notification feed**, with overflow counter. It must **NOT** be the authoritative lossless event/command queue; IPC #8 state store and mapping #5 need independent high-integrity event/ordered-apply semantics.
- TCP Server: explicit bind (loopback default), read-only FC01–04; publish valid mapped values by stable signal ID into sparse register table. Unknown addresses -> Modbus 0x02; not-yet-valid value -> 0x04; unsupported *writes* -> 0x01. There is **no physical command passthrough**. 32 clients by default, bounded timeout/frame length and cleanup. External network must be segmented; Modbus TCP normally has no built-in auth/encryption.

## Current support / intentionally missing

| Feature | Implementation | Verified evidence |
| --- | --- | --- |
| TCP Master FC01/02/03/04 | Async PyModbus wrapper, grouped polling | fake-device regression + independent TCP fixture in GitHub CI |
| RTU Master FC01/02/03/04 | Async PyModbus SerialClient, shared bus owner | fake serialized RTU tests; **physical serial hardware NOT YET tested** |
| TCP Server FC01/02/03/04 | Bounded read-only native asyncio MBAP listener | local loopback wire tests + independent PyModbus client CI |
| RTU Slave/Server | **NOT IMPLEMENTED**, requires validated serial multi-drop behavior and role design | BLOCKED: hardware/PTY tests |
| Modbus writes FC05/06/0F/10 | BLOCKED BY DEFAULT | negative wire/port test |
| Protocol edits/save DB | **NOT IMPLEMENTED in Modbus lane** | Store #3 and ARCH #13 |
| Validate/stage/deploy/restart/rollback | **NOT IMPLEMENTED in Modbus lane** | Service IPC #8 + Store #3 |
| PySide6 Value Viewer live | **NOT IMPLEMENTED in Modbus lane** | GUI #4 binds to paged runtime IPC #8 |
| HA active/standby redundancy | **RFC only**, must prevent two active poll/publish/control owners | Proposed issue, not current behavior |


## Embedded end-user packaging requirement

**End users must never install Python/PyModbus manually or run the diagnostic CLI.** PyModbus 3.15.0 plus the serial extra are internal, version-pinned dependencies of the future self-contained Windows/Linux gateway runtime package. Bundle health is checked during service startup; incompatible/missing transport triggers installer repair guidance, not perpetual device reconnect. Transport networking remains independent of PySide6, and the installed daemon must survive Studio close/logout.

**Full packaging/QA handoff:** [BUNDLING_CONTRACT.md](BUNDLING_CONTRACT.md). The headless JSON probe below is **for developers and protocol QA only** and must not appear in the customer GUI workflow.

## Local headless device/value probe (without GUI)

Install tested dependency: `python -m pip install "pymodbus[serial]==3.15.0"` and run:

```shell
PYTHONPATH=src python -m arpy_gateway.adapters.modbus.probe --config src/arpy_gateway/adapters/modbus/example_probe.json --seconds 20
```

Edit the **local probe JSON fixture** for an actual IED IP, unit id, PDU addresses and types. The provided sample defaults to `127.0.0.1:1502`, and no Modbus server is launched automatically. Probe prints per-device connection state, last read count and latest values/quality. It **does not Save/Deploy** a revision and exits without stopping any separate operating-system Gateway Runtime. This executable probe must not be conflated with the future service/Studio integration.

For RTU, replace `instance.protocol` with `modbus_rtu`, set `instance.settings={"serial_port":"COM3" | "/dev/ttyUSB0", "baudrate":9600,"parity":"N","stopbits":1}`, and remove device `host` or set empty. Unit IDs remain per device. Use actual permissioned serial interface; virtual serial/CRC timing are owned by PyModbus/pyserial and require independent interop evidence.

## Production integration acceptance gates

1. Approve and persist `ConnectionPoint`/Station IDs and snapshot schema (#13/#3), type-safe aliases and per-device poll groups. Changing draft `Save` cannot affect live I/O.
2. Runtime service #8 compiles the frozen approved revision, creates adapter instances, and publishes `SignalSample` into authoritative StateStore. Studio reattaches through authenticated IPC and queries *paged* Value Viewer by selected Modbus connection; GUI exit must not close TCP/RTU I/O.
3. Explicit `Validate → Stage → Deploy` with revision CAS/idempotency, impact preview and rollback; per-IED changes permit selective reconnect when proven; COM baud/parity changes restart affected serial bus, not unrelated TCP devices.
4. Independent Modbus TCP and RTU interoperability suite on **Windows and Linux** with hardware for serial timing, bad CRC, timeouts, link down/replug, mixed FC, floating endian and PDU limit negative cases.
5. Security/OT review for field listen binds, allowed client IP policy, FC write prohibition, service account/serial privileges, diagnostics redaction. Avoid exposing device network to untrusted hosts.
6. Subsequent **redundancy RFC**: define active/standby fencing, lease/leader epoch and split-brain prevention *before* automatic failover. Two independent gateways polling/reporting the same IED and serving the same SCADA without interlocks is not high availability.

**Keep PR draft until tests and integration evidence meet issue #6, especially RTU Server and real device interoperability.**
