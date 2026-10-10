"""Desktop application bootstrap. Never imports or launches protocol services."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .store import initialize


def main():
    parser = argparse.ArgumentParser(description="ARPY Gateway Studio signal editor")
    parser.add_argument("--database", type=Path, help="SQLite engineering project file")
    opts = parser.parse_args()

    from PySide6.QtCore import QStandardPaths
    from PySide6.QtWidgets import QApplication
    from .window import StudioWindow

    app = QApplication(sys.argv[:1])
    app.setApplicationName("ARPY Gateway Studio")
    app.setOrganizationName("ARPY")
    root = Path(QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppDataLocation))
    database = opts.database or root / "signals.sqlite3"
    initialize(database)
    win = StudioWindow(str(database))
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
