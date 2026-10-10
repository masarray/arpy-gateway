"""Compact native spreadsheet UX for large industrial signal maps."""
from __future__ import annotations

import csv
import io
from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import (
    QApplication, QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout,
    QFrame, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QMainWindow, QMessageBox,
    QPushButton, QSpinBox, QStatusBar, QTableView, QToolBar, QVBoxLayout, QWidget,
    QAbstractItemView,
)

from . import store
from .model import SignalModel
from .workers import Workers


STYLE = """
QMainWindow, QWidget { background: #f6f8fb; color: #253344; font-family: 'Segoe UI', Arial; font-size: 12px; }
QToolBar { background: #ffffff; border: 0; border-bottom: 1px solid #e1e7ee; padding: 4px 7px; spacing: 6px; }
QToolButton { background: transparent; border: 1px solid transparent; border-radius: 5px; padding: 5px 9px; }
QToolButton:hover { background: #edf3f8; border-color: #d9e3ec; }
QLineEdit, QComboBox, QSpinBox { background: #ffffff; border: 1px solid #d1dbe5; border-radius: 5px; padding: 5px 7px; min-height: 19px; }
QLineEdit:focus, QComboBox:focus, QSpinBox:focus { border: 1px solid #2985b5; }
QPushButton { background: #ffffff; border: 1px solid #d4dde6; border-radius: 5px; padding: 6px 11px; }
QPushButton:hover { background: #eef5fa; }
QPushButton#primary { background: #187cad; color: white; border: 1px solid #187cad; }
QPushButton#primary:hover { background: #12688f; }
QTableView { background: #ffffff; alternate-background-color: #f8fafc; gridline-color: #e8edf2; border: 1px solid #dce4eb;
             selection-background-color: #d8eaf8; selection-color: #193b50; }
QTableView::item { padding: 2px 6px; }
QHeaderView::section { background: #f0f4f8; color: #405469; border: 0; border-right: 1px solid #e0e7ee;
                       border-bottom: 1px solid #d8e1e9; padding: 6px 7px; font-weight: 500; }
QStatusBar { background: #ffffff; border-top: 1px solid #e0e7ee; color: #526579; }
QLabel#muted { color: #768799; }
QFrame#topline { background: white; border-bottom: 1px solid #e0e7ee; }
"""


class NumberDialog(QDialog):
    def __init__(self, parent=None, total: int = 0):
        super().__init__(parent)
        self.setWindowTitle("Auto-number addresses")
        self.setMinimumWidth(340)
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.start_value = QSpinBox()
        self.start_value.setRange(0, 16_777_215)
        self.start_value.setValue(1001)
        self.step_value = QSpinBox()
        self.step_value.setRange(1, 1_000_000)
        form.addRow("First address", self.start_value)
        form.addRow("Increment", self.step_value)
        layout.addWidget(QLabel(f"Fill {total:,} selected rows. All changes can be undone."))
        layout.addLayout(form)
        controls = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        controls.accepted.connect(self.accept)
        controls.rejected.connect(self.reject)
        layout.addWidget(controls)


