"""ARPY Gateway Studio v2: protocol-context explorer and task-specific workspaces.

GUI is deliberately a *separate process* and uses GUI-only mock projections.
No runtime sockets, no Modbus frames, no field controls, no deployed revisions.
RFC #13 / STORE #3 / IPC #8 / SIM #9 provide production implementations.
"""
from __future__ import annotations

from PySide6.QtCore import Qt, QEvent, QModelIndex, QAbstractTableModel, Signal, QTimer
from PySide6.QtWidgets import (
    QApplication, QAbstractItemView, QCheckBox, QComboBox, QDialog, QDialogButtonBox,
    QDoubleSpinBox, QFormLayout, QFrame, QHBoxLayout, QInputDialog, QLabel,
    QLineEdit, QTabBar, QMainWindow, QMessageBox, QPushButton, QSpinBox,
    QSplitter, QStackedWidget, QTableView, QTreeView, QVBoxLayout, QWidget,
    QHeaderView
)

from .demo import DemoProvider
from .project_view import EngineeringView, SelectionContext
from .design_tokens import apply_theme, ROW_HEIGHT, SIDEBAR_WIDTH, EXPLORER_WIDTH
from .explorer import ProtocolExplorer
from .grid import VirtualSignals, SignalGrid, PAGE
from .telemetry_view import TelemetrySource, ReadOnlySignalModel
from .runtime_view import DisconnectedRuntime
from .live_ipc import LiveRuntimePanel
from .persisted_devices import SavedDevicesPanel
from .workers import WorkerHub
from .window import GatewayPage, VirtualMappings
from .catalog_drawer import SignalCatalogDrawer


def title(text, subtitle):
    content = QWidget()
    lay = QVBoxLayout(content)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(2)
    h = QLabel(text)
    h.setObjectName("PageTitle")
    lay.addWidget(h)
    caption = QLabel(subtitle)
    caption.setObjectName("Muted")
    caption.setWordWrap(True)
    lay.addWidget(caption)
    return content


def page_layout(page):
    lay = QVBoxLayout(page)
    lay.setContentsMargins(14, 12, 14, 10)
    lay.setSpacing(9)
    return lay


def action(label, callback, primary=False):
    button = QPushButton(label)
    if primary:
        button.setObjectName("Primary")
    button.clicked.connect(callback)
    return button


class ConnectionsModel(QAbstractTableModel):
    """Small peer config table; identity-checked asynchronous edits."""

    columns = ("Name", "Host / IP", "Port", "Unit ID", "Poll ms", "Enabled")

    def __init__(self, project, hub, notify, parent=None):
        super().__init__(parent)
        self.project, self.hub, self.notify = project, hub, notify
        self.instance_id = ""
        self.peer_id = ""
        self.rows = ()

    def select(self, instance_id, peer_id=""):
        self.beginResetModel()
        self.instance_id, self.peer_id = instance_id, peer_id
        rows = self.project.points(instance_id) if instance_id else ()
        self.rows = tuple(p for p in rows if not peer_id or p.id == peer_id)
        self.endResetModel()

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.rows)

    def columnCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.columns)

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if role != Qt.ItemDataRole.DisplayRole:
            return None
        return self.columns[section] if orientation == Qt.Orientation.Horizontal else str(section+1)

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        p = self.rows[index.row()]
        if role == Qt.ItemDataRole.ToolTipRole:
            return p.id
        if role not in (Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.EditRole):
            return None
        settings = p.params()
        values = (p.name, settings.get("host", settings.get("ip", "—")),
                  settings.get("port", "—"), settings.get("unit_id", "—"),
                  settings.get("poll_ms", "—"), settings.get("enabled", "—"))
        return str(values[index.column()])

    def flags(self, index):
        if not index.isValid():
            return Qt.ItemFlag.NoItemFlags
        base = Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsEnabled
        p = self.rows[index.row()]
        key = ("name", "host" if "host" in p.params() else "ip",
               "port", "unit_id", "poll_ms", "enabled")[index.column()]
        return base | (Qt.ItemFlag.ItemIsEditable if key == "name" or key in p.params()
                       else Qt.ItemFlag.NoItemFlags)

    def setData(self, index, value, role=Qt.ItemDataRole.EditRole):
        if role != Qt.ItemDataRole.EditRole or not index.isValid():
            return False
        p = self.rows[index.row()]
        keys = ("name", "host" if "host" in p.params() else "ip",
                "port", "unit_id", "poll_ms", "enabled")
        key = keys[index.column()]
        if key != "name" and key not in p.params():
            return False
        iid, pid = p.instance_id, p.id
        self.hub.submit(
            lambda: self.project.update_point(iid, pid, key, value),
            lambda _: (self.select(self.instance_id, self.peer_id),
                       self.notify(f"Connection {p.name} saved in offline draft")),
            write=True, error=self.notify
        )
        return True


