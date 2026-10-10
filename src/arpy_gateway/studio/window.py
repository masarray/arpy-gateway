"""Compact six-workspace engineering GUI; no protocol/network runtime imports.

All dataset work runs through WorkerHub. Demo storage is session-only until
engineering repository issue #3 supplies transactional persistent backing.
"""
from __future__ import annotations

from PySide6.QtCore import Qt, QAbstractTableModel, QModelIndex, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFormLayout,
    QFrame, QGridLayout, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QListWidget,
    QMainWindow, QMessageBox, QPushButton, QSpinBox, QDoubleSpinBox, QSplitter,
    QStackedWidget, QTableView, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)
from arpy_gateway.domain.models import InstanceRole
from .demo import DemoProvider
from .grid import PAGE, SignalGrid, VirtualSignals
from .workers import WorkerHub


def heading(text: str, subtitle: str = ""):
    box = QWidget()
    layout = QVBoxLayout(box)
    layout.setContentsMargins(0, 0, 0, 5)
    layout.setSpacing(2)
    title = QLabel(text)
    title.setObjectName("PageTitle")
    layout.addWidget(title)
    if subtitle:
        minor = QLabel(subtitle)
        minor.setObjectName("Muted")
        minor.setWordWrap(True)
        layout.addWidget(minor)
    return box


def layout_page(title, description):
    page = QWidget()
    layout = QVBoxLayout(page)
    layout.setContentsMargins(18, 14, 18, 12)
    layout.setSpacing(10)
    layout.addWidget(heading(title, description))
    return page, layout


class EndpointDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Add Protocol Instance")
        self.name = QLineEdit("New Endpoint")
        self.protocol = QComboBox()
        self.protocol.addItem("Modbus TCP", "modbus_tcp")
        self.protocol.addItem("Modbus RTU", "modbus_rtu")
        self.protocol.addItem("IEC 60870-5-104", "iec104")
        self.role = QComboBox()
        self.role.addItem("Client / Master", InstanceRole.CLIENT)
        self.role.addItem("Server / Slave", InstanceRole.SERVER)
        form = QFormLayout(self)
        form.addRow("Instance name", self.name)
        form.addRow("Protocol", self.protocol)
        form.addRow("Role", self.role)
        actions = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok |
                                   QDialogButtonBox.StandardButton.Cancel)
        actions.accepted.connect(self.accept)
        actions.rejected.connect(self.reject)
        form.addRow(actions)


