# ADR-0001: Studio and runtime are separate OS processes

**Status:** Accepted, v1.

**Decision:** PySide6 Studio is optional engineering/control-plane UI. Headless Gateway Runtime independently owns sockets, protocol event loops, live signal state, simulation injection, deployed revision and telemetry. Communication uses versioned IPC; no direct Studio imports of protocol adapters.

**Why:** closing the GUI must not stop SCADA connections; deterministic failure domain; cross-platform service deployment; prevent Qt event loop blockage.

**Consequence:** IPC compatibility/auth, runtime service lifecycle, snapshot recovery and observability are mandatory. GUI workers only perform engineering/IPC tasks, not protocol I/O. No claim of implemented IPC transport in P0.