class StationsModel(QAbstractTableModel):
    def __init__(self, project, parent=None):
        super().__init__(parent)
        self.project = project
        self.rows = ()
    def select(self, iid, sid=""):
        self.beginResetModel()
        self.rows = tuple(x for x in self.project.stations(iid) if not sid or x.id == sid)
        self.endResetModel()
    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.rows)
    def columnCount(self, parent=QModelIndex()):
        return 3
    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if role != Qt.ItemDataRole.DisplayRole: return None
        return ("Station", "Common Address / Unit ID", "Stable ID")[section] if orientation == Qt.Orientation.Horizontal else str(section+1)
    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or role != Qt.ItemDataRole.DisplayRole: return None
        station = self.rows[index.row()]
        return (station.name, str(station.address), station.id)[index.column()]


class ProtocolConfigWorkspace(QWidget):
    def __init__(self, project, hub, notify, topology_changed, parent=None,
                 engineering_db_path=None):
        super().__init__(parent)
        self.project, self.hub, self.notify = project, hub, notify
        self.topology_changed = topology_changed
        self.current = None
        lay = page_layout(self)
        lay.addWidget(title("Protocol Config",
            "Choose a protocol child: global settings, IED/slave connections, ASDU stations or master peers."))
        self.breadcrumb = QLabel("Select a protocol from the explorer")
        self.breadcrumb.setObjectName("SectionTitle")
        lay.addWidget(self.breadcrumb)
        # No second "Saved Modbus" page/button: IED Connections is the
        # database-backed editor, not an independent demo configuration.
        self.stack = QStackedWidget()
        lay.addWidget(self.stack, 1)

        self.overview = QLabel("Select a protocol or child in the explorer.\n"
            "A communication peer and IEC-104 ASDU data station are different engineering objects.")
        self.overview.setObjectName("Muted")
        self.overview.setWordWrap(True)
        self.stack.addWidget(self.overview)

        general = QWidget()
        form_lay = QVBoxLayout(general)
        self.form_panel = QWidget()
        self.form = QFormLayout(self.form_panel)
        self.form.setContentsMargins(0, 0, 0, 0)
        form_lay.addWidget(self.form_panel, 0, Qt.AlignmentFlag.AlignTop)
        form_lay.addStretch()
        self.stack.addWidget(general)

        peers = QWidget()
        pv = QVBoxLayout(peers)
        bar = QHBoxLayout()
        self.peer_title = QLabel("Communication points")
        self.peer_title.setObjectName("SectionTitle")
        bar.addWidget(self.peer_title)
        bar.addStretch()
        bar.addWidget(action("+ Add Connection", self.add_peer))
        pv.addLayout(bar)
        self.peers_model = ConnectionsModel(project, hub, notify, self)
        self.peer_grid = QTableView()
        self.peer_grid.setModel(self.peers_model)
        self.peer_grid.setAlternatingRowColors(True)
        self.peer_grid.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.peer_grid.verticalHeader().setDefaultSectionSize(ROW_HEIGHT)
        self.peer_grid.horizontalHeader().setStretchLastSection(True)
        self.peer_grid.setColumnWidth(0, 175)
        self.peer_grid.setColumnWidth(1, 160)
        pv.addWidget(self.peer_grid)
        self.stack.addWidget(peers)

        stations = QWidget()
        sv = QVBoxLayout(stations)
        tools = QHBoxLayout()
        tools.addWidget(QLabel("IEC-104 ASDU stations represent local CA data, not remote SCADA master IPs."))
        tools.addStretch()
        tools.addWidget(action("+ Add Station", self.add_station))
        sv.addLayout(tools)
        self.stations_model = StationsModel(project, self)
        self.station_grid = QTableView()
        self.station_grid.setModel(self.stations_model)
        self.station_grid.verticalHeader().setDefaultSectionSize(ROW_HEIGHT)
        self.station_grid.horizontalHeader().setStretchLastSection(True)
        self.station_grid.setColumnWidth(0, 210)
        self.station_grid.setColumnWidth(1, 180)
        sv.addWidget(self.station_grid)
        self.stack.addWidget(stations)

        self.saved_devices = (SavedDevicesPanel(hub, engineering_db_path,
                                               notify, self)
                              if engineering_db_path is not None else None)
        if self.saved_devices is not None:
            self.stack.addWidget(self.saved_devices)
            self.saved_devices.devices_changed.connect(self._saved_peers_loaded)

    def _saved_peers_loaded(self, rows):
        # Rebuild the Explorer from the same Store snapshot as the table.
        # The Store remains the only persistent authority.
        self.project.install_saved_modbus_points(rows)
        if self.topology_changed is not None:
            self.topology_changed()

    def show_saved_devices(self):
        if self.saved_devices is None:
            return
        self.breadcrumb.setText("Saved Modbus TCP Devices / SQLite v2 Engineering Draft")
        self.stack.setCurrentWidget(self.saved_devices)
        self.saved_devices.activate()

    def select_node(self, node):
        self.current = node
        if node is None:
            self.stack.setCurrentIndex(0)
            return
        iid = node.instance_id
        self.breadcrumb.setText(node.title + "  /  " + self.project.demo.instance(iid).name)
        if node.kind == "general":
            self._build_form(iid)
            self.stack.setCurrentIndex(1)
        elif node.kind in ("peers", "peer"):
            if iid == "meter01" and self.saved_devices is not None:
                self.stack.setCurrentWidget(self.saved_devices)
                self.saved_devices.activate()
                if node.kind == "peer":
                    self.saved_devices.select_device(node.scope_id)
            else:
                self.peers_model.select(iid, node.scope_id if node.kind == "peer" else "")
                self.peer_title.setText("Master Connections" if self.project.demo.instance(iid).protocol == "iec104"
                                        else "IED / Slave Connections")
                self.stack.setCurrentIndex(2)
        elif node.kind in ("stations", "station"):
            self.stations_model.select(iid, node.scope_id if node.kind == "station" else "")
            self.stack.setCurrentIndex(3)
        else:
            num_peers = len(self.project.points(iid))
            num_stations = len(self.project.stations(iid))
            self.overview.setText(
                f"{self.project.demo.instance(iid).name}\n\n"
                f"{num_peers} communication point(s)   •   {num_stations} local station(s)\n\n"
                "Select General Settings or a connection/station child to edit parameters.")
            self.stack.setCurrentIndex(0)

    def _build_form(self, iid):
        while self.form.rowCount():
            self.form.removeRow(0)
        instance = self.project.demo.instance(iid)
        for key, value in instance.settings.items():
            field = QLineEdit(str(value))
            field.setMaximumWidth(320)
            field.editingFinished.connect(
                lambda key=key, field=field, iid=iid: self.save_setting(iid, key, field.text()))
            self.form.addRow(key.replace("_", " ").title(), field)
        note = QLabel("Global instance/bus settings only · saving draft never restarts runtime")
        note.setObjectName("FieldHint")
        self.form.addRow(note)

    def save_setting(self, iid, key, value):
        self.hub.submit(lambda: self.project.demo.set_setting(iid, key, value),
                        lambda _: self.notify(f"{key} saved to offline draft"),
                        write=True, error=self.notify)

    def add_peer(self):
        if not self.current:
            return
        iid = self.current.instance_id
        label = "SCADA Master" if self.project.demo.instance(iid).protocol == "iec104" else "IED"
        name = f"New {label} {len(self.project.points(iid))+1}"
        if iid == "meter01" and self.saved_devices is not None:
            self.saved_devices.activate()
            self.saved_devices.add_device()
            return
        self.hub.submit(lambda: self.project.add_point(iid, name, "127.0.0.1"),
                        lambda _: (self.peers_model.select(iid),
                                   self.topology_changed(self.current.key),
                                   self.notify("New communication point created — edit parameters inline")),
                        write=True, error=self.notify)

    def add_station(self):
        if not self.current:
            return
        iid = self.current.instance_id
        ca, accepted = QInputDialog.getInt(self, "Add ASDU Station",
                        "Local Common Address", 2, 1, 65534)
        if not accepted: return
        self.hub.submit(lambda: self.project.add_station(iid, ca),
                        lambda _: (self.stations_model.select(iid),
                                   self.topology_changed(self.current.key),
                                   self.notify(f"Local ASDU station CA={ca} created")),
                        write=True, error=self.notify)