class GatewayPage(QWidget):
    changed = Signal()

    def __init__(self, demo, hub, notify, parent=None):
        super().__init__(parent)
        self.demo, self.hub, self.notify = demo, hub, notify
        page = QVBoxLayout(self)
        page.setContentsMargins(18, 14, 18, 12)
        page.setSpacing(10)
        page.addWidget(heading("Gateway Config",
            "Create protocol instances and enable roles. This is an OFFLINE engineering draft."))
        bar = QHBoxLayout()
        add = QPushButton("+ Add Protocol Instance")
        add.clicked.connect(self.add_instance)
        bar.addWidget(add)
        bar.addStretch()
        page.addLayout(bar)
        self.table = QTableWidget()
        self.table.setColumnCount(5)
        self.table.setHorizontalHeaderLabels(["Enabled", "Instance", "Protocol", "Role", "Instance ID"])
        self.table.verticalHeader().setDefaultSectionSize(27)
        self.table.setAlternatingRowColors(True)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.itemChanged.connect(self.item_changed)
        page.addWidget(self.table)
        footer = QLabel("Only enabled instances are candidates for future deployment. "
                        "Studio never opens protocol sockets.")
        footer.setObjectName("Muted")
        page.addWidget(footer)
        self.refresh()

    def refresh(self):
        self.table.blockSignals(True)
        items = self.demo.instances()
        self.table.setRowCount(len(items))
        for row, inst in enumerate(items):
            check = QTableWidgetItem()
            check.setFlags(Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsEnabled |
                           Qt.ItemFlag.ItemIsUserCheckable)
            check.setCheckState(Qt.CheckState.Checked if inst.enabled else Qt.CheckState.Unchecked)
            check.setData(Qt.ItemDataRole.UserRole, inst.id)
            self.table.setItem(row, 0, check)
            for col, text in enumerate((inst.name, inst.protocol, inst.role.value, inst.id), 1):
                self.table.setItem(row, col, QTableWidgetItem(text))
        self.table.blockSignals(False)

    def item_changed(self, item):
        if item.column() != 0:
            return
        iid = item.data(Qt.ItemDataRole.UserRole)
        enabled = item.checkState() == Qt.CheckState.Checked
        self.hub.submit(lambda: self.demo.set_enabled(iid, enabled),
                        lambda _: (self.notify(f"{iid}: draft enabled={enabled}"), self.changed.emit()),
                        write=True, error=self.notify)

    def add_instance(self):
        dialog = EndpointDialog(self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        name, protocol, role = dialog.name.text(), dialog.protocol.currentData(), dialog.role.currentData()
        self.hub.submit(lambda: self.demo.add_instance(name, protocol, role),
                        lambda _: (self.refresh(), self.changed.emit(),
                                   self.notify("Protocol instance created (session-only draft)")),
                        write=True, error=self.notify)


class ProtocolConfigPage(QWidget):
    changed = Signal()
    def __init__(self, demo, hub, notify, parent=None):
        super().__init__(parent)
        self.demo, self.hub, self.notify = demo, hub, notify
        page = QVBoxLayout(self)
        page.setContentsMargins(18, 14, 18, 12)
        page.setSpacing(10)
        page.addWidget(heading("Protocol Config",
                "Edit communication parameters per instance, not signal addresses."))
        bar = QHBoxLayout()
        self.instances = QComboBox()
        self.instances.currentIndexChanged.connect(self.build_form)
        bar.addWidget(QLabel("Protocol instance"))
        bar.addWidget(self.instances, 1)
        page.addLayout(bar)
        panel = QWidget()
        self.form = QFormLayout(panel)
        self.form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.FieldsStayAtSizeHint)
        page.addWidget(panel)
        page.addStretch()
        self.refresh()

    def refresh(self):
        selected = self.instances.currentData()
        self.instances.blockSignals(True)
        self.instances.clear()
        for i in self.demo.instances():
            self.instances.addItem(f"{i.name} [{i.role.value}]", i.id)
        idx = self.instances.findData(selected)
        self.instances.setCurrentIndex(idx if idx >= 0 else 0)
        self.instances.blockSignals(False)
        self.build_form()

    def build_form(self):
        while self.form.rowCount():
            self.form.removeRow(0)
        iid = self.instances.currentData()
        if not iid:
            return
        inst = self.demo.instance(iid)
        for key, value in inst.settings.items():
            entry = QLineEdit(str(value))
            entry.setMinimumWidth(220)
            entry.setMaximumWidth(350)
            entry.editingFinished.connect(
                lambda iid=iid, key=key, edit=entry: self.save(iid, key, edit))
            self.form.addRow(key.replace("_", " ").title(), entry)
        note = QLabel("Values are changed only in session draft. No socket reconnect is triggered.")
        note.setObjectName("Muted")
        self.form.addRow(note)

    def save(self, instance, field, editor):
        value = editor.text()
        self.hub.submit(lambda: self.demo.set_setting(instance, field, value),
                        lambda _: (self.notify(f"Updated {field} in offline draft"), self.changed.emit()),
                        write=True, error=self.notify)


