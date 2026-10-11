"""Headless engineering diagnostic: real Modbus reads, no GUI and no DB writes.

This is NOT deployed runtime / privileged local IPC / persisted project support.
Use a reviewed JSON fixture to probe devices before the Store #3/#8 integration.
"""
from __future__ import annotations
import argparse
import asyncio
from datetime import datetime, timezone
import json
from pathlib import Path
from arpy_gateway.domain.models import (ProtocolInstance, ProtocolSignal,
   InstanceRole, SignalFamily, SignalDirection, ValueType)
from .adapter import ModbusClientAdapter
from .configuration import DeviceConfig


def load_probe_config(path: str):
    filename = Path(path)
    if filename.stat().st_size > 2_000_000:
        raise ValueError("Probe config cannot exceed 2 MB")
    content = json.loads(filename.read_text(encoding="utf-8"))
    if not isinstance(content, dict):
        raise ValueError("Expected JSON object")
    raw_devices = content.get("devices", [])
    raw_signals = content.get("signals", [])
    if len(raw_devices) > 128 or len(raw_signals) > 20_000:
        raise ValueError("Maximum 128 connections and 20k signals per diagnostic probe")
    spec = content["instance"]
    instance = ProtocolInstance(spec["id"], spec["name"], spec["protocol"],
        InstanceRole.CLIENT, bool(spec.get("enabled", True)), spec.get("settings", {}))
    devices = [DeviceConfig(instance_id=instance.id, **record) for record in raw_devices]
    signals = [ProtocolSignal(id=s["id"], instance_id=instance.id,
        name=s["name"], family=SignalFamily(s.get("family", "analog")),
        value_type=ValueType(s["value_type"]), direction=SignalDirection.INBOUND,
        enabled=bool(s.get("enabled", True)), unit=s.get("unit", ""),
        parameters=s["parameters"]) for s in raw_signals]
    return instance, devices, tuple(signals)


async def run(config, duration):
    instance, devices, signals = load_probe_config(config)
    adapter = ModbusClientAdapter(devices)
    await adapter.start(instance, signals)
    print(f"ARPY Modbus diagnostic · {instance.protocol} · {len(devices)} devices · "
          f"{len(signals)} points · READ ONLY · No config Save/Deploy", flush=True)
    try:
        start = asyncio.get_running_loop().time()
        while duration <= 0 or asyncio.get_running_loop().time() - start < duration:
            await asyncio.sleep(1.0)
            snapshots = adapter.latest_snapshot()
            statuses = {d.device_id:d for d in adapter.device_statuses()}
            print(f"UTC {datetime.now(timezone.utc).isoformat(timespec='seconds')} | "
                  f"received {len(snapshots)} | queue-overruns {adapter.dropped_notifications}")
            for d in devices:
                st = statuses[d.id]
                print(f"  {d.name:24} {st.state.value:11} ok={st.reads_ok:5} "
                      f"errors={st.read_errors:3} last={st.last_error}")
            for signal in signals[:50]:
                item = snapshots.get(signal.id)
                value = str(item.value) if item and item.value is not None else '—'
                quality = item.quality.value if item else 'unavailable'
                print(f"    {signal.name[:32]:32} {value[:20]:20} {quality}")
            if len(signals) > 50:
                print("    ... 50 rows shown; runtime retains latest by stable ID")
    finally:
        await adapter.stop()


def main(argv=None):
    parser = argparse.ArgumentParser(description="Modbus TCP/RTU diagnostic probe, never deploys")
    parser.add_argument("--config", required=True, help="Local reviewed JSON device/signal fixture")
    parser.add_argument("--seconds", type=float, default=20, help="0=until Ctrl+C")
    args = parser.parse_args(argv)
    try:
        asyncio.run(run(args.config, args.seconds))
    except KeyboardInterrupt:
        return 130
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