class SignalWorkspace(QWidget):
    catalog_requested = Signal(str, str)
    def __init__(self, project, hub, notify, assignments_changed, parent=None):
        super().__init__(parent)
        self.project, self.hub, self.notify = project, hub, notify
        self.assignments_changed = assignments_changed
        self.instance_id, self.scope_id = "meter01", ""
        lay = page_layout(self)
        lay.addWidget(title("Protocol Signals", "Engineering signal catalog · connection/station bound · protocol-native columns"))
        bar = QHBoxLayout()
        self.context_label = QLabel("All signals")
        self.context_label.setObjectName("SectionTitle")
        bar.addWidget(self.context_label)
        bar.addStretch()
        self.search = QLineEdit()
        self.search.setMaximumWidth(190)
        self.search.setPlaceholderText("Find ID: #1234")
        self.search.returnPressed.connect(self.search_changed)
        bar.addWidget(self.search)
        bar.addWidget(action("Find", self.search_changed))
        self.add_button = action("+ Add Signal", self.add_signal)
        bar.addWidget(self.add_button)
        self.assign_button = action("Assign from Catalog", self.assign_catalog, True)
        bar.addWidget(self.assign_button)
        lay.addLayout(bar)
        self.model = VirtualSignals(project.source(self.instance_id), hub, self.instance_id, self)
        self.grid = SignalGrid(self.model)
        self.model.failure.connect(notify)
        self.model.info.connect(notify)
        self.grid.failure.connect(notify)
        self.grid.info.connect(notify)
        lay.addWidget(self.grid, 1)
        foot = QHBoxLayout()
        self.counter = QLabel("Loading virtual signal catalog...")
        self.counter.setObjectName("Muted")
        foot.addWidget(self.counter)
        foot.addStretch()
        foot.addWidget(action("Auto-number selected", self.grid.auto_number))
        lay.addLayout(foot)
        self.model.row_count_changed.connect(
            lambda n: self.counter.setText(f"{n:,} scoped signal(s) · session-only draft"))

    def select_node(self, node):
        if not node:
            return
        iid = node.instance_id
        scope = node.scope_id if node.kind in ("scope", "station", "peer") else ""
        if node.kind == "peer" and self.project.demo.instance(iid).protocol == "iec104":
            scope = ""  # IEC104 remote master is not signal owner
        self.instance_id, self.scope_id = iid, scope
        self.context_label.setText(self.project.demo.instance(iid).name +
                                   ("  /  " + node.title if scope else "  /  All Signals"))
        self.model.demo = self.project.source(iid, scope)
        self.model.reload(instance_id=iid, search=self.search.text().strip())
        self.grid._apply_columns()
        can_assign = bool(self.project.stations(iid)) and (
            self.project.demo.instance(iid).protocol == "iec104" and
            self.project.demo.instance(iid).role.value == "server")
        self.assign_button.setEnabled(can_assign)
        self.assign_button.setVisible(can_assign)  # no irrelevant actions on Modbus editors

    def search_changed(self):
        self.model.reload(search=self.search.text().strip())

    def add_signal(self):
        iid, scope = self.instance_id, self.scope_id
        def done(sid):
            self.search.setText("#" + str(int(sid.rsplit(":", 1)[-1])))
            self.model.reload(search=self.search.text())
            self.notify("Created stable-ID signal in selected protocol catalog (offline draft)")
        self.hub.submit(lambda: self.project.append_signal(iid, scope, "New Signal"),
                        done, write=True, error=self.notify)

    def assign_catalog(self):
        instance = self.instance_id
        stations = self.project.stations(instance)
        if not stations:
            self.notify("Configure an IEC-104 ASDU station before assignment")
            return
        station_id = (self.scope_id if self.scope_id in {s.id for s in stations}
                      else stations[0].id)
        self.catalog_requested.emit(instance, station_id)

    def focus(self, signal_id):
        index = int(signal_id.rsplit(":", 1)[-1])
        self.search.setText("#" + str(index))
        self.model.reload(search=self.search.text())
        self.model.row_count_changed.connect(self._focus_first)

    def _focus_first(self, count):
        try: self.model.row_count_changed.disconnect(self._focus_first)
        except (TypeError, RuntimeError): pass
        if count:
            self.grid.setCurrentIndex(self.model.index(0, 0))
            self.grid.scrollTo(self.model.index(0, 0))