class ProtocolSignalsPage(QWidget):
    def __init__(self, demo, hub, notify, parent=None):
        super().__init__(parent)
        self.demo, self.hub, self.notify = demo, hub, notify
        page = QVBoxLayout(self)
        page.setContentsMargins(14, 11, 14, 10)
        page.setSpacing(8)
        page.addWidget(heading("Protocol Signals",
            "Dedicated protocol fields · virtualized table · stable signal IDs · Excel-like editing"))
        bar = QHBoxLayout()
        self.instances = QComboBox()
        self.instances.currentIndexChanged.connect(self.instance_changed)
        bar.addWidget(self.instances, 2)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Find exact # / signal number")
        self.search.returnPressed.connect(self.search_changed)
        bar.addWidget(self.search, 2)
        find = QPushButton("Find")
        find.clicked.connect(self.search_changed)
        bar.addWidget(find)
        self.sizes = QComboBox()
        for size in (1_000, 100_000, 500_000):
            self.sizes.addItem(f"{size:,} demo rows", size)
        initial_size = self.sizes.findData(self.demo.count)
        self.sizes.setCurrentIndex(max(0, initial_size))
        self.sizes.currentIndexChanged.connect(self.size_changed)
        bar.addWidget(self.sizes)
        page.addLayout(bar)

        self.model = VirtualSignals(self.demo, self.hub, "meter01", self)
        self.grid = SignalGrid(self.model, self)
        self.grid.info.connect(self.notify)
        self.grid.failure.connect(self.notify)
        self.model.info.connect(self.notify)
        self.model.failure.connect(self.notify)
        page.addWidget(self.grid, 1)
        bottom = QHBoxLayout()
        self.counter = QLabel("Loading...")
        bottom.addWidget(self.counter)
        bottom.addStretch()
        increment = QPushButton("Auto-number selected")
        increment.clicked.connect(self.grid.auto_number)
        bottom.addWidget(increment)
        self.model.row_count_changed.connect(
            lambda count: self.counter.setText(f"{count:,} signals (virtual dataset)"))
        page.addLayout(bottom)
        self.refresh_instances()

    def refresh_instances(self):
        prior = self.instances.currentData()
        self.instances.blockSignals(True)
        self.instances.clear()
        for inst in self.demo.instances():
            self.instances.addItem(inst.name + " [" + inst.protocol + "]", inst.id)
        idx = self.instances.findData(prior)
        self.instances.setCurrentIndex(idx if idx >= 0 else 0)
        self.instances.blockSignals(False)
        self.instance_changed()

    def instance_changed(self):
        iid = self.instances.currentData()
        if iid:
            self.model.reload(instance_id=iid, search=self.search.text().strip())
            self.grid._apply_columns()

    def search_changed(self):
        self.model.reload(search=self.search.text().strip())

    def size_changed(self):
        size = self.sizes.currentData()
        if size is None:
            return
        self.hub.submit(lambda: self.demo.resize_demo(size),
                        lambda _: (self.model.reload(), self.notify(
                            f"Generated {size:,} virtual points per instance; cleared preview edits/mappings")),
                        write=True, error=self.notify)


class VirtualMappings(QAbstractTableModel):
    def __init__(self, demo, hub, parent=None):
        super().__init__(parent)
        self.demo, self.hub = demo, hub
        self.count = 0
        self.cache = {}
        self.loading = set()
        self.generation = 0
        self.refresh()
    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else self.count
    def columnCount(self, parent=QModelIndex()):
        return 4
    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if role != Qt.ItemDataRole.DisplayRole:
            return None
        if orientation == Qt.Orientation.Vertical:
            return str(section+1)
        return ("Mapping ID", "Source Instance / Signal", "Destination Instance / Signal",
                "Direction")[section]
    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or role != Qt.ItemDataRole.DisplayRole:
            return None
        page = index.row() // PAGE
        if page not in self.cache:
            self.load_page(page)
            return "…"
        rows = self.cache[page]
        pos = index.row() % PAGE
        if pos >= len(rows):
            return None
        edge = rows[pos]
        return (edge.id, edge.source_signal_id, edge.destination_signal_id,
                "Telemetry →")[index.column()]
    def refresh(self):
        self.generation += 1
        epoch = self.generation
        self.beginResetModel()
        self.count = 0
        self.cache.clear()
        self.loading.clear()
        self.endResetModel()
        def done(n):
            if epoch != self.generation:
                return
            self.beginResetModel()
            self.count = n
            self.endResetModel()
            if n:
                self.load_page(0)
        self.hub.submit(self.demo.mappings_count, done)
    def load_page(self, page):
        if page in self.loading or page in self.cache or len(self.loading) >= 3:
            return
        self.loading.add(page)
        epoch = self.generation
        def done(rows):
            if epoch != self.generation:
                return
            self.loading.discard(page)
            self.cache[page] = rows
            if len(self.cache) > 16:
                self.cache.pop(next(iter(self.cache)))
            start = PAGE*page
            if rows:
                self.dataChanged.emit(self.index(start, 0),
                                      self.index(min(self.count-1, start+len(rows)-1), 3))
        def failed(_):
            if epoch == self.generation:
                self.loading.discard(page)
        self.hub.submit(lambda: self.demo.mapping_page(page*PAGE, PAGE), done, error=failed)


