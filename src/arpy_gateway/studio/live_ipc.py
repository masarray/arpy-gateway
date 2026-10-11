"""Real read-only Gateway Runtime viewport for PySide6 Studio.

No Modbus, IEC-104 or runtime service imports. All network I/O happens in a
bounded WorkerHub thread, never in Qt's GUI event loop. Uses the versioned
IPC client's cursor/session to recover from gaps and runtime restarts.

Temporary test transport is opt-in only; it MUST NOT be mistaken for
production authenticated OS IPC or an active engineering deployment.
"""
from __future__ import annotations

import asyncio

from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt, QTimer
from PySide6.QtWidgets import (
    QAbstractItemView, QHBoxLayout, QLabel, QPushButton, QTableView,
    QVBoxLayout, QWidget,
)


class LiveValueModel(QAbstractTableModel):
    COLUMNS = ("Signal ID", "Value", "Quality", "Observed UTC", "Origin", "Sequence")

    def __init__(self, parent=None):
        super().__init__(parent)
        self.rows: tuple[dict, ...] = ()

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.rows)

    def columnCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.COLUMNS)

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if role != Qt.ItemDataRole.DisplayRole:
            return None
        return (self.COLUMNS[section] if orientation == Qt.Orientation.Horizontal
                else str(section + 1))

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or role not in (
                Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.ToolTipRole):
            return None
        row = self.rows[index.row()]
        if role == Qt.ItemDataRole.ToolTipRole:
            return row["signal_id"]
        keys = ("signal_id", "value", "quality", "observed_at", "origin", "sequence")
        value = row.get(keys[index.column()])
        if value is None:
            return "—"
        return str(value)

    def replace(self, items):
        rows = tuple(items)[:128]
        if tuple(x.get("signal_id") for x in rows) != tuple(
                x.get("signal_id") for x in self.rows):
            self.beginResetModel()
            self.rows = rows
            self.endResetModel()
            return
        old = self.rows
        self.rows = rows
        for i, (prior, current) in enumerate(zip(old, rows)):
            if prior != current:
                self.dataChanged.emit(self.index(i, 0), self.index(i, 5),
                                      [Qt.ItemDataRole.DisplayRole])


class LiveDeviceModel(QAbstractTableModel):
    COLUMNS = ("Instance", "Device ID", "State", "Reads OK", "Errors", "Reconnects", "Last RX")

    def __init__(self, parent=None):
        super().__init__(parent)
        self.rows: tuple[dict, ...] = ()

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.rows)

    def columnCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.COLUMNS)

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if role != Qt.ItemDataRole.DisplayRole:
            return None
        return self.COLUMNS[section] if orientation == Qt.Orientation.Horizontal else str(section + 1)

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or role not in (
                Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.ToolTipRole):
            return None
        item = self.rows[index.row()]
        if role == Qt.ItemDataRole.ToolTipRole:
            return item.get("last_error") or item.get("device_id")
        key = ("instance_id", "device_id", "state", "reads_ok", "read_errors",
               "reconnects", "last_rx_utc")[index.column()]
        value = item.get(key)
        return str(value) if value is not None else "—"

    def replace(self, rows):
        # Device-page summary is bounded to <=128 entries, not per-signal widgets.
        self.beginResetModel()
        self.rows = tuple(rows)[:128]
        self.endResetModel()