class MappingWorkspace(QWidget):
    navigate_signal = Signal(str, str)
    def __init__(self, project, hub, notify, parent=None):
        super().__init__(parent)
        self.project, self.notify = project, notify
        lay = page_layout(self)
        lay.addWidget(title("Protocol Mapping",
            "Directed signal assignments · double-click source/destination to open exact signal parameter"))
        tip = QLabel("Mapping uses stable signal IDs. IEC-104 destination IOA/TI is edited in Protocol Signals.")
        tip.setObjectName("Muted")
        lay.addWidget(tip)
        self.model = VirtualMappings(project.demo, hub, self)
        self.grid = QTableView()
        self.grid.setModel(self.model)
        self.grid.setAlternatingRowColors(True)
        self.grid.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.grid.verticalHeader().setDefaultSectionSize(ROW_HEIGHT)
        self.grid.horizontalHeader().setStretchLastSection(True)
        self.grid.setColumnWidth(0, 280)
        self.grid.setColumnWidth(1, 300)
        self.grid.setColumnWidth(2, 300)
        self.grid.doubleClicked.connect(self.open_mapping_signal)
        lay.addWidget(self.grid, 1)

    def open_mapping_signal(self, index):
        page = index.row() // PAGE
        values = self.model.cache.get(page)
        if not values or index.row() % PAGE >= len(values):
            return
        route = values[index.row() % PAGE]
        if index.column() == 2:
            self.navigate_signal.emit(route.destination_instance_id, route.destination_signal_id)
        else:
            self.navigate_signal.emit(route.source_instance_id, route.source_signal_id)


