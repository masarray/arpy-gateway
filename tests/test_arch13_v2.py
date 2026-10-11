"""RFC #13 executable acceptance: Modbus peers ≠ IEC-104 station ownership.

Only Python stdlib & canonical v1 DTOs. Windows-first workflow validates the
proposed v2 wire/semantic contract without changing deployed v1 models.
"""
from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
import unittest

from arpy_gateway.contracts.codec import encode_project, decode_project
from arpy_gateway.contracts.project_v2 import (
    canonical_project_digest_v2, decode_project_v2, encode_project_v2,
)
from arpy_gateway.domain.models import (
    ProtocolInstance, ProtocolSignal, MappingDefinition, ProjectSnapshot,
    InstanceRole, SignalFamily, SignalDirection, ValueType,
)
from arpy_gateway.domain.v2 import (
    CommunicationPoint, DataStation, EngineeringProjectV2, SignalBinding,
    propose_migration_v1_draft,
)
from arpy_gateway.domain.validation_v2 import validate_project_v2

ROOT = Path(__file__).resolve().parents[1]


def source(sid, iid="modbus", addr=10):
    return ProtocolSignal(
        sid, iid, sid, SignalFamily.ANALOG, ValueType.FLOAT32,
        SignalDirection.INBOUND, True, "V",
        {"register_table": "holding", "native_address": addr}
    )


def destination(sid, iid="iec", ioa=1000):
    return ProtocolSignal(
        sid, iid, sid, SignalFamily.ANALOG, ValueType.FLOAT32,
        SignalDirection.OUTBOUND, True, "V",
        {"ioa": ioa, "ti": 13}
    )


def scenario():
    instances = (
        ProtocolInstance("modbus","Meters","modbus_tcp",InstanceRole.CLIENT,True,{}),
        ProtocolInstance("iec","IEC-104 Service","iec104",InstanceRole.SERVER,True,{}),
    )
    points = (
        CommunicationPoint("meter-a","modbus","Meter A","remote_device",True,
                           {"host":"127.0.0.1","port":1502,"unit_id":1,"poll_interval_ms":100}),
        CommunicationPoint("meter-b","modbus","Meter B","remote_device",True,
                           {"host":"127.0.0.2","port":1502,"unit_id":1,"poll_interval_ms":200}),
        CommunicationPoint("master-a","iec","SCADA A","authorized_master",True,
                           {"allowed_master_ip":"192.0.2.1"}),
        CommunicationPoint("master-b","iec","SCADA B","authorized_master",True,
                           {"allowed_master_ip":"192.0.2.2"}),
    )
    stations = (DataStation("station-1","iec","Plant CA1",True,
                             {"common_address":1}),)
    signals = (source("s-a"),source("s-b",addr=20),destination("iec-out"))
    mappings = (MappingDefinition("route-a","s-a","iec-out"),)
    bindings = (
        SignalBinding("s-a",communication_point_id="meter-a"),
        SignalBinding("s-b",communication_point_id="meter-b"),
        SignalBinding("iec-out",data_station_id="station-1"),
    )
    return EngineeringProjectV2(
        ProjectSnapshot("plant",instances,signals,mappings),
        points,stations,bindings
    )


def codes(candidate):
    return {x.code for x in validate_project_v2(candidate)}


