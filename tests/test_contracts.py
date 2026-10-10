"""Offline foundation acceptance tests — no Qt, no network, no protocol drivers."""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
import json
import unittest

from arpy_gateway.contracts.codec import canonical_project_digest, decode_project, encode_project
from arpy_gateway.contracts.ipc import API_VERSION, Envelope, MessageKind
from arpy_gateway.domain.models import (
    MappingKind, SignalDirection, SignalFamily, Quality, SignalOrigin, SignalSample,
)
from arpy_gateway.domain.validation import validate_project

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "examples" / "projects" / "modbus-client-to-iec104-server.v1.json"
NOW = datetime(2026, 10, 10, tzinfo=timezone.utc)


def sample_project():
    return decode_project(json.loads(FIXTURE.read_text(encoding="utf-8")))


def codes(project):
    return {i.code for i in validate_project(project)}


class ContractsTest(unittest.TestCase):
    def test_fixture_valid_and_canonical_roundtrip(self):
        p = sample_project()
        self.assertEqual(validate_project(p), ())
        self.assertEqual(decode_project(encode_project(p)), p)
        self.assertEqual(canonical_project_digest(p), canonical_project_digest(sample_project()))
        self.assertEqual(len(canonical_project_digest(p)), 64)

    def test_nested_settings_are_immutable(self):
        p = sample_project()
        with self.assertRaises(TypeError):
            p.instances[0].settings["host"] = "invalid"
        with self.assertRaises(TypeError):
            p.signals[0].parameters["native_address"] = 999
        writable = encode_project(p)
        writable["instances"][0]["settings"]["host"] = "other"
        self.assertEqual(p.instances[0].settings["host"], "192.168.1.10")

    def test_missing_owner_and_mapping_reference(self):
        p = sample_project()
        self.assertIn("SIGNAL_INSTANCE", codes(replace(
            p, signals=(replace(p.signals[0], instance_id="missing"), p.signals[1]))))
        self.assertIn("MAPPING_REFERENCE", codes(replace(
            p, mappings=(replace(p.mappings[0], source_signal_id="unknown"),))))

    def test_fanout_valid_fanin_requires_policy(self):
        p = sample_project()
        dst = replace(p.signals[1], id="dst2", name="IEC104.VoltageB",
                      parameters={"common_address": 1, "ioa": 1002, "ti": 13})
        edge = replace(p.mappings[0], id="route2", destination_signal_id=dst.id)
        self.assertEqual(validate_project(replace(p, signals=p.signals + (dst,),
                                                  mappings=p.mappings + (edge,))), ())
        src2 = replace(p.signals[0], id="src2", name="Meter.VoltageB",
                       parameters={"register_table": "holding", "native_address": 2, "register_count": 2})
        edge2 = replace(p.mappings[0], id="route3", source_signal_id="src2")
        self.assertIn("FAN_IN", codes(replace(
            p, signals=p.signals + (src2,), mappings=p.mappings + (edge2,))))

    def test_duplicate_edges_cycle_and_invalid_direction(self):
        p = sample_project()
        duplicate = replace(p.mappings[0], id="duplicate")
        self.assertIn("DUPLICATE_EDGE", codes(replace(p, mappings=p.mappings + (duplicate,))))
        reverse = replace(p.mappings[0], id="reverse",
                          source_signal_id=p.signals[1].id,
                          destination_signal_id=p.signals[0].id)
        errors = codes(replace(p, mappings=p.mappings + (reverse,)))
        self.assertIn("MAPPING_CYCLE", errors)
        self.assertIn("SOURCE_DIRECTION", errors)

    def test_command_requires_explicit_intent(self):
        p = sample_project()
        target = replace(p.signals[1], family=SignalFamily.COMMAND)
        self.assertIn("COMMAND_AS_TELEMETRY", codes(replace(
            p, signals=(p.signals[0], target))))
        command = replace(p.mappings[0], kind=MappingKind.COMMAND)
        self.assertIn("COMMAND_POLICY", codes(replace(
            p, signals=(p.signals[0], target), mappings=(command,))))
        self.assertNotIn("COMMAND_POLICY", codes(replace(
            p, signals=(p.signals[0], target),
            mappings=(replace(command, request_control=True),))))

    def test_sample_requires_quality_and_aware_timestamp(self):
        signal = SignalSample("source", 229.8, Quality.TEST, SignalOrigin.SIMULATED, NOW, 3)
        self.assertEqual(signal.origin, SignalOrigin.SIMULATED)
        with self.assertRaises(ValueError):
            SignalSample("a", None, Quality.GOOD, SignalOrigin.PHYSICAL, NOW, 0)
        with self.assertRaises(ValueError):
            SignalSample("a", 1, Quality.GOOD, SignalOrigin.PHYSICAL, datetime(2026, 1, 1), 1)
        with self.assertRaises(ValueError):
            SignalSample("a", 1, Quality.GOOD, SignalOrigin.PHYSICAL, NOW, -1)

    def test_envelope_is_versioned_and_immutable(self):
        msg = Envelope(API_VERSION, "msg1", MessageKind.REQUEST, NOW, {"action": "health"})
        self.assertEqual(msg.to_json_object()["kind"], "request")
        with self.assertRaises(ValueError):
            Envelope("2.0", "msg1", MessageKind.REQUEST, NOW, {})
        with self.assertRaises(TypeError):
            msg.payload["action"] = "deploy"

    def test_decoder_rejects_schema_version_and_extra_fields(self):
        fixture = json.loads(FIXTURE.read_text())
        fixture["schema_version"] = 2
        with self.assertRaises(ValueError):
            decode_project(fixture)
        fixture["schema_version"] = 1
        fixture["auto_deploy"] = True
        with self.assertRaises(ValueError):
            decode_project(fixture)

    def test_iec104_address_collision_is_scoped_to_server_and_common_address(self):
        p = sample_project()
        collision = replace(p.signals[1], id="another", name="Another IEC104 point")
        self.assertIn("ADDRESS_COLLISION", codes(replace(p, signals=p.signals + (collision,))))
        another_ca = replace(collision, parameters={"common_address": 2, "ioa": 1001, "ti": 13})
        self.assertNotIn("ADDRESS_COLLISION", codes(replace(p, signals=p.signals + (another_ca,))))

    def test_modbus_server_register_overlap_and_client_aliasing(self):
        p = sample_project()
        server = replace(p.instances[0], role=type(p.instances[0].role).SERVER)
        original = replace(p.signals[0], direction=SignalDirection.OUTBOUND)
        overlap = replace(original, id="other", name="overlap",
                          parameters={"register_table": "holding", "native_address": 1, "register_count": 2})
        project = replace(p, instances=(server, p.instances[1]),
                          signals=(original, overlap, p.signals[1]))
        self.assertIn("ADDRESS_COLLISION", codes(project))
        original_client = replace(p.signals[0], direction=SignalDirection.INBOUND)
        alias_client = replace(original_client, id="alias", name="Alias")
        self.assertNotIn("ADDRESS_COLLISION", codes(replace(
            p, signals=(original_client, alias_client, p.signals[1]))))

    def test_contract_json_files_parse(self):
        for filename in ["project.v1.schema.json", "ipc.v1.schema.json"]:
            obj = json.loads((ROOT / "contracts" / filename).read_text())
            self.assertIn("$schema", obj)


if __name__ == "__main__":
    unittest.main()
