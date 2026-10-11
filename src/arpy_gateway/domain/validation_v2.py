"""RFC #13 v2 semantic validation: O(I + P + ST + S + E).

ConnectionPoint ≠ DataStation. Remote SCADA master IP never owns IEC-104
server CA/IOA data. All enabled Modbus Master signals must belong to an
explicit stable IED/Slave, never to the GUI row index or instance settings.
"""
from __future__ import annotations

from dataclasses import replace
import ipaddress
from .models import InstanceRole, ProjectSnapshot, ProtocolSignal, to_mutable
from .validation import ValidationIssue, validate_project
from .v2 import EngineeringProjectV2

def _valid_int(value, lower, upper):
    return type(value) is int and lower <= value <= upper

def validate_project_v2(project: EngineeringProjectV2) -> tuple[ValidationIssue, ...]:
    issues: list[ValidationIssue] = []
    def error(code, kind, key, reason):
        issues.append(ValidationIssue(code, kind, key, reason))

    instances = {x.id: x for x in project.base.instances}
    points, stations, bindings = {}, {}, {}
    tcp_scopes, rtu_scopes, station_scopes, allowed_masters = set(), set(), set(), set()

    for p in project.communication_points:
        if not p.id or p.id in points:
            error("CONNECTION_ID", "communication_point", p.id, "Empty/duplicate stable peer ID")
            continue
        points[p.id] = p
        inst = instances.get(p.instance_id)
        if inst is None or not p.name.strip():
            error("CONNECTION_OWNER", "communication_point", p.id, "Invalid owning instance or name")
            continue
        if inst.protocol in ("modbus_tcp", "modbus_rtu") and inst.role is InstanceRole.CLIENT:
            if p.kind != "remote_device":
                error("CONNECTION_KIND", "communication_point", p.id, "Modbus Master requires remote_device")
                continue
            unit = p.settings.get("unit_id")
            if not _valid_int(unit, 1, 247):
                error("MODBUS_UNIT_ID", "communication_point", p.id, "Read-only Master unit 1..247 required")
                continue
            if inst.protocol == "modbus_tcp":
                host, port = p.settings.get("host"), p.settings.get("port")
                if not (type(host) is str and 1 <= len(host) <= 253
                        and not any(c.isspace() or ord(c) < 32 for c in host)
                        and _valid_int(port, 1, 65535)):
                    error("MODBUS_TCP_ENDPOINT", "communication_point", p.id, "Valid host and port required")
                    continue
                key = (inst.id, host.casefold(), port, unit)
                if key in tcp_scopes:
                    error("MODBUS_PEER_COLLISION", "communication_point", p.id, "Duplicate TCP peer and Unit ID")
                tcp_scopes.add(key)
            else:
                # COM/TTY parameters belong solely to the owning RTU instance.
                if "serial_port" in p.settings or "baudrate" in p.settings:
                    error("RTU_BUS_OWNERSHIP", "communication_point", p.id,
                          "Serial port and baudrate belong to RTU instance")
                key = (inst.id, unit)
                if key in rtu_scopes:
                    error("MODBUS_PEER_COLLISION", "communication_point", p.id, "Duplicate RTU slave on bus")
                rtu_scopes.add(key)
            for key in ("poll_interval_ms", "timeout_ms"):
                val = p.settings.get(key)
                if val is not None and not _valid_int(
                    val, 50, 3600_000 if key == "poll_interval_ms" else 60_000
                ):
                    error("MODBUS_POLL_SETTING", "communication_point", p.id,
                          "Invalid Modbus poll/timeout setting")
        elif inst.protocol == "iec104" and inst.role is InstanceRole.SERVER:
            if p.kind != "authorized_master":
                error("CONNECTION_KIND", "communication_point", p.id, "IEC104 server peer is authorized_master")
                continue
            host = p.settings.get("allowed_master_ip")
            try:
                if type(host) is not str:
                    raise ValueError("IP must be string")
                addr = str(ipaddress.ip_address(host))
            except ValueError:
                error("IEC104_MASTER_IP", "communication_point", p.id,
                      "Authorized SCADA peer requires valid IP address")
                continue
            key = (inst.id, addr)
            if key in allowed_masters:
                error("IEC104_MASTER_COLLISION", "communication_point", p.id,
                      "Duplicate authorized master IP")
            allowed_masters.add(key)
        else:
            error("CONNECTION_CAPABILITY", "communication_point", p.id,
                  "Unsupported protocol/role for communication point")

    for station in project.data_stations:
        if not station.id or station.id in stations:
            error("STATION_ID", "data_station", station.id, "Empty/duplicate stable station ID")
            continue
        stations[station.id] = station
        inst = instances.get(station.instance_id)
        if inst is None or not station.name.strip():
            error("STATION_OWNER", "data_station", station.id, "Invalid owning instance or name")
            continue
        if inst.protocol != "iec104" or inst.role is not InstanceRole.SERVER:
            error("STATION_CAPABILITY", "data_station", station.id,
                  "This v2 candidate defines station CA only for IEC104 Server")
            continue
        ca = station.settings.get("common_address")
        if not _valid_int(ca, 1, 65534):
            error("IEC104_CA", "data_station", station.id, "Common Address must be 1..65534")
            continue
        key = (inst.id, ca)
        if key in station_scopes:
            error("IEC104_CA_COLLISION", "data_station", station.id,
                  "Duplicate CA in same server instance")
        station_scopes.add(key)

    for binding in project.signal_bindings:
        if not binding.signal_id or binding.signal_id in bindings:
            error("BINDING_ID", "signal", binding.signal_id, "Duplicate or empty signal binding")
        else:
            bindings[binding.signal_id] = binding

    samples = {s.id: s for s in project.base.signals}
    for sid, binding in bindings.items():
        sig = samples.get(sid)
        if sig is None:
            error("BINDING_SIGNAL", "signal", sid, "Binding refers to missing signal")
            continue
        if binding.communication_point_id is not None and binding.data_station_id is not None:
            error("BINDING_AMBIGUOUS", "signal", sid, "Signal cannot belong to peer and station")
        for field, node, label in (
            (binding.communication_point_id, points, "communication_point"),
            (binding.data_station_id, stations, "data_station"),
        ):
            if field is not None:
                target = node.get(field)
                if target is None or target.instance_id != sig.instance_id:
                    error("BINDING_SCOPE", "signal", sid, "Binding missing or belongs to different instance")
                elif sig.enabled and instances[sig.instance_id].enabled and not target.enabled:
                    error("BINDING_DISABLED", "signal", sid, f"Signal bound to disabled {label}")

    # Validate v1 stable IDs, mapping graph, type/field safety. IEC server CA
    # lives on the STATION, not remote-master peer, so inject station CA only
    # into this transient v1 validation view; never persist guessed metadata.
    transient: list[ProtocolSignal] = []
    for sig in project.base.signals:
        inst = instances.get(sig.instance_id)
        binding = bindings.get(sig.id)
        if inst and inst.enabled and sig.enabled:
            if inst.protocol in ("modbus_tcp", "modbus_rtu") and inst.role is InstanceRole.CLIENT:
                if binding is None or not binding.communication_point_id:
                    error("BINDING_REQUIRED", "signal", sig.id, "Enabled Modbus source requires IED/slave")
                elif binding.data_station_id:
                    error("BINDING_KIND", "signal", sig.id, "Modbus source cannot belong to station")
            if inst.protocol == "iec104" and inst.role is InstanceRole.SERVER:
                if binding is None or not binding.data_station_id:
                    error("STATION_REQUIRED", "signal", sig.id, "Enabled IEC server signal needs CA station")
                elif binding.communication_point_id:
                    error("STATION_BINDING_KIND", "signal", sig.id, "IEC output owned by station, not SCADA IP")
        if inst and inst.protocol == "iec104" and inst.role is InstanceRole.SERVER:
            station = stations.get(binding.data_station_id) if binding else None
            if station is not None:
                params = to_mutable(sig.parameters)
                params["common_address"] = station.settings.get("common_address")
                sig = replace(sig, parameters=params)
        transient.append(sig)
    base = replace(project.base, signals=tuple(transient))
    issues.extend(validate_project(base))
    return tuple(issues)
