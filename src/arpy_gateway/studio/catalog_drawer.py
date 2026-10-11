"""Bounded right-side signal catalog: browse → search → select → review → assign.

GUI-only projection of existing protocol signals; no new domain model. A query
runs on read workers; selected stable signal IDs are resolved before the write
worker validates and commits them. No per-signal Qt widgets or modal dialog.
"""
from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
from threading import RLock
import re

from PySide6.QtCore import (Qt, QAbstractTableModel, QModelIndex, QEasingCurve,
                            QPropertyAnimation, Signal)
from PySide6.QtWidgets import (QAbstractItemView, QComboBox, QFrame, QHBoxLayout,
                               QHeaderView, QLabel, QLineEdit, QPushButton,
                               QSpinBox, QTableView, QVBoxLayout, QWidget)

from .grid import PAGE

MAX_CHOSEN = 2000
MAX_SEARCH_MATCHES = 5000
MAX_CATALOG_PAGES = 12


@dataclass(frozen=True)
class CatalogLookup:
    query: str
    total: int
    indexes: tuple[int, ...] | None
    truncated: bool = False


class CatalogSource:
    """Query only the configured IED. Search is executed off the GUI thread."""

    def __init__(self, project, instance_id: str, scope_id: str):
        self.project, self.instance_id, self.scope_id = project, instance_id, scope_id
        self.source = project.source(instance_id, scope_id)
        self.demo = project.demo

    def _native_address(self, idx: int) -> int:
        inst = self.demo.instance(self.instance_id)
        return int(self.demo._get(inst, idx, "native_address"))

    def _display(self, idx: int) -> tuple[str, str, str, str, str]:
        inst = self.demo.instance(self.instance_id)
        native_name = str(self.demo._get(inst, idx, "name"))
        if not self.demo.has_field_override(self.instance_id, idx, "name"):
            native_name = self.source.owner_name(idx).replace(" ", "") + (
                " / Voltage " + f"{idx+1:06d}")
        if inst.protocol.startswith("modbus"):
            address = str(self._native_address(idx)) + "  ·  " + str(
                self.demo._get(inst, idx, "register_table")).upper()
        else:
            address = "IOA " + str(self.demo._get(inst, idx, "ioa"))
        typ = str(self.demo._get(inst, idx, "value_type"))
        return (self.demo.signal_id(self.instance_id, idx), native_name,
                self.source.owner_name(idx), address, typ)

    def _matched_addresses(self, address: int) -> tuple[int, ...]:
        """O(N/30000 + changed cells) for demo Modbus virtual addresses."""
        a, b = self.source._range()
        candidates = set()
        if address >= 0 and address % 2 == 0:
            k = address // 2
            if k < 30000:
                first = a + ((k-a) % 30000)
                candidates.update(range(first, b, 30000))
        # Address edits are indexed by changed-cell overrides, not full dataset.
        with self.demo._lock:
            candidates.update(i for (iid, i, key), value in self.demo._overrides.items()
                              if iid == self.instance_id and key == "native_address"
                              and value == address)
        return tuple(sorted(i for i in candidates if self.source.contains(i)
                            and self._native_address(i) == address))

    def lookup(self, search: str) -> CatalogLookup:
        query = search.strip()
        if not query:
            return CatalogLookup("", self.source.row_count(), None)
        low = query.casefold()
        if low in ("voltage", "volt", "analog"):
            # Synthetic demo base is generated in address order; production
            # DB implements genuine indexed name search under STORE #3.
            return CatalogLookup(query, self.source.row_count(), None)
        direct = re.fullmatch(r"#\s*(\d{1,7})", low)
        if direct:
            index = int(direct.group(1))-1
            matched = (index,) if self.source.contains(index) else ()
            return CatalogLookup(query, len(matched), matched)
        address = re.fullmatch(r"(?:addr(?:ess)?\s*:\s*)?(\d{1,7})", low)
        if address and self.demo.instance(self.instance_id).protocol.startswith("modbus"):
            value = int(address.group(1))
            # Allow Modbus engineering labels 40001..49999 without mistaking
            # them for PDU offsets; explicit addr:0 is always unambiguous.
            if 40001 <= value <= 49999:
                value -= 40001
            matched = self._matched_addresses(value)
            return CatalogLookup(query, len(matched), matched)

        # General contains search on read worker, bounded output, not UI.
        # This synthetic adapter has no SQLite name index. A real production
        # provider replaces this scan with an indexed FTS / trgm query.
        indexes = []
        a, b = self.source._range()
        with self.demo._lock:
            changed_names = {
                idx: str(value) for (iid, idx, key), value in self.demo._overrides.items()
                if iid == self.instance_id and key == "name"
            }
        for idx in list(range(a, b)) if b-a < 10 else range(a, b):
            candidate = changed_names.get(idx)
            if candidate is None:
                candidate = self.source.owner_name(idx).replace(" ", "") + (
                    " / Voltage " + f"{idx+1:06d}")
            if low in candidate.casefold():
                indexes.append(idx)
                if len(indexes) > MAX_SEARCH_MATCHES:
                    break
        if len(indexes) <= MAX_SEARCH_MATCHES:
            for idx in self.source._extra():
                if low in self._display(idx)[1].casefold():
                    indexes.append(idx)
                    if len(indexes) > MAX_SEARCH_MATCHES:
                        break
        truncated = len(indexes) > MAX_SEARCH_MATCHES
        if truncated:
            indexes = indexes[:MAX_SEARCH_MATCHES]
        return CatalogLookup(query, len(indexes), tuple(indexes), truncated)

    def index_for_row(self, row: int, lookup: CatalogLookup) -> int:
        if row < 0 or row >= lookup.total:
            raise IndexError("Catalog row outside search result")
        if lookup.indexes is not None:
            return lookup.indexes[row]
        return self.source.index_for_view(self.instance_id, row)

    def page(self, start: int, size: int, lookup: CatalogLookup):
        if not 1 <= size <= PAGE:
            raise ValueError("Catalog page outside bounded limit")
        return tuple(self._display(self.index_for_row(row, lookup))
                     for row in range(start, min(start + size, lookup.total)))

    def resolve(self, rows: tuple[int, ...], lookup: CatalogLookup):
        if not 1 <= len(rows) <= MAX_CHOSEN:
            raise ValueError("Select 1–2000 catalog rows")
        return tuple(self._display(self.index_for_row(row, lookup)) for row in rows)