class LiveRuntimePanel(QWidget):
    """Visibility-aware, bounded live monitor panel.

    Offscreen/test IPC endpoint is supplied by the app composition layer. The
    IPC client creates the request socket; never run it in the Qt GUI thread.
    """

    def __init__(self, hub, endpoint, parent=None):
        super().__init__(parent)
        self.hub = hub
        self.endpoint = endpoint  # (loopback port, test token), never rendered
        self.active = False
        self.busy = False
        self._epoch = 0
        self._offset = 0
        self._tick = 0
        self._session = None    # touched only by single non-overlapping worker
        self._capabilities = None
        self._last_page_size = 0
        self.current_state = "DISCONNECTED"
        self.values = LiveValueModel(self)
        self.devices = LiveDeviceModel(self)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        toolbar = QHBoxLayout()
        self.connection = QLabel("TEST IPC · DISCONNECTED")
        self.connection.setObjectName("StatusWarning")
        toolbar.addWidget(self.connection)
        toolbar.addStretch()
        self.previous = QPushButton("Previous 128")
        self.next = QPushButton("Next 128")
        self.reconnect = QPushButton("Reconnect")
        self.previous.clicked.connect(lambda: self._move(-128))
        self.next.clicked.connect(lambda: self._move(128))
        self.reconnect.clicked.connect(self.force_refresh)
        for button in (self.previous, self.next, self.reconnect):
            toolbar.addWidget(button)
        layout.addLayout(toolbar)

        self.info = QLabel(
            "Global runtime signals · read-only. Not filtered by the offline engineering explorer; "
            "Device/Signal identity binding requires Store #3 and RFC #13."
        )
        self.info.setWordWrap(True)
        self.info.setObjectName("FieldHint")
        layout.addWidget(self.info)
        self.signal_table = QTableView(self)
        self.signal_table.setModel(self.values)
        self.signal_table.setAlternatingRowColors(True)
        self.signal_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.signal_table.verticalHeader().setDefaultSectionSize(23)
        self.signal_table.horizontalHeader().setStretchLastSection(True)
        self.signal_table.setColumnWidth(0, 220)
        self.signal_table.setColumnWidth(1, 110)
        self.signal_table.setColumnWidth(2, 160)
        self.signal_table.setColumnWidth(3, 205)
        layout.addWidget(self.signal_table, 1)

        self.devices_label = QLabel("Runtime device connections · awaiting IPC")
        layout.addWidget(self.devices_label)
        self.device_table = QTableView(self)
        self.device_table.setModel(self.devices)
        self.device_table.setAlternatingRowColors(True)
        self.device_table.verticalHeader().setDefaultSectionSize(23)
        self.device_table.horizontalHeader().setStretchLastSection(True)
        self.device_table.setMaximumHeight(165)
        layout.addWidget(self.device_table)

        self.page = QLabel("No runtime signal snapshot")
        self.page.setObjectName("Muted")
        layout.addWidget(self.page)
        self._timer = QTimer(self)
        self._timer.setInterval(450)
        self._timer.timeout.connect(self._refresh)

    def activate(self):
        if not self.endpoint:
            return
        self.active = True
        self._timer.start()
        self._refresh()

    def deactivate(self):
        self.active = False
        self._timer.stop()
        self._epoch += 1

    def _move(self, step):
        if step > 0 and self._last_page_size < 128:
            return
        new = max(0, self._offset + step)
        if new == self._offset:
            return
        self._offset = new
        self._epoch += 1
        self._last_page_size = 0
        self.page.setText("Loading runtime viewport…")
        if not self.busy:
            self._refresh()

    def force_refresh(self):
        self._epoch += 1
        self._capabilities = None
        if not self.busy:
            self._refresh()

    def _refresh(self):
        if not self.active or self.busy or self.endpoint is None:
            return
        epoch, offset = self._epoch, self._offset
        tick = self._tick
        self._tick += 1
        self.busy = True
        def work():
            return asyncio.run(self._query(offset, tick))
        def done(result):
            self.busy = False
            if not self.active or epoch != self._epoch:
                if self.active:
                    self._refresh()
                return
            self.current_state = result["state"]
            if self.current_state != "CONNECTED":
                self.connection.setText("TEST IPC · DISCONNECTED")
                self.values.replace(())
                self.devices.replace(())
                self._last_page_size = 0
                self.page.setText(result.get("error_code") or "Gateway runtime unreachable")
                self.devices_label.setText("Runtime device status unavailable")
                return
            self.values.replace(result["items"])
            self._last_page_size = len(result["items"])
            self.connection.setText(
                "TEST IPC · CONNECTED · " + result.get("runtime_state", "Unknown"))
            self.page.setText(
                f"Global signals {offset + 1}–{offset + len(result['items'])} "
                f"· {'snapshot' if result['full_snapshot'] else 'delta'} "
                f"· {result.get('source', 'UNKNOWN')}")
            if result.get("devices") is not None:
                self.devices.replace(result["devices"])
                self.devices_label.setText("Runtime device connections · read-only")
        def failure(message):
            self.busy = False
            if epoch == self._epoch:
                self.current_state = "DISCONNECTED"
                self.values.replace(())
                self.devices.replace(())
                self._last_page_size = 0
                self.connection.setText("TEST IPC · DISCONNECTED")
                self.page.setText("Gateway IPC worker failed (details in diagnostics)")
        if not self.hub.submit(work, done, error=failure):
            self.busy = False

    async def _query(self, offset: int, tick: int) -> dict:
        """All access to the session and IPC socket occurs in the worker."""
        try:
            from arpy_gateway.ipc.client import ViewportSession, request
            port, token = self.endpoint
            if self._session is None:
                self._session = ViewportSession(offset, 128)
            elif self._session.offset != offset:
                self._session.set_viewport(offset, 128)
            if self._capabilities is None:
                hello = await request(port, token, "hello", timeout=3)
                if hello.get("api_version") != "1.0" or hello.get("mutable_operations") is not False:
                    raise RuntimeError("Unsupported or unsafe runtime IPC capability")
                self._capabilities = frozenset(hello.get("capabilities", ()))
                if not {"health", "signals.page", "signals.delta"} <= self._capabilities:
                    raise RuntimeError("Runtime is missing read-only monitoring capabilities")
            value = await self._session.refresh(port, token)
            if value["state"] != "CONNECTED":
                self._capabilities = None
                return value
            result = dict(value)
            result["runtime_state"] = "Runtime reachable"
            if tick % 3 == 0:
                health = await request(port, token, "health", timeout=3)
                result["runtime_state"] = str(health.get("state", "UNKNOWN"))
                if "devices.page" in self._capabilities:
                    page = await request(port, token, "devices.page",
                                         {"offset": 0, "limit": 128}, timeout=3)
                    result["devices"] = page.get("items", ())
            return result
        except Exception:
            # Fail closed in GUI read model: never keep stale GOOD measurements
            # on version mismatch, auth failure or unexpectedly missing client.
            if self._session is not None:
                self._session.generation = None
                self._session.cursor = None
                self._session.items.clear()
            self._capabilities = None
            return {"state": "DISCONNECTED", "items": [],
                    "error_code": "IPC_UNAVAILABLE_OR_INCOMPATIBLE"}

    def shutdown(self):
        self.deactivate()
        # In-flight bounded worker completes independently; worker callbacks
        # are discarded by WorkerHub on Studio close. Engine is never stopped.