class ProposalTests(unittest.TestCase):
    def test_two_ieds_two_scada_masters_one_ca_and_mapping_are_valid(self):
        p=scenario()
        self.assertEqual(validate_project_v2(p),())
        self.assertEqual(len(p.communication_points),4)
        self.assertEqual(p.signal_bindings[2].data_station_id,"station-1")
        self.assertNotEqual(p.communication_points[2].id,p.data_stations[0].id)
        self.assertEqual(
            decode_project_v2(encode_project_v2(p)),p)
        self.assertEqual(
            canonical_project_digest_v2(p),
            canonical_project_digest_v2(decode_project_v2(encode_project_v2(p))))

    def test_one_signal_fanout_keeps_stable_ownership(self):
        p=scenario()
        next_dst=replace(p.base.signals[2],id="iec-extra",name="Second destination",parameters={"ioa":1002,"ti":13})
        new=replace(p.base.mappings[0],id="route-b",destination_signal_id="iec-extra")
        p=replace(p,base=replace(p.base,signals=p.base.signals+(next_dst,),
                                 mappings=p.base.mappings+(new,)),
                  signal_bindings=p.signal_bindings+(SignalBinding("iec-extra",data_station_id="station-1"),))
        self.assertEqual(codes(p),set())
        renamed=replace(p.communication_points[0],name="Renamed Meter")
        self.assertEqual(codes(replace(p,communication_points=(renamed,)+p.communication_points[1:])),set())

    def test_reject_cross_instance_dangling_disabled_or_ambiguous_binding(self):
        p=scenario()
        bad=replace(p.signal_bindings[0],communication_point_id="master-a")
        self.assertIn("BINDING_SCOPE",codes(replace(p,signal_bindings=(bad,)+p.signal_bindings[1:])))
        bad=replace(p.signal_bindings[0],communication_point_id="missing")
        self.assertIn("BINDING_SCOPE",codes(replace(p,signal_bindings=(bad,)+p.signal_bindings[1:])))
        bad=replace(p.signal_bindings[0],data_station_id="station-1")
        self.assertIn("BINDING_AMBIGUOUS",codes(replace(p,signal_bindings=(bad,)+p.signal_bindings[1:])))
        offline=replace(p.communication_points[0],enabled=False)
        self.assertIn("BINDING_DISABLED",codes(replace(p,communication_points=(offline,)+p.communication_points[1:])))

    def test_iec104_ca_not_assigned_to_allowed_master_ip(self):
        p=scenario()
        wrong=replace(p.signal_bindings[2],communication_point_id="master-a",data_station_id=None)
        self.assertIn("STATION_REQUIRED",codes(replace(p,signal_bindings=p.signal_bindings[:2]+(wrong,))))
        station2=DataStation("other-station","iec","Plant CA2",True,{"common_address":2})
        dst=destination("iec-two","iec",ioa=1000)
        b=SignalBinding("iec-two",data_station_id="other-station")
        q=replace(p,data_stations=p.data_stations+(station2,),
                  base=replace(p.base,signals=p.base.signals+(dst,)),
                  signal_bindings=p.signal_bindings+(b,))
        self.assertEqual(codes(q),set())
        collided=replace(station2,settings={"common_address":1})
        self.assertIn("IEC104_CA_COLLISION",codes(replace(q,data_stations=p.data_stations+(collided,))))

    def test_duplicate_tcp_ied_and_rtu_slave_scope_rejected(self):
        p=scenario()
        duplicate=replace(p.communication_points[1],settings=p.communication_points[0].settings)
        self.assertIn("MODBUS_PEER_COLLISION",codes(replace(p,communication_points=
             (p.communication_points[0],duplicate)+p.communication_points[2:])))
        inst=ProtocolInstance("serial","RS485","modbus_rtu",InstanceRole.CLIENT,True,
                              {"serial_port":"COM3","baudrate":9600})
        a=CommunicationPoint("slave1","serial","IED1","remote_device",True,{"unit_id":5})
        b=CommunicationPoint("slave2","serial","IED2","remote_device",True,{"unit_id":5})
        r=EngineeringProjectV2(ProjectSnapshot("serial",instances=(inst,)),
                              (a,b))
        self.assertIn("MODBUS_PEER_COLLISION",codes(r))
        moved=replace(b,settings={"unit_id":6,"serial_port":"COM4"})
        self.assertIn("RTU_BUS_OWNERSHIP",codes(replace(r,communication_points=(a,moved))))

    def test_v1_migration_draft_does_not_guess_ied_or_deploy(self):
        p=scenario()
        migrated=propose_migration_v1_draft(p.base)
        self.assertEqual(migrated.base,p.base)
        self.assertFalse(migrated.communication_points)
        self.assertFalse(migrated.signal_bindings)
        self.assertIn("BINDING_REQUIRED",codes(migrated))
        self.assertIn("STATION_REQUIRED",codes(migrated))
        self.assertEqual(migrated.base.schema_version,1)
        self.assertEqual(decode_project(encode_project(p.base)),p.base)

    def test_wire_rejects_unknown_fields_and_unchanged_v1(self):
        p=scenario()
        wire=encode_project_v2(p)
        self.assertEqual(wire["schema_version"],2)
        self.assertIn("connection_point_id",wire["signals"][0])
        unknown=encode_project_v2(p)
        unknown["signals"][0]["invented"] = 1
        with self.assertRaises(ValueError):
            decode_project_v2(unknown)
        wrong=encode_project_v2(p)
        wrong["schema_version"]=1
        with self.assertRaises(ValueError):
            decode_project_v2(wrong)
        original=encode_project(p.base)
        self.assertEqual(original["schema_version"],1)
        self.assertNotIn("communication_points",original)
        with self.assertRaises(ValueError):
            decode_project(wire)

    def test_schema_v2_does_not_mutate_v1_and_schema_files_are_strict(self):
        old=json.loads((ROOT/"contracts/project.v1.schema.json").read_text())
        new=json.loads((ROOT/"contracts/project.v2.schema.json").read_text())
        self.assertEqual(old["properties"]["schema_version"]["const"],1)
        self.assertEqual(new["properties"]["schema_version"]["const"],2)
        self.assertFalse(new["additionalProperties"])
        self.assertFalse(new["$defs"]["signal"]["additionalProperties"])
        self.assertEqual(new["$defs"]["signal"]["properties"]["connection_point_id"]["type"],["string","null"])


if __name__=="__main__":
    unittest.main()
