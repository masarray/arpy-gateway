"""SQLite-backed engineering IED editor, isolated from offline demo peers.

No protocol sockets, network probes, runtime deploy or Qt-thread SQLite access.
Uses the public proposed ARCH #13 DTO and STORE #3 v2 transactional API from
pinned PRs. All database IO is delegated to Studio's bounded WorkerHub writer.
This is a Windows-first engineering candidate, not a shipped field gateway.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
import os
from pathlib import Path
from uuid import uuid4

from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QDialog, QDialogButtonBox, QFormLayout,
    QHBoxLayout, QLabel, QLineEdit, QPushButton, QSpinBox, QTableView,
    QVBoxLayout, QWidget,
)

_INSTANCE = "arpy-modbus-tcp-master"
_PROJECT = "local-gateway"
_PAGE = 128


def default_engineering_database() -> Path:
    """User-writable path automatically resolved, never a PowerShell step."""
    if os.name == "nt":
        root = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
    else:
        root = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share")
    return root / "ARPY" / "Gateway Studio" / "engineering.sqlite3"


@dataclass(frozen=True)
class TCPDeviceEdit:
    name: str
    host: str
    port: int = 502
    unit_id: int = 1
    poll_interval_ms: int = 1000
    enabled: bool = True

    def normalized(self):
        name, host = self.name.strip(), self.host.strip()
        if not name or len(name) > 128:
            raise ValueError("IED name must contain 1–128 characters")
        if not 1 <= len(host) <= 253 or any(c.isspace() or ord(c) < 33 for c in host):
            raise ValueError("Valid Modbus TCP IP / hostname required")
        for key, number, lo, hi in (
            ("port", self.port, 1, 65535),
            ("unit_id", self.unit_id, 1, 247),
            ("poll_interval_ms", self.poll_interval_ms, 50, 3_600_000),
        ):
            if type(number) is not int or not lo <= number <= hi:
                raise ValueError(f"{key} out of range")
        if type(self.enabled) is not bool:
            raise ValueError("enabled must be boolean")
        return replace(self, name=name, host=host)


class TCPDeviceRepository:
    """Pure worker-thread facade over proposed SQLite v2 Store.

    The editor never shares a SQLite handle with the runtime process. Each
    request opens/closes its own Store on the single bounded write worker.
    Existing v1 databases are explicitly refused rather than silently upgraded.
    """

    def __init__(self, path: Path):
        self.path = Path(path)
        self.project_id = _PROJECT
        self.instance_id = _INSTANCE

    def _store(self):
        from arpy_gateway.engineering.store_v2 import EngineeringStoreV2
        return EngineeringStoreV2(self.path)

    def prepare(self):
        from arpy_gateway.domain.models import ProtocolInstance, InstanceRole
        # The only auto-migration is on a brand-new database owned by this
        # Studio setup. Existing v1 files require explicit reviewed migration.
        is_new = not self.path.exists()
        with self._store() as store:
            if is_new:
                store.migrate_v2()
                store.create_project(self.project_id)
                store.apply_v2(
                    self.project_id, expected_generation=0,
                    instances=(ProtocolInstance(
                        _INSTANCE, "Modbus TCP Master", "modbus_tcp",
                        InstanceRole.CLIENT, True, {}),),
                    actor="studio", reason="Initialize empty engineering project",
                )
            else:
                if getattr(store, "_db_version", None) != 2:
                    raise RuntimeError(
                        "Existing v1 database needs approved backup and migration")
                # An existing project is not altered by simply opening Studio.
                store.generation(self.project_id)
            return store.generation(self.project_id)

    def page(self, *, after_id="", limit=_PAGE):
        with self._store() as store:
            generation = store.generation(self.project_id)
            rows = store.communication_point_page(
                self.project_id, _INSTANCE, after_id=after_id,
                limit=min(limit + 1, 256))
        return {"generation": generation, "rows": rows[:limit],
                "has_next": len(rows) > limit}

    def save(self, details: TCPDeviceEdit, *, expected_generation: int,
             original=None):
        from arpy_gateway.domain.v2 import CommunicationPoint
        edited = details.normalized()
        if original is None:
            identity = "tcp-" + uuid4().hex
            point = CommunicationPoint(
                identity, _INSTANCE, edited.name, "remote_device",
                edited.enabled, {
                    "host": edited.host, "port": edited.port,
                    "unit_id": edited.unit_id,
                    "poll_interval_ms": edited.poll_interval_ms,
                })
        else:
            if original.instance_id != _INSTANCE or original.kind != "remote_device":
                raise ValueError("Incorrect Modbus instance/device type")
            point = replace(original, name=edited.name, enabled=edited.enabled,
                            settings={
                                **dict(original.settings),
                                "host": edited.host, "port": edited.port,
                                "unit_id": edited.unit_id,
                                "poll_interval_ms": edited.poll_interval_ms,
                            })
        with self._store() as store:
            gen = store.apply_v2(
                self.project_id, expected_generation=expected_generation,
                communication_points=(point,), actor="studio",
                reason="Save Modbus TCP IED draft")
        return {"generation": gen, "id": point.id}

    def stage(self, *, expected_generation: int):
        with self._store() as store:
            if store.count_signals(self.project_id, _INSTANCE) == 0:
                raise ValueError(
                    "No configured signals: Save Draft is complete, "
                    "but a runtime-ready revision cannot be staged yet")
            record = store.stage_v2(
                self.project_id, expected_generation=expected_generation)
        return {"digest": record.digest,
                "generation": record.draft_generation}


class DevicesModel(QAbstractTableModel):
    COLUMNS = ("IED Name", "Host / IP", "Port", "Unit ID", "Poll ms", "Enabled", "Stable ID")

    def __init__(self, parent=None):
        super().__init__(parent)
        self.rows = ()

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.rows)

    def columnCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.COLUMNS)

    def headerData(self, index, orientation, role=Qt.ItemDataRole.DisplayRole):
        if role != Qt.ItemDataRole.DisplayRole:
            return None
        return (self.COLUMNS[index] if orientation == Qt.Orientation.Horizontal
                else str(index+1))

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or role not in (
                Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.ToolTipRole):
            return None
        point = self.rows[index.row()]
        values = (point.name, point.settings.get("host", ""),
                  point.settings.get("port", ""), point.settings.get("unit_id", ""),
                  point.settings.get("poll_interval_ms", ""), point.enabled, point.id)
        return str(values[index.column()])

    def set_rows(self, items):
        self.beginResetModel()
        self.rows = tuple(items)[:_PAGE]
        self.endResetModel()


class TCPDeviceDialog(QDialog):
    def __init__(self, current=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Edit Modbus TCP IED" if current else "Add Modbus TCP IED")
        self.setMinimumWidth(420)
        layout = QVBoxLayout(self)
        form = QFormLayout()
        settings = current.settings if current is not None else {}
        self.name = QLineEdit(current.name if current else "")
        self.name.setMaxLength(128)
        self.name.setPlaceholderText("IED name (e.g. Power Meter 01)")
        self.host = QLineEdit(str(settings.get("host", "")))
        self.host.setPlaceholderText("192.168.1.10 or meter.example")
        self.port = QSpinBox()
        self.port.setRange(1, 65535)
        self.port.setValue(int(settings.get("port", 502)))
        self.unit_id = QSpinBox()
        self.unit_id.setRange(1, 247)
        self.unit_id.setValue(int(settings.get("unit_id", 1)))
        self.poll = QSpinBox()
        self.poll.setRange(50, 3_600_000)
        self.poll.setSuffix(" ms")
        self.poll.setValue(int(settings.get("poll_interval_ms", 1000)))
        self.enabled = QCheckBox("Enable in future deployed revision")
        self.enabled.setChecked(current.enabled if current else True)
        for key, widget in (
            ("Name", self.name), ("IP / Hostname", self.host),
            ("TCP Port", self.port), ("Unit ID", self.unit_id),
            ("Poll Interval", self.poll), ("Enabled", self.enabled),
        ):
            form.addRow(key, widget)
        layout.addLayout(form)
        hint = QLabel("Save Draft only · no connection or control will be sent to this IED")
        hint.setWordWrap(True)
        layout.addWidget(hint)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save |
            QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def details(self):
        return TCPDeviceEdit(self.name.text(), self.host.text(),
                             self.port.value(), self.unit_id.value(),
                             self.poll.value(), self.enabled.isChecked())


class SavedDevicesPanel(QWidget):
    """Canonical persisted Modbus peer editor for Protocol Config."""

    devices_changed = Signal(object)

    def __init__(self, hub, database_path, notify, parent=None):
        super().__init__(parent)
        self.repo = TCPDeviceRepository(Path(database_path))
        self.hub, self.notify = hub, notify
        self.generation = None
        self._busy = False
        self._initialized = False
        self._cursors = [""]
        self._page = 0
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        toolbar = QHBoxLayout()
        self.add = QPushButton("+ Add TCP IED")
        self.edit = QPushButton("Edit Selected")
        self.previous = QPushButton("Previous")
        self.next = QPushButton("Next")
        self.refresh = QPushButton("Refresh")
        self.stage = QPushButton("Validate & Stage")
        for button in (self.add, self.edit, self.previous,
                       self.next, self.refresh, self.stage):
            toolbar.addWidget(button)
        toolbar.addStretch()
        layout.addLayout(toolbar)
        self.status = QLabel("NOT LOADED · Saved engineering draft ≠ active gateway")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.model = DevicesModel(self)
        self.table = QTableView(self)
        self.table.setModel(self.model)
        self.table.selectionModel().currentChanged.connect(
            lambda *_: self._sync_buttons())
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().setDefaultSectionSize(23)
        self.table.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(self.table, 1)
        self.footer = QLabel("ID-based SQLite v2 draft · No live Deploy · No physical write")
        layout.addWidget(self.footer)
        self.add.clicked.connect(self.add_device)
        self.edit.clicked.connect(self.edit_device)
        self.refresh.clicked.connect(self.load)
        self.previous.clicked.connect(self.prev_page)
        self.next.clicked.connect(self.next_page)
        self.stage.clicked.connect(self.stage_revision)
        self._sync_buttons()

    def activate(self):
        if not self._initialized and not self._busy:
            self._run(self.repo.prepare, self._prepared)

    def _prepared(self, generation):
        self._initialized = True
        self.generation = generation
        self.load()

    def _run(self, work, done):
        if self._busy:
            return
        self._busy = True
        self._sync_buttons()

        def success(data):
            self._busy = False
            done(data)
            self._sync_buttons()

        def failure(message):
            self._busy = False
            # The first line is the domain exception, not the worker traceback.
            # A stale generation, migration gate or missing signals is an
            # actionable configuration condition, never an active Deploy.
            first_line = str(message).splitlines()[0][:180]
            self.status.setText("NOT SAVED / NOT DEPLOYED · " + first_line)
            self._sync_buttons()

        if not self.hub.submit(work, success, write=True, error=failure):
            self._busy = False
            self._sync_buttons()

    def _sync_buttons(self):
        ready = self._initialized and not self._busy
        for button in (self.add, self.refresh, self.stage):
            button.setEnabled(ready)
        self.edit.setEnabled(ready and self.selected() is not None)
        self.previous.setEnabled(ready and self._page > 0)
        self.next.setEnabled(ready and hasattr(self, "_has_next") and self._has_next)

    def selected(self):
        idx = self.table.currentIndex()
        if not idx.isValid() or idx.row() >= len(self.model.rows):
            return None
        return self.model.rows[idx.row()]

    def load(self):
        if not self._initialized or self._busy:
            return
        cursor = self._cursors[self._page]
        self._run(lambda: self.repo.page(after_id=cursor), self._loaded)

    def _loaded(self, packet):
        self.generation = packet["generation"]
        self.model.set_rows(packet["rows"])
        self._has_next = packet["has_next"]
        self.devices_changed.emit(tuple(packet["rows"]))
        self.status.setText(
            f"SAVED DRAFT · Revision generation {self.generation} · "
            f"{len(packet['rows'])} visible IED(s) · NOT DEPLOYED")
        self._sync_buttons()

    def next_page(self):
        if self._busy or not getattr(self, "_has_next", False) or not self.model.rows:
            return
        cursor = self.model.rows[-1].id
        self._cursors = self._cursors[:self._page+1] + [cursor]
        self._page += 1
        self.load()

    def prev_page(self):
        if self._busy or self._page <= 0:
            return
        self._page -= 1
        self.load()

    def add_device(self):
        self._edit(None)

    def edit_device(self):
        selected = self.selected()
        if selected is not None:
            self._edit(selected)

    def select_device(self, point_id):
        """Navigate from an Explorer node to the canonical persisted IED."""
        for index, point in enumerate(self.model.rows):
            if point.id == point_id:
                self.table.selectRow(index)
                return True
        return False

    def _edit(self, original):
        if self._busy or not self._initialized:
            return
        dialog = TCPDeviceDialog(original, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            data = dialog.details().normalized()
        except ValueError:
            self.status.setText("INVALID DEVICE · Check name, host and address ranges")
            return
        gen = self.generation

        def saved(result):
            self.generation = result["generation"]
            self.status.setText("SAVED DRAFT · Changes are NOT deployed")
            self._page = 0
            self._cursors = [""]
            self.load()

        self._run(lambda: self.repo.save(data,expected_generation=gen,
                                         original=original),saved)

    def stage_revision(self):
        if not self._initialized or self._busy:
            return
        gen = self.generation
        def staged(result):
            self.status.setText(
                "VALIDATED/STAGED · Revision " + result["digest"][:12] +
                " · NOT DEPLOYED")
        self._run(lambda: self.repo.stage(expected_generation=gen),staged)

    def shutdown(self):
        # WorkerHub closes separately; no SQLite handle is owned by this QWidget.
        pass