class MappingPage(QWidget):
    def __init__(self, demo, hub, notify, parent=None):
        super().__init__(parent)
        self.demo, self.hub, self.notify = demo, hub, notify
        page = QVBoxLayout(self)
        page.setContentsMargins(14, 11, 14, 10)
        page.setSpacing(8)
        page.addWidget(heading("Protocol Mapping",
            "Bind existing signal identities across protocol instances. No address fields are stored in mappings."))
        row = QHBoxLayout()
        self.source = QComboBox()
        self.dest = QComboBox()
        row.addWidget(QLabel("From"))
        row.addWidget(self.source, 2)
        row.addWidget(QLabel("To"))
        row.addWidget(self.dest, 2)
        page.addLayout(row)
        controls = QHBoxLayout()
        self.start = QSpinBox()
        self.start.setRange(1, 500_000)
        self.start.setValue(1)
        self.length = QSpinBox()
        self.length.setRange(1, 10_000)
        self.length.setValue(10)
        controls.addWidget(QLabel("Start signal #"))
        controls.addWidget(self.start)
        controls.addWidget(QLabel("Count"))
        controls.addWidget(self.length)
        preview = QPushButton("Preview")
        preview.clicked.connect(self.preview)
        controls.addWidget(preview)
        commit = QPushButton("Auto-assign")
        commit.clicked.connect(self.assign)
        controls.addWidget(commit)
        controls.addStretch()
        page.addLayout(controls)
        self.hint = QLabel("Select instances and Preview to inspect identity-based assignments.")
        self.hint.setObjectName("Muted")
        page.addWidget(self.hint)
        self.model = VirtualMappings(demo, hub, self)
        self.grid = QTableView()
        self.grid.setModel(self.model)
        self.grid.setAlternatingRowColors(True)
        self.grid.setEditTriggers(QTableView.EditTrigger.NoEditTriggers)
        self.grid.verticalHeader().setDefaultSectionSize(23)
        self.grid.horizontalHeader().setStretchLastSection(True)
        self.grid.setColumnWidth(0, 260)
        self.grid.setColumnWidth(1, 300)
        self.grid.setColumnWidth(2, 300)
        page.addWidget(self.grid, 1)
        self.refresh_instances()

    def refresh_instances(self):
        src = self.source.currentData()
        dst = self.dest.currentData()
        for picker in (self.source, self.dest):
            picker.clear()
            for i in self.demo.instances():
                picker.addItem(i.name, i.id)
        src_index = self.source.findData(src)
        dst_index = self.dest.findData(dst)
        self.source.setCurrentIndex(src_index if src_index >= 0 else 0)
        self.dest.setCurrentIndex(dst_index if dst_index >= 0 else min(1, self.dest.count()-1))

    def preview(self):
        src, dst = self.source.currentData(), self.dest.currentData()
        start, length = self.start.value()-1, self.length.value()
        if not src or not dst or src == dst:
            self.notify("Select two different protocol instances")
            return False
        if start + length > self.demo.count:
            self.notify("Selected range is outside virtual signal catalog")
            return False
        source_id = self.demo.signal_id(src, start)
        target_id = self.demo.signal_id(dst, start)
        self.hint.setText(f"{length:,} telemetry binding(s): {source_id} → {target_id}. "
                          "Destination addresses remain defined in Protocol Signals.")
        return True

    def assign(self):
        if not self.preview():
            return
        src, dst, start, length = (self.source.currentData(), self.dest.currentData(),
                                   self.start.value()-1, self.length.value())
        if length > 24:
            confirmation = QMessageBox.question(self, "Confirm bulk assignment",
                f"Create up to {length:,} session-only mapping edges?\n"
                "These changes do NOT reach any field device or runtime.")
            if confirmation != QMessageBox.StandardButton.Yes:
                return
        def done(n):
            self.model.refresh()
            self.notify(f"Created {n:,} cross-instance mapping(s) — session-only demo")
        self.hub.submit(lambda: self.demo.map_range(src, dst, start, length), done,
                        write=True, error=self.notify)


