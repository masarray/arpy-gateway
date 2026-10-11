"""Studio bootstrap: offline demo until IPC and engineering store lanes integrate."""
from __future__ import annotations

import argparse
import sys
import os
from .demo import DemoProvider


def main(argv=None):
    parser = argparse.ArgumentParser(description="ARPY Gateway Studio engineering preview")
    parser.add_argument("--demo-rows", type=int, choices=(1000, 100000, 500000),
                        default=1000, help="virtual signals per instance")
    parser.add_argument("--screenshot", help="write screenshot PNG and exit (CI)")
    opts = parser.parse_args(argv)

    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication
    from .v2_window import StudioV2Window

    app = QApplication(sys.argv[:1])
    app.setApplicationName("ARPY Gateway Studio")
    app.setOrganizationName("ARPY")
    demo = DemoProvider()
    demo.resize_demo(opts.demo_rows)
    # Developer/CI fixture only: production runtime endpoints must be
    # provisioned securely by the installer + OS IPC service, never through
    # a GUI port/password form, command-line token or a runtime pip install.
    endpoint = None
    port_text = os.environ.get("ARPY_STUDIO_TEST_IPC_PORT", "")
    token = os.environ.get("ARPY_IPC_TEST_TOKEN", "")
    if port_text.isdecimal() and len(token) >= 32:
        port = int(port_text)
        if 1 <= port <= 65535:
            endpoint = (port, token)
    win = StudioV2Window(demo, runtime_endpoint=endpoint)
    win.show()
    if opts.screenshot:
        def capture():
            from pathlib import Path
            path = Path(opts.screenshot)
            path.parent.mkdir(parents=True, exist_ok=True)
            ok = win.grab().save(str(path), "PNG")
            app.exit(0 if ok else 2)
        QTimer.singleShot(1600, capture)
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