class LiveWorkspace(QWidget):
    def __init__(self, project, hub, notify, runtime, simulation=False,
                 parent=None, runtime_endpoint=None):
        super().__init__(parent)
        self.project, self.hub, self.notify, self.runtime = project, hub, notify, runtime
        self.simulation = simulation
        self.preview_values = {}
        self.instance_id, self.scope_id = "meter01", ""
        lay = page_layout(self)
        label = "Value Simulation" if simulation else "Protocol Monitoring"
        subtitle = ("All selected protocol signals, Real vs Simulated values; output is disabled until Runtime #9."
                    if simulation else
                    "Protocol-scoped live values, quality, timestamp, COT and diagnostics from Runtime IPC.")
        lay.addWidget(title(label, subtitle))
        toolbar = QHBoxLayout()
        self.context_label = QLabel("Select protocol from explorer")
        self.context_label.setObjectName("SectionTitle")
        toolbar.addWidget(self.context_label)
        toolbar.addStretch()
        self.status = QLabel("RUNTIME DISCONNECTED")
        self.status.setObjectName("StatusWarning")
        toolbar.addWidget(self.status)
        if not simulation:
            toolbar.addWidget(action("Copy Diagnostics", self.copy_diagnostics))
        lay.addLayout(toolbar)
        # Exactly one data model per mode: do not construct a hidden 500k-row
        # offline TelemetrySource when actual runtime IPC monitoring is active.
        self.live_panel = (LiveRuntimePanel(hub, runtime_endpoint, self)
                           if runtime_endpoint is not None and not simulation else None)
        self.provider = None
        self.model = None
        self.grid = None
        if self.live_panel is not None:
            lay.addWidget(self.live_panel, 1)
            self.status.setText("TEST IPC · READ ONLY")
        else:
            self.provider = TelemetrySource(project.source(self.instance_id),
                                            simulation=simulation)
            self.model = ReadOnlySignalModel(self.provider, hub, self.instance_id, self)
            self.grid = QTableView()
            self.grid.setModel(self.model)
            self.grid.setAlternatingRowColors(True)
            self.grid.verticalHeader().setDefaultSectionSize(ROW_HEIGHT)
            self.grid.horizontalHeader().setStretchLastSection(True)
            self.grid.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
            lay.addWidget(self.grid, 1)
            self.model.failure.connect(notify)
        if simulation:
            b = QHBoxLayout()
            b.addWidget(QLabel("Selected signal · proposed value"))
            self.proposed = QLineEdit()
            self.proposed.setMaximumWidth(180)
            self.proposed.setPlaceholderText("e.g. 229.8")
            b.addWidget(self.proposed)
            b.addWidget(action("Preview Locally", self.preview))
            self.toggle = QCheckBox("Real  ↔  Simulated")
            self.toggle.setEnabled(False)
            self.toggle.setToolTip("Authenticated Gateway Runtime Simulation API not connected")
            b.addWidget(self.toggle)
            b.addWidget(action("Stop All (unavailable)", self.stop_all))
            b.addStretch()
            lay.addLayout(b)
            self.caption = QLabel("No values transmitted. Real source remains unaffected; field controls blocked.")
        else:
            self.caption = QLabel(
                "Test IPC client · live values only from the connected headless runtime. "
                "No configuration deployment or field controls."
                if self.live_panel is not None else
                "No runtime connection · unavailable does NOT mean value zero or quality Good.")
        self.caption.setObjectName("Muted")
        lay.addWidget(self.caption)

    def select_node(self, node):
        if node is None: return
        iid = node.instance_id
        scope = node.scope_id if node.kind == "scope" else ""
        self.instance_id, self.scope_id = iid, scope
        if self.live_panel is not None:
            self.context_label.setText(
                "Runtime Global · offline explorer selection is NOT a signal filter")
            return
        self.context_label.setText(self.project.demo.instance(iid).name +
                                   ("  /  "+node.title if scope else "  /  All Signals"))
        self.provider = TelemetrySource(self.project.source(iid, scope), self.simulation)
        if self.simulation:
            self.provider.preview_values = self.preview_values
        self.model.demo = self.provider
        self.model.reload(instance_id=iid)
        for c, f in enumerate(self.model.fields):
            self.grid.setColumnWidth(c, f.width)

    def preview(self):
        index = self.grid.currentIndex()
        if not index.isValid():
            self.notify("Select a signal row first")
            return
        number = self.provider.signals.index_for_view(
            self.instance_id, index.row(), self.model.search, self.model.descending, self.model.sort_key)
        sid = self.provider.signal_id(self.instance_id, number)
        self.preview_values[sid] = self.proposed.text()
        self.model.reload()
        self.caption.setText(f"{sid}: local preview only · NOT transmitted · physical source unchanged")
        self.notify("Preview stored locally; simulated source is NOT active")

    def stop_all(self):
        self.preview_values.clear()
        self.model.reload()
        self.notify("Cleared local previews; runtime simulation was never active")

    def copy_diagnostics(self):
        if self.live_panel is not None:
            text = ("ARPY runtime monitoring · TEST IPC only\n"
                    f"State: {self.live_panel.current_state}\n"
                    f"Visible signals: {len(self.live_panel.values.rows)}\n"
                    "No tokens, device passwords, or raw IPC envelopes exported.")
            QApplication.clipboard().setText(text)
            self.notify("Copied redacted runtime viewer state")
            return
        QApplication.clipboard().setText(self.runtime.diagnostics())
        self.notify("Copied redacted offline diagnostic summary")