class MonitoringPage(QWidget):
    def __init__(self, demo, notify, parent=None):
        super().__init__(parent)
        self.demo, self.notify = demo, notify
        page = QVBoxLayout(self)
        page.setContentsMargins(18, 14, 18, 12)
        page.addWidget(heading("Protocol Monitoring",
            "Connection, RX/TX, quality and timestamps — read-only view from a runtime provider."))
        self.status = QLabel("No Gateway Runtime IPC connected — ALL data unavailable")
        self.status.setObjectName("Notice")
        page.addWidget(self.status)
        self.table = QTableWidget()
        self.table.setColumnCount(6)
        self.table.setHorizontalHeaderLabels(
            ["Instance", "Connection", "Last Value", "Quality", "RX / TX", "Timestamp"])
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.verticalHeader().setDefaultSectionSize(27)
        page.addWidget(self.table)
        bar = QHBoxLayout()
        copy = QPushButton("Copy Diagnostics")
        copy.clicked.connect(self.copy_diagnostics)
        bar.addWidget(copy)
        bar.addStretch()
        page.addLayout(bar)
        self.refresh()

    def refresh(self):
        self.table.setRowCount(len(self.demo.instances()))
        for row, inst in enumerate(self.demo.instances()):
            values = [inst.name, "Not connected", "—", "Unavailable", "— / —", "—"]
            for col, value in enumerate(values):
                self.table.setItem(row, col, QTableWidgetItem(value))
    def copy_diagnostics(self):
        QApplication.clipboard().setText(self.demo.diagnostics())
        self.notify("Copied OFFLINE diagnostic summary; no live packet data")


class SimulationPage(QWidget):
    def __init__(self, demo, notify, parent=None):
        super().__init__(parent)
        self.demo, self.notify = demo, notify
        page = QVBoxLayout(self)
        page.setContentsMargins(18, 14, 18, 12)
        page.addWidget(heading("Value Simulation",
            "UI preview only until authenticated Runtime / Simulation API (#8/#9) exists. "
            "Values are NOT forwarded to IEC-104 or Modbus."))
        box = QFrame()
        box.setObjectName("PreviewPanel")
        form = QFormLayout(box)
        self.source = QComboBox()
        self.source.addItem("Meter 01 / Voltage 000001", "meter01:signal:0000001")
        self.voltage = QDoubleSpinBox()
        self.voltage.setRange(-1_000_000, 1_000_000)
        self.voltage.setSuffix(" V")
        self.voltage.setDecimals(2)
        self.voltage.setValue(229.80)
        form.addRow("Virtual source signal", self.source)
        form.addRow("Value preview", self.voltage)
        self.preview_status = QLabel("No simulation session. No physical output permitted.")
        form.addRow(self.preview_status)
        preview = QPushButton("Preview value locally")
        preview.clicked.connect(self.preview_value)
        form.addRow(preview)
        start = QPushButton("Start runtime simulation (unavailable)")
        start.setEnabled(False)
        form.addRow(start)
        page.addWidget(box)
        page.addStretch()
        self.label = QLabel("Physical controls are blocked by default; a future runtime must own simulation origin, TTL, quality, and audit.")
        self.label.setObjectName("Muted")
        self.label.setWordWrap(True)
        page.addWidget(self.label)

    def preview_value(self):
        self.preview_status.setText(
            f"Local preview: {self.voltage.value():.2f} V | origin: preview-only | "
            "delivered: NO | actual quality: unavailable")
        self.notify("Simulation preview ONLY — no gateway runtime connected")


