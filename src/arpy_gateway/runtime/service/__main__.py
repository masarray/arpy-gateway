"""Issue #8 headless service lifecycle spike; NOT a registered OS service.

For CI/developers only: ARPY_IPC_TEST_TOKEN must be supplied by secure
test harness. No PySide6 and no PyModbus dependency. Platform SCM/systemd
registration and real Modbus binding are separate acceptance gates.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import signal

from arpy_gateway.ipc.server import LocalReadOnlyServer
from .fixture import FixtureRuntime


async def run(port: int, *, period: float = 0.05):
    token = os.environ.get("ARPY_IPC_TEST_TOKEN", "")
    if len(token) < 32:
        raise ValueError("No IPC test credential supplied; refusing unsecured runtime")
    runtime = FixtureRuntime(period=period)
    server = LocalReadOnlyServer(runtime, token=token)
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, stop.set)
        except (NotImplementedError, RuntimeError):
            signal.signal(sig, lambda *_: loop.call_soon_threadsafe(stop.set))
    try:
        await runtime.start()
        port = await server.start(port)
        # Ready only after a headless loop actually runs; stdout is used by
        # test harness, NOT by the future user-facing Studio.
        print(json.dumps({"event": "ARPY_FIXTURE_READY",
                          "port": port, "source": "DETERMINISTIC_FIXTURE_ONLY"}),
              flush=True)
        await stop.wait()
    finally:
        await server.stop()
        await runtime.stop()


def main(argv=None):
    parser = argparse.ArgumentParser(description="ARPY headless IPC fixture (developer only)")
    parser.add_argument("--port", type=int, default=0, help="loopback listening port (0=ephemeral)")
    parser.add_argument("--period-ms", type=int, default=50)
    args = parser.parse_args(argv)
    if not 10 <= args.period_ms <= 10000:
        parser.error("period-ms outside allowed range")
    try:
        asyncio.run(run(args.port, period=args.period_ms / 1000))
    except KeyboardInterrupt:
        return 130
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
