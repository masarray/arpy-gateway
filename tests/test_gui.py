"""Offscreen Qt smoke test (skipped when the optional GUI dependency is absent)."""
import importlib.util
import os
import tempfile
import unittest
from pathlib import Path


@unittest.skipUnless(importlib.util.find_spec("PySide6") is not None, "PySide6 not installed")
class GuiSmoke(unittest.TestCase):
    def test_editor_window_initialization(self):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtWidgets import QApplication
        from arpy_gateway.studio.store import initialize
        from arpy_gateway.studio.window import StudioWindow
        app = QApplication.instance() or QApplication([])
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "studio.sqlite"
            initialize(path)
            win = StudioWindow(str(path))
            self.assertEqual(win.model.columnCount(), 13)
            self.assertIsNotNone(win.table.model())
            win.show()
            app.processEvents()
            win.close()
            app.processEvents()


if __name__ == "__main__":
    unittest.main()