class CatalogTableModel(QAbstractTableModel):
    count_changed = Signal(int, bool)
    failure = Signal(str)
    COLUMNS = ("Signal Name", "IED / Device", "Address / FC", "Data Type")

    def __init__(self, workers, parent=None):
        super().__init__(parent)
        self.workers = workers
        self.provider = None
        self.lookup = CatalogLookup("", 0, ())
        self.generation = 0
        self._pages = OrderedDict()
        self._pending = set()

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else self.lookup.total

    def columnCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else 4

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if role != Qt.ItemDataRole.DisplayRole:
            return None
        return (self.COLUMNS[section] if orientation == Qt.Orientation.Horizontal
                else str(section + 1))

    def flags(self, index):
        return (Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsEnabled
                if index.isValid() else Qt.ItemFlag.NoItemFlags)

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or role not in (Qt.ItemDataRole.DisplayRole,
                                               Qt.ItemDataRole.ToolTipRole,
                                               Qt.ItemDataRole.UserRole):
            return None
        page = index.row() // PAGE
        records = self._pages.get(page)
        if records is None:
            self._load(page)
            return "…" if role == Qt.ItemDataRole.DisplayRole else None
        self._pages.move_to_end(page)
        record = records[index.row() % PAGE] if index.row() % PAGE < len(records) else None
        if record is None:
            return None
        return (record[0] if role in (Qt.ItemDataRole.ToolTipRole, Qt.ItemDataRole.UserRole)
                else record[index.column() + 1])

    def _load(self, page: int):
        if (self.provider is None or page in self._pending or page in self._pages
                or len(self._pending) >= 3):
            return
        self._pending.add(page)
        generation, provider, lookup = self.generation, self.provider, self.lookup

        def done(records):
            if generation != self.generation:
                return
            self._pending.discard(page)
            self._pages[page] = records
            self._pages.move_to_end(page)
            while len(self._pages) > MAX_CATALOG_PAGES:
                self._pages.popitem(last=False)
            if records:
                first = page * PAGE
                self.dataChanged.emit(self.index(first, 0),
                                      self.index(first + len(records)-1, 3))
        def failed(msg):
            if generation == self.generation:
                self._pending.discard(page)
                self.failure.emit(msg)
        if not self.workers.submit(lambda: provider.page(page*PAGE, PAGE, lookup),
                                   done, error=failed):
            self._pending.discard(page)

    def search(self, source: CatalogSource, term: str):
        self.generation += 1
        epoch = self.generation
        self.provider = source
        self.beginResetModel()
        self.lookup = CatalogLookup("", 0, ())
        self._pages.clear()
        self._pending.clear()
        self.endResetModel()

        def done(lookup):
            if epoch != self.generation:
                return
            self.beginResetModel()
            self.lookup = lookup
            self.endResetModel()
            self.count_changed.emit(lookup.total, lookup.truncated)
            if lookup.total:
                self._load(0)
        self.workers.submit(lambda: source.lookup(term), done, error=self.failure.emit)