class StudioWindow(QMainWindow):
    def __init__(self, db_path: str):
        super().__init__()
        self.db_path = db_path
        self.setWindowTitle("ARPY Gateway Studio — Signal Engineering")
        self.resize(1420, 840)
        self.setMinimumSize(940, 530)
        self.setStyleSheet(STYLE)
        self.workers = Workers(self)
        self.workers.failed.connect(self._error)
        self.model = SignalModel(db_path, self.workers, self)
        self.model.edit_requested.connect(self._single_edit)
        self.model.status_changed.connect(self._status)
        self.model.count_changed.connect(self._count)
        self._build()

    def _build(self):
        bar = QToolBar("Engineering", self)
        bar.setMovable(False)
        bar.setFloatable(False)
        self.addToolBar(bar)
        self._action(bar, "New +100", self._add_rows, None)
        self._action(bar, "Demo 100K", lambda: self._seed(100_000), None)
        self._action(bar, "Demo 500K", lambda: self._seed(500_000), None)
        bar.addSeparator()
        self._action(bar, "Import CSV", self._import, None)
        self._action(bar, "Export CSV", self._export, None)
        bar.addSeparator()
        self._action(bar, "Copy", self._copy, "Ctrl+C")
        self._action(bar, "Paste", self._paste, "Ctrl+V")
        self._action(bar, "Undo", lambda: self._history(True), "Ctrl+Z")
        self._action(bar, "Redo", lambda: self._history(False), "Ctrl+Y")
        self._action(bar, "Fill Down", lambda: self._fill("down"), "Ctrl+D")
        self._action(bar, "Fill Right", lambda: self._fill("right"), "Ctrl+R")
        bar.addSeparator()
        self._action(bar, "Auto-number", self._auto_number, None)
        self._action(bar, "Refresh", self.model.refresh, "F5")
        outer = QWidget()
        self.setCentralWidget(outer)
        vertical = QVBoxLayout(outer)
        vertical.setContentsMargins(12, 10, 12, 8)
        vertical.setSpacing(8)
        heading = QHBoxLayout()
        titles = QVBoxLayout()
        titles.setSpacing(2)
        title = QLabel("Signal Mapping Editor")
        title.setStyleSheet("font-size: 16px; font-weight: 600; color: #233d51")
        titles.addWidget(title)
        subtitle = QLabel("Independent engineering workspace  ·  No protocol engine required")
        subtitle.setObjectName("muted")
        titles.addWidget(subtitle)
        heading.addLayout(titles, stretch=1)
        indicator = QLabel("●  EDITOR ONLY — OFFLINE")
        indicator.setStyleSheet("color: #587184; font-size: 11px; background: #eaf0f5; padding: 7px 9px; border-radius: 5px")
        heading.addWidget(indicator)
        vertical.addLayout(heading)
        filters = QHBoxLayout()
        filters.setSpacing(8)
        filters.addWidget(QLabel("Protocol"))
        self.protocol = QComboBox()
        self.protocol.addItems(["All", "Modbus TCP", "Modbus RTU", "IEC-104", "IEC 61850", "OPC UA", "MQTT"])
        self.protocol.currentTextChanged.connect(lambda x: self.model.change_view(protocol=x))
        filters.addWidget(self.protocol)
        filters.addWidget(QLabel("Search"))
        self.search = QLineEdit()
        self.search.setPlaceholderText("Find signal, device, address or description…  Ctrl+F")
        self.search.setClearButtonEnabled(True)
        self.search.setMaximumWidth(510)
        self.search_timer = QTimer(self)
        self.search_timer.setSingleShot(True)
        self.search_timer.setInterval(300)
        self.search_timer.timeout.connect(lambda: self.model.change_view(search=self.search.text()))
        self.search.textChanged.connect(lambda _: self.search_timer.start())
        filters.addWidget(self.search, stretch=1)
        self.count_text = QLabel("Loading…")
        self.count_text.setObjectName("muted")
        filters.addWidget(self.count_text)
        vertical.addLayout(filters)
        self.table = QTableView(self)
        self.table.setModel(self.model)
        self.table.setAlternatingRowColors(True)
        self.table.setShowGrid(True)
        self.table.setSortingEnabled(True)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectItems)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.table.setEditTriggers(
            QAbstractItemView.EditTrigger.DoubleClicked |
            QAbstractItemView.EditTrigger.EditKeyPressed |
            QAbstractItemView.EditTrigger.AnyKeyPressed)
        self.table.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.table.setHorizontalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.table.setWordWrap(False)
        self.table.verticalHeader().setDefaultSectionSize(25)
        self.table.verticalHeader().setMinimumSectionSize(22)
        self.table.horizontalHeader().setSectionsMovable(False)  # positional paste remains deterministic
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        self.table.horizontalHeader().setMinimumSectionSize(65)
        for index, col in enumerate(store.COLUMNS):
            self.table.setColumnWidth(index, col.width)
        vertical.addWidget(self.table, stretch=1)
        helper = QLabel("TIP  ·  Select a range → Ctrl+C / Ctrl+V  ·  Ctrl+D / Ctrl+R  ·  Double-click to edit  ·  Click column header to sort")
        helper.setObjectName("muted")
        vertical.addWidget(helper)
        self.status = QStatusBar()
        self.setStatusBar(self.status)
        self.status.showMessage(f"Database: {self.db_path}")
        find = QAction(self)
        find.setShortcut(QKeySequence("Ctrl+F"))
        find.triggered.connect(self.search.setFocus)
        self.addAction(find)
        # Ctrl+H is reserved for dedicated Find/Replace (not silently mapped to Search).

    def _action(self, bar, title, fn, shortcut):
        action = QAction(title, self)
        if shortcut:
            action.setShortcut(QKeySequence(shortcut))
            action.setShortcutContext(Qt.ShortcutContext.WindowShortcut)
        action.triggered.connect(fn)
        bar.addAction(action)
        return action

    def _status(self, msg):
        self.status.showMessage(msg, 8000)

    def _count(self, count):
        self.count_text.setText(f"{count:,} signals")
        self._status(f"Ready · {count:,} visible signals")

    def _error(self, message):
        # Show the first line inline; a modal dialog allows reading full backend exceptions.
        self._status("Error: " + message.splitlines()[0])
        QMessageBox.warning(self, "Operation failed", message)

    def _write(self, title, fn):
        self._status(title + " — processing in background…")
        self.workers.write(fn, callback=lambda result: self._write_done(title, result))

    def _write_done(self, title, result):
        self.model.refresh()
        self._status(f"{title} · {result:,} cells/rows processed" if isinstance(result, int) else f"{title} · {result}")

    def _single_edit(self, row, col, value):
        view = self.model.view
        generation = self.model.generation
        # Avoid model reset: spreadsheet selection remains stable while editing.
        self.workers.write(
            lambda: store.apply_grid(self.db_path, view, row, col, [[value]], "Edit cell"),
            callback=lambda _: self.model.confirm_edit(row, col, value, generation),
            error=lambda msg: self._failed_edit(row, col, msg),
        )

    def _failed_edit(self, row, col, message):
        self.model.cancel_edit(row, col)
        self._error(message)

    def _selected_rect(self):
        selections = self.table.selectionModel().selection()
        if len(selections) != 1:
            self._status("Select one contiguous rectangular range")
            return None
        region = selections[0]
        return region.top(), region.bottom(), region.left(), region.right()

    def _copy(self):
        rect = self._selected_rect()
        if rect is None:
            return
        top, bottom, left, right = rect
        view = self.model.view
        self._status("Copying selection…")
        self.workers.read(lambda: store.copy_tsv(self.db_path, view, top, bottom, left, right),
                          callback=lambda content: self._copy_done(content))

    def _copy_done(self, content):
        QApplication.clipboard().setText(content)
        self._status(f"Copied {len(content):,} characters to clipboard")

    def _paste(self):
        index = self.table.currentIndex()
        if not index.isValid():
            self._status("Select the first destination cell")
            return
        raw = QApplication.clipboard().text()
        if not raw:
            return
        try:
            parsed = list(csv.reader(io.StringIO(raw), dialect="excel-tab"))
            if not parsed:
                return
            # Trim Excel's trailing newline implicitly handled by csv.reader.
            width = max(map(len, parsed))
            if width == 0 or any(len(row) != width for row in parsed):
                raise ValueError("Clipboard range is not rectangular")
            rect = self._selected_rect()
            if rect and len(parsed) == 1 and width == 1:
                top, bottom, left, right = rect
                cells = (bottom - top + 1) * (right - left + 1)
                if cells > store.WRITE_CELL_LIMIT:
                    raise ValueError("Selection exceeds 500,000 cells")
                parsed = [parsed[0] * (right - left + 1) for _ in range(bottom - top + 1)]
            else:
                top, left = index.row(), index.column()
            if len(parsed) * len(parsed[0]) > store.WRITE_CELL_LIMIT:
                raise ValueError("Paste exceeds 500,000 cells")
            view = self.model.view
            self._write("Paste", lambda: store.apply_grid(self.db_path, view, top, left, parsed))
        except (ValueError, csv.Error) as exc:
            self._error(str(exc))

    def _history(self, undo):
        action = "Undo" if undo else "Redo"
        self._status(action + " — processing…")
        self.workers.write(lambda: store.history_step(self.db_path, undo),
                           callback=lambda result: self._history_done(action, result))

    def _history_done(self, action, result):
        count, label = result
        if count:
            self.model.refresh()
        self._status(f"{action} · {label} ({count:,} cells)")

    def _fill(self, direction):
        rect = self._selected_rect()
        if rect is None:
            return
        top, bottom, left, right = rect
        view = self.model.view
        self._write("Fill " + direction.title(),
                    lambda: store.fill_cells(self.db_path, view, top, bottom, left, right, direction))

    def _auto_number(self):
        rect = self._selected_rect()
        if rect is None:
            return
        top, bottom, left, right = rect
        if left != right or store.FIELDS[left] not in ("source_address", "target_address"):
            self._status("Select one column of Source Address or Target Address cells")
            return
        dialog = NumberDialog(self, bottom - top + 1)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        start, step = dialog.start_value.value(), dialog.step_value.value()
        view = self.model.view
        field = store.FIELDS[left]
        self._write("Auto-number", lambda: store.auto_number(self.db_path, view, top, bottom, field, start, step))

    def _seed(self, count):
        choice = QMessageBox.question(self, "Replace demo data?",
            f"Replace ALL signals in this database with {count:,} generated demo signals?\n\n"
            "This resets the editor undo history. Export your edits first.")
        if choice != QMessageBox.StandardButton.Yes:
            return
        self._write(f"Generate {count:,} demo signals", lambda: store.seed_demo(self.db_path, count))

    def _add_rows(self):
        self._write("Add 100 signals", lambda: store.add_signals(self.db_path, 100))

    def _import(self):
        path, _ = QFileDialog.getOpenFileName(self, "Import signals", "", "CSV files (*.csv)")
        if path:
            self._write("Import CSV", lambda: store.import_csv(self.db_path, path))

    def _export(self):
        path, _ = QFileDialog.getSaveFileName(self, "Export visible signals", "signals.csv", "CSV files (*.csv)")
        if path:
            view = self.model.view
            self._status("Exporting CSV in background…")
            self.workers.read(lambda: store.export_csv(self.db_path, path, view),
                              callback=lambda count: self._status(f"Exported {count:,} signals to {path}"))
