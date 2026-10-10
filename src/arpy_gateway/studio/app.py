"""Studio bootstrap: offline demo until IPC and engineering store lanes integrate."""
from __future__ import annotations

import argparse
import sys
from .demo import DemoProvider


def main(argv=None):
    parser = argparse.ArgumentParser(description="ARPY Gateway Studio engineering preview")
    parser.add_argument("--demo-rows", type=int, choices=(1000, 100000, 500000),
                        default=1000, help="virtual signals per instance")
    parser.add_argument("--screenshot", help="write screenshot PNG and exit (CI)")
    opts = parser.parse_args(argv)

    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication
    from .window import StudioWindow

    app = QApplication(sys.argv[:1])
    app.setApplicationName("ARPY Gateway Studio")
    app.setOrganizationName("ARPY")
    demo = DemoProvider()
    demo.resize_demo(opts.demo_rows)
    win = StudioWindow(demo)
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