class WorkspaceTabs(QTabBar):
    """Compact native Qt tabs preserve old navigation test APIs."""
    def currentRow(self):
        return self.currentIndex()

    def setCurrentRow(self, row):
        self.setCurrentIndex(row)


class StudioV2Window(QMainWindow):
    WORKSPACES = ("Gateway Config", "Protocol Config", "Protocol Signals",
                  "Protocol Mapping", "Protocol Monitoring", "Value Simulation")
    def __init__(self, demo=None, parent=None, *, runtime_endpoint=None,
                 engineering_db_path=None):
        super().__init__(parent)
        self.project = EngineeringView(demo or DemoProvider())
        if engineering_db_path is not None:
            # Do not display seeded demo IEDs during asynchronous DB startup.
            self.project.install_saved_modbus_points(())
        self.hub = WorkerHub(self)
        self.runtime = DisconnectedRuntime(self.project.demo)
        self.runtime_endpoint = runtime_endpoint  # developer-only test endpoint
        self.engineering_db_path = engineering_db_path  # persistent local draft, not Deploy
        self.context = SelectionContext()
        self.setWindowTitle("ARPY Gateway Studio · Protocol Engineering Preview")
        self.resize(1450, 850)
        self.setMinimumSize(1040, 620)
        self._build()
        apply_theme(QApplication.instance(), "light")
        if self.config.saved_devices is not None:
            # Populate the Explorer on launch; never wait for a special button.
            QTimer.singleShot(0, self.config.saved_devices.activate)

    def _build(self):
        # Horizontal navigation replaces the wasteful fixed-width left sidebar.
        central = QWidget(self)
        frame = QVBoxLayout(central)
        frame.setContentsMargins(0, 0, 0, 0)
        frame.setSpacing(0)
        header = QFrame()
        header.setObjectName("AppHeader")
        top = QHBoxLayout(header)
        top.setContentsMargins(12, 6, 12, 6)
        top.setSpacing(9)
        brand = QLabel("ARPY")
        brand.setObjectName("Brand")
        brand.setToolTip("ARPY Gateway Engineering Studio")
        top.addWidget(brand)
        self.navigation = WorkspaceTabs()
        self.navigation.setObjectName("WorkspaceTabs")
        self.navigation.setDrawBase(False)
        self.navigation.setExpanding(False)
        for label in self.WORKSPACES:
            self.navigation.addTab(label)
        top.addWidget(self.navigation, 1)
        self.back_button = action("← Back", self.go_back)
        self.back_button.setEnabled(False)
        top.addWidget(self.back_button)
        self.theme = QComboBox()
        self.theme.addItem("Light", "light")
        self.theme.addItem("Dark", "dark")
        self.theme.setMaximumWidth(86)
        self.theme.currentIndexChanged.connect(
            lambda _: apply_theme(QApplication.instance(), self.theme.currentData()))
        top.addWidget(self.theme)
        draft = QLabel("OFFLINE DRAFT")
        draft.setObjectName("StatusWarning")
        top.addWidget(draft)
        frame.addWidget(header)

        horizontal = QWidget()
        line = QHBoxLayout(horizontal)
        line.setContentsMargins(0, 0, 0, 0)
        line.setSpacing(0)
        splitter = QSplitter(Qt.Orientation.Horizontal)
        explorer_pane = QWidget()
        explorer_pane.setObjectName("ExplorerPanel")
        explorer = QVBoxLayout(explorer_pane)
        explorer.setContentsMargins(8, 10, 8, 7)
        explorer.setSpacing(6)
        explorer.addWidget(QLabel("PROTOCOL EXPLORER"))
        self.disabled_toggle = QCheckBox("Show disabled")
        self.disabled_toggle.toggled.connect(self.refresh_explorer)
        explorer.addWidget(self.disabled_toggle)
        self.tree_model = ProtocolExplorer(self.project)
        self.tree = QTreeView()
        self.tree.setModel(self.tree_model)
        self.tree.setHeaderHidden(True)
        self.tree.setUniformRowHeights(True)
        self.tree.setExpandsOnDoubleClick(True)
        self.tree.selectionModel().currentChanged.connect(self._selection_changed)
        explorer.addWidget(self.tree, 1)
        splitter.addWidget(explorer_pane)

        self.pages = QStackedWidget()
        self.gateway = GatewayPage(self.project.demo, self.hub, self.notify)
        self.config = ProtocolConfigWorkspace(
            self.project, self.hub, self.notify, self.refresh_explorer,
            engineering_db_path=self.engineering_db_path)
        self.signals = SignalWorkspace(self.project, self.hub, self.notify,
                                       self.on_mappings_changed)
        self.mapping = MappingWorkspace(self.project, self.hub, self.notify)
        self.monitor = LiveWorkspace(self.project, self.hub, self.notify, self.runtime,
                                     runtime_endpoint=self.runtime_endpoint)
        self.simulation = LiveWorkspace(self.project, self.hub, self.notify,
                                        self.runtime, simulation=True)
        for widget in (self.gateway, self.config, self.signals, self.mapping,
                       self.monitor, self.simulation):
            self.pages.addWidget(widget)
        splitter.addWidget(self.pages)
        splitter.setChildrenCollapsible(False)
        splitter.setSizes([232, 1190])
        line.addWidget(splitter, 1)
        # Non-modal overlay; never resize/reflow the large IEC104 signal grid.
        self.workspace_surface = horizontal
        self.catalog_drawer = SignalCatalogDrawer(self.project, self.hub,
                                                  self.notify, horizontal)
        frame.addWidget(horizontal, 1)
        self.setCentralWidget(central)
        horizontal.installEventFilter(self)
        self.catalog_drawer.sync_parent_geometry()

        self.navigation.currentChanged.connect(self._workspace_changed)
        self.gateway.changed.connect(self.refresh_explorer)
        self.mapping.navigate_signal.connect(self.open_signal)
        self.signals.catalog_requested.connect(self.open_catalog)
        self.catalog_drawer.close_requested.connect(self.close_catalog)
        self.catalog_drawer.assigned.connect(self._catalog_assigned)
        self.hub.error.connect(self.notify)
        self._workspace_changed(0)
        self.statusBar().showMessage(
            "Offline engineering demo  •  Draft only  •  No gateway runtime connected", 0)

    def open_catalog(self, destination_id: str, station_id: str):
        # The source browser is a NON-MODAL right panel. Engineers retain
        # visual context in Protocol Signals while selecting real source rows.
        try:
            self.catalog_drawer.set_destination(destination_id, station_id)
        except ValueError as exc:
            self.notify(str(exc))
            return
        width = min(600, max(470, int(self.width() * 0.42)))
        self.catalog_drawer.slide(True, width)

    def close_catalog(self):
        self.catalog_drawer.slide(False)

    def _catalog_assigned(self, created):
        self.signals.search.clear()
        self.signals.model.reload(search="")
        self.on_mappings_changed()
        self.close_catalog()

    def _workspace_changed(self, workspace):
        if workspace < 0:
            return
        self.context.workspace = workspace
        self.pages.setCurrentIndex(workspace)
        if self.monitor.live_panel is not None:
            if workspace == 4:
                self.monitor.live_panel.activate()
            else:
                self.monitor.live_panel.deactivate()
        if workspace != 2 and self.catalog_drawer.is_open:
            self.close_catalog()
        self.refresh_explorer()

    def refresh_explorer(self, key=None):
        workspace = self.navigation.currentRow()
        if workspace < 0:
            return
        previous = key if isinstance(key, str) and key else (
            self.tree.currentIndex().internalPointer().key if self.tree.currentIndex().isValid()
            else "")
        self.tree_model.rebuild(workspace, self.disabled_toggle.isChecked())
        # Expand only protocol roots: large connection lists remain collapsed and
        # are never instantiated as per-signal widgets or eagerly expanded.
        for row in range(self.tree_model.rowCount()):
            self.tree.expand(self.tree_model.index(row, 0))
        index = self.tree_model.find(previous) if previous else QModelIndex()
        if not index.isValid() and self.context.instance_id:
            index = self.tree_model.first_for(self.context.instance_id)
        if not index.isValid() and self.tree_model.rowCount() > 0:
            index = self.tree_model.index(0, 0)
        if index.isValid():
            ancestor = index.parent()
            while ancestor.isValid():
                self.tree.expand(ancestor)
                ancestor = ancestor.parent()
            self.tree.setCurrentIndex(index)
            self._set_node(index.internalPointer())

    def _selection_changed(self, current, old):
        if current.isValid():
            self._set_node(current.internalPointer())

    def _set_node(self, node):
        if node.kind in ("peers", "stations"):
            index = self.tree_model.find(node.key)
            if index.isValid():
                self.tree.expand(index)  # reveal IED/station children only on selection
        self.context.instance_id = node.instance_id
        self.context.scope_id = node.scope_id
        self.context.node_type = node.kind
        workspace = self.navigation.currentRow()
        if workspace == 1:
            self.config.select_node(node)
        elif workspace == 2:
            self.signals.select_node(node)
        elif workspace == 3:
            self.mapping.model.refresh()
        elif workspace == 4:
            self.monitor.select_node(node)
        elif workspace == 5:
            self.simulation.select_node(node)

    def on_mappings_changed(self):
        self.mapping.model.refresh()

    def open_signal(self, instance_id, signal_id):
        self.context.save()
        self.back_button.setEnabled(True)
        self.navigation.setCurrentRow(2)
        scope = self.project.owner_for_signal_id(instance_id, signal_id)
        index = self.tree_model.find(scope) if scope else self.tree_model.find("all:"+instance_id)
        if not index.isValid():
            index = self.tree_model.first_for(instance_id)
        if index.isValid():
            self.tree.setCurrentIndex(index)
            self._set_node(index.internalPointer())
        self.signals.focus(signal_id)
        self.notify(f"Opened {signal_id} · Back restores mapping selection")

    def go_back(self):
        if not self.context.restore():
            return
        workspace = self.context.workspace
        self.navigation.setCurrentRow(workspace)
        match = self.tree_model.find(self.context.scope_id) if self.context.scope_id else (
            self.tree_model.first_for(self.context.instance_id))
        if match.isValid():
            self.tree.setCurrentIndex(match)
        self.back_button.setEnabled(bool(self.context.back))

    def eventFilter(self, watched, event):
        if (watched is getattr(self, "workspace_surface", None)
                and event.type() == QEvent.Type.Resize):
            self.catalog_drawer.sync_parent_geometry()
        return super().eventFilter(watched, event)

    def notify(self, msg):
        self.statusBar().showMessage(str(msg).splitlines()[0], 12000)

    def closeEvent(self, event):
        if self.monitor.live_panel is not None:
            self.monitor.live_panel.shutdown()
        self.hub.shutdown()
        super().closeEvent(event)