class StudioWindow(QMainWindow):
    WORKSPACES = ("Gateway Config", "Protocol Config", "Protocol Signals",
                  "Protocol Mapping", "Protocol Monitoring", "Value Simulation")
    def __init__(self, demo=None, parent=None):
        super().__init__(parent)
        self.demo = demo or DemoProvider()
        self.hub = WorkerHub(self)
        self.setWindowTitle("ARPY Gateway Studio — Offline Engineering Preview")
        self.resize(1330, 810)
        self.setMinimumSize(960, 600)
        self._build()
        self._theme()

    def _build(self):
        splitter = QSplitter(Qt.Orientation.Horizontal)
        nav = QWidget()
        nav.setObjectName("Navigation")
        left = QVBoxLayout(nav)
        left.setContentsMargins(12, 14, 12, 10)
        brand = QLabel("ARPY  /  GATEWAY")
        brand.setObjectName("Brand")
        left.addWidget(brand)
        state = QLabel("OFFLINE ENGINEERING")
        state.setObjectName("Muted")
        left.addWidget(state)
        left.addSpacing(14)
        self.navigation = QListWidget()
        self.navigation.addItems(self.WORKSPACES)
        self.navigation.setSpacing(2)
        left.addWidget(self.navigation, 1)
        left.addWidget(QLabel("Draft · Not deployed"))
        splitter.addWidget(nav)
        self.pages = QStackedWidget()
        self.gateway = GatewayPage(self.demo, self.hub, self.show_message)
        self.config = ProtocolConfigPage(self.demo, self.hub, self.show_message)
        self.signals = ProtocolSignalsPage(self.demo, self.hub, self.show_message)
        self.mapping = MappingPage(self.demo, self.hub, self.show_message)
        self.monitor = MonitoringPage(self.demo, self.show_message)
        self.simulation = SimulationPage(self.demo, self.show_message)
        for page in (self.gateway, self.config, self.signals, self.mapping,
                     self.monitor, self.simulation):
            self.pages.addWidget(page)
        splitter.addWidget(self.pages)
        splitter.setSizes([205, 1125])
        splitter.setChildrenCollapsible(False)
        self.setCentralWidget(splitter)
        self.navigation.currentRowChanged.connect(self.pages.setCurrentIndex)
        self.navigation.currentRowChanged.connect(self._sync_page)
        self.navigation.setCurrentRow(0)
        self.gateway.changed.connect(self._sync_instances)
        self.hub.error.connect(self.show_message)
        self.statusBar().showMessage("Offline demo · Session-only changes · No protocol sockets", 0)

    def _sync_instances(self):
        self.gateway.refresh()
        self.config.refresh()
        self.signals.refresh_instances()
        self.mapping.refresh_instances()
        self.monitor.refresh()

    def _sync_page(self, index):
        if index == 4:
            self.monitor.refresh()

    def show_message(self, text):
        self.statusBar().showMessage(str(text).splitlines()[0], 12000)

    def _theme(self):
        self.setFont(QFont("Segoe UI", 9))
        self.setStyleSheet("""
            QMainWindow, QWidget { background: #f8fafc; color: #29394b; }
            QWidget#Navigation { background: #edf2f7; border-right: 1px solid #dce3ec; }
            QLabel#Brand { color: #145879; font-size: 13px; font-weight: 600; }
            QLabel#PageTitle { font-size: 17px; font-weight: 500; color: #132e46; }
            QLabel#Muted { color: #768697; font-size: 11px; }
            QLabel#Notice { color: #a04e17; background: #fff3dd;
                            border: 1px solid #eed7ad; padding: 8px; }
            QListWidget { background: transparent; border: 0; outline: none; font-size: 12px; }
            QListWidget::item { padding: 9px 8px; border-radius: 5px; }
            QListWidget::item:selected { background: #d7e9f4; color: #164762; }
            QFrame#PreviewPanel { border: 1px solid #dce4eb; border-radius: 7px;
                                  background: white; padding: 10px; }
            QPushButton { background: #fff; border: 1px solid #cbd5e1; border-radius: 5px;
                          padding: 6px 11px; min-height: 20px; }
            QPushButton:hover { background: #e6f3fa; border-color: #86b4ca; }
            QPushButton:disabled { color: #9eabb9; background: #f1f4f7; }
            QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox {
                 border: 1px solid #cbd5e1; border-radius: 4px; min-height: 25px;
                 background: white; padding: 1px 5px; }
            QTableView, QTableWidget { background: white; alternate-background-color: #f6f9fc;
                    border: 1px solid #dce4eb; outline: none; }
            QHeaderView::section { background: #eef3f7; color: #48586b;
                  border: none; border-right: 1px solid #e2e8f0;
                  border-bottom: 1px solid #d6dde8; padding: 5px 5px; }
            QStatusBar { background: #edf2f7; color: #506477; }
        """)

    def closeEvent(self, event):
        # GUI resources only. Runtime, once present, is a separate service.
        self.hub.shutdown()
        super().closeEvent(event)