class SelectedSignalsModel(QAbstractTableModel):
    """Only the selected stable identities, never a second 500k table copy."""
    COLUMNS = ("Selected Source", "Address", "Target IOA")

    def __init__(self, parent=None):
        super().__init__(parent)
        self.rows = ()
        self.start = 1001

    def set_rows(self, rows, start):
        self.beginResetModel()
        self.rows = tuple(rows)
        self.start = start
        self.endResetModel()

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.rows)

    def columnCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else 3

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if role != Qt.ItemDataRole.DisplayRole:
            return None
        return (self.COLUMNS[section] if orientation == Qt.Orientation.Horizontal
                else str(section + 1))

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or role != Qt.ItemDataRole.DisplayRole:
            return None
        row = self.rows[index.row()]
        return (row[1], row[3], str(self.start + index.row()))[index.column()]


class SignalCatalogDrawer(QFrame):
    assigned = Signal(object)
    close_requested = Signal()
    WIDTH = 540

    def __init__(self, project, workers, notify, parent=None):
        super().__init__(parent)
        self.setObjectName("SignalCatalogDrawer")
        self.project, self.workers, self.notify = project, workers, notify
        self.dest_instance = ""
        self.station_id = ""
        self._selection_epoch = 0
        self._selected = ()
        self._animation = QPropertyAnimation(self, b"maximumWidth", self)
        self._animation.setDuration(190)
        self._animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.setMaximumWidth(0)
        self.setMinimumWidth(0)
        self._build()

    def _build(self):
        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 12, 14, 11)
        lay.setSpacing(9)
        top = QHBoxLayout()
        title = QLabel("Signal Catalog")
        title.setObjectName("PageTitle")
        top.addWidget(title)
        top.addStretch()
        close = QPushButton("✕")
        close.setFixedWidth(32)
        close.setToolTip("Close signal catalog")
        close.clicked.connect(self.close_requested.emit)
        top.addWidget(close)
        lay.addLayout(top)
        self.subtitle = QLabel("Browse and select actual signal rows before assignment.")
        self.subtitle.setObjectName("Muted")
        lay.addWidget(self.subtitle)
        bar = QHBoxLayout()
        self.protocol = QComboBox()
        self.protocol.currentIndexChanged.connect(self._protocol_changed)
        self.connection = QComboBox()
        self.connection.currentIndexChanged.connect(self.refresh_search)
        bar.addWidget(self.protocol, 1)
        bar.addWidget(self.connection, 1)
        lay.addLayout(bar)
        search_bar = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search name, addr:0, 40001, #1234")
        self.search.returnPressed.connect(self.refresh_search)
        search_bar.addWidget(self.search)
        find = QPushButton("Search")
        find.clicked.connect(self.refresh_search)
        search_bar.addWidget(find)
        lay.addLayout(search_bar)
        self.count_label = QLabel("Choose an IED to browse")
        self.count_label.setObjectName("Muted")
        lay.addWidget(self.count_label)
        self.model = CatalogTableModel(self.workers, self)
        self.table = QTableView()
        self.table.setModel(self.model)
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().setDefaultSectionSize(25)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setColumnWidth(0, 192)
        self.table.setColumnWidth(1, 113)
        self.table.setColumnWidth(2, 98)
        self.table.selectionModel().selectionChanged.connect(self._selection_changed)
        self.model.count_changed.connect(self._count_changed)
        self.model.failure.connect(self.notify)
        lay.addWidget(self.table, 3)

        selection_bar = QHBoxLayout()
        self.selection_label = QLabel("Selected: 0 signals")
        self.selection_label.setObjectName("SectionTitle")
        selection_bar.addWidget(self.selection_label)
        selection_bar.addStretch()
        selection_bar.addWidget(QPushButton("Clear selection", clicked=self.clear_selection))
        lay.addLayout(selection_bar)
        self.preview = SelectedSignalsModel(self)
        self.review = QTableView()
        self.review.setModel(self.preview)
        self.review.setAlternatingRowColors(True)
        self.review.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.review.verticalHeader().setDefaultSectionSize(23)
        self.review.horizontalHeader().setStretchLastSection(True)
        self.review.setColumnWidth(0, 210)
        self.review.setColumnWidth(1, 115)
        lay.addWidget(self.review, 1)

        controls = QHBoxLayout()
        self.ioa = QSpinBox()
        self.ioa.setRange(0, 16777215)
        self.ioa.setValue(1001)
        self.ioa.valueChanged.connect(self._refresh_targets)
        self.ti = QComboBox()
        for label, ti in (("TI 13 · Float", 13), ("TI 35 · Time-tagged scaled", 35),
                          ("TI 11 · Scaled", 11), ("TI 9 · Normalized", 9),
                          ("TI 1 · Binary", 1), ("TI 3 · Double", 3)):
            self.ti.addItem(label, ti)
        controls.addWidget(QLabel("Start IOA"))
        controls.addWidget(self.ioa)
        controls.addWidget(self.ti, 1)
        lay.addLayout(controls)
        self.message = QLabel("No field IO or deployment. Only session draft assignments.")
        self.message.setObjectName("Muted")
        self.message.setWordWrap(True)
        lay.addWidget(self.message)
        self.commit = QPushButton("Assign selected signals")
        self.commit.setObjectName("Primary")
        self.commit.setEnabled(False)
        self.commit.clicked.connect(self.assign)
        lay.addWidget(self.commit)

    def set_destination(self, instance_id: str, station_id: str):
        self.dest_instance, self.station_id = instance_id, station_id
        station = next((x for x in self.project.stations(instance_id)
                        if x.id == station_id), None)
        if station is None:
            raise ValueError("Select an IEC-104 destination ASDU station first")
        self.subtitle.setText(f"Target: {station.name} · Select source signal rows")
        self.protocol.blockSignals(True)
        self.protocol.clear()
        for inst in self.project.instances():
            if inst.id != instance_id and inst.role.value == "client":
                self.protocol.addItem(inst.name, inst.id)
        self.protocol.blockSignals(False)
        self._protocol_changed()

    def _protocol_changed(self):
        self.connection.blockSignals(True)
        self.connection.clear()
        iid = self.protocol.currentData()
        if iid:
            self.connection.addItem("All IEDs / Devices", "")
            for point in self.project.points(iid):
                if point.kind != "allowed_master":
                    self.connection.addItem(point.name, point.id)
        self.connection.blockSignals(False)
        self.refresh_search()

    def refresh_search(self):
        self._selection_epoch += 1
        self._selected = ()
        self.preview.set_rows((), self.ioa.value())
        self.selection_label.setText("Selected: 0 signals")
        self.commit.setEnabled(False)
        iid = self.protocol.currentData()
        if iid:
            self.model.search(CatalogSource(self.project, iid, self.connection.currentData() or ""),
                              self.search.text())
        else:
            self.model.search(CatalogSource(self.project, self.dest_instance, ""),
                              "#0")

    def _count_changed(self, count, truncated):
        self.count_label.setText(f"{count:,} catalog results" +
            (" (first 5,000 matches; narrow search)" if truncated else "") +
            " · Ctrl/Shift to select rows")

    def _selection_changed(self, *args):
        self._selection_epoch += 1
        epoch = self._selection_epoch
        selection = self.table.selectionModel().selection()
        ranges = tuple(selection)
        if not ranges:
            self._selected = ()
            self.preview.set_rows((), self.ioa.value())
            self.selection_label.setText("Selected: 0 signals")
            self.commit.setEnabled(False)
            return
        # Check ranges before expanding; Ctrl+A over 500k must not allocate
        # 500k selected QModelIndex objects on UI thread.
        count = sum(section.height() for section in ranges)
        if count > MAX_CHOSEN:
            self._selected = ()
            self.preview.set_rows((), self.ioa.value())
            self.selection_label.setText("Select at most 2,000 rows")
            self.commit.setEnabled(False)
            return
        rows = tuple(sorted({r for section in ranges
                             for r in range(section.top(), section.bottom() + 1)}))
        if not rows or len(rows) > MAX_CHOSEN or self.model.provider is None:
            return
        provider, lookup, generation = self.model.provider, self.model.lookup, self.model.generation
        def done(signals):
            if epoch != self._selection_epoch or generation != self.model.generation:
                return
            self._selected = signals
            self.preview.set_rows(signals, self.ioa.value())
            self.selection_label.setText(f"Selected: {len(signals):,} signals")
            self.commit.setText(f"Assign {len(signals):,} selected signals → IEC-104")
            self.commit.setEnabled(bool(signals))
        self.workers.submit(lambda: provider.resolve(rows, lookup), done,
                            error=self.notify)

    def _refresh_targets(self):
        self.preview.set_rows(self._selected, self.ioa.value())

    def clear_selection(self):
        self.table.clearSelection()

    def assign(self):
        if not self._selected:
            return
        provider = self.model.provider
        ids = tuple(item[0] for item in self._selected)
        source_id, scope_id = provider.instance_id, provider.scope_id
        target_id, station_id = self.dest_instance, self.station_id
        ioa, ti = self.ioa.value(), self.ti.currentData()
        if ioa + len(ids) - 1 > 16777215:
            self.notify("IOA range exceeds the 24-bit limit")
            return
        self.commit.setEnabled(False)
        self.message.setText(f"Validating {len(ids):,} selected identities & IOAs on write worker…")
        def done(created):
            self.message.setText(f"Assigned {len(created):,} destination signals / mapping edges (offline draft).")
            self._selected = ()
            self.preview.set_rows((), self.ioa.value())
            self.table.clearSelection()
            self.assigned.emit(created)
            self.notify(f"Assigned {len(created):,} source catalog signals to IEC-104 (offline draft)")
        def fail(error):
            self.commit.setEnabled(True)
            self.message.setText(error.splitlines()[0])
            self.notify(error)
        self.workers.submit(lambda: self.project.assign_selected(
            source_id, scope_id, ids, target_id, station_id, ioa, ti),
            done, write=True, error=fail)

    def slide(self, visible: bool, width: int | None = None):
        self._animation.stop()
        self._animation.setStartValue(self.maximumWidth())
        self._animation.setEndValue(width or self.WIDTH if visible else 0)
        self._animation.start()
