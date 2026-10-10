"""Virtualized spreadsheet model: bounded page cache; no QStandardItem objects."""
from __future__ import annotations

from collections import OrderedDict
from typing import Any

from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt, Signal
from PySide6.QtGui import QColor

from . import store
from .workers import Workers

PAGE_SIZE = 256
MAX_PAGES = 48  # 12,288 rows cached regardless of database size


class SignalModel(QAbstractTableModel):
    edit_requested = Signal(int, int, object)
    status_changed = Signal(str)
    count_changed = Signal(int)

    def __init__(self, path: str, workers: Workers, parent=None):
        super().__init__(parent)
        self.path = path
        self.workers = workers
        self.view = store.View()
        self.total = 0
        self.generation = 0
        self.cache: OrderedDict[int, list[tuple[Any, ...]]] = OrderedDict()
        self.loading: set[int] = set()
        self.pending: dict[tuple[int, int], Any] = {}
        self.refresh()

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else self.total

    def columnCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(store.COLUMNS)

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if role != Qt.ItemDataRole.DisplayRole:
            return None
        if orientation == Qt.Orientation.Horizontal:
            return store.COLUMNS[section].title
        return str(section + 1)

    def flags(self, index):
        if not index.isValid():
            return Qt.ItemFlag.NoItemFlags
        flags = Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsEditable
        if store.COLUMNS[index.column()].kind == "bool":
            flags |= Qt.ItemFlag.ItemIsUserCheckable
        return flags

    def _row(self, row_number: int):
        page_number = row_number // PAGE_SIZE
        if page_number not in self.cache:
            self._request_page(page_number)
            return None
        self.cache.move_to_end(page_number)
        values = self.cache[page_number]
        offset = row_number % PAGE_SIZE
        return values[offset] if offset < len(values) else None

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or index.row() >= self.total:
            return None
        col = index.column()
        spec = store.COLUMNS[col]
        if role == Qt.ItemDataRole.TextAlignmentRole:
            if spec.kind == "float" or spec.key in ("source_address", "target_address"):
                return Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
            return Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
        if role not in (Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.EditRole, Qt.ItemDataRole.CheckStateRole):
            return None
        values = self._row(index.row())
        if values is None:
            return "…" if col == 0 and role == Qt.ItemDataRole.DisplayRole else None
        current = self.pending.get((index.row(), col), values[col + 1])
        if role == Qt.ItemDataRole.CheckStateRole:
            if spec.kind == "bool":
                return Qt.CheckState.Checked if current else Qt.CheckState.Unchecked
            return None
        if spec.kind == "bool" and role == Qt.ItemDataRole.DisplayRole:
            return ""  # checkbox renders boolean
        if spec.kind == "bool":
            return "true" if current else "false"
        return str(current)

    def setData(self, index, value, role=Qt.ItemDataRole.EditRole):
        if not index.isValid():
            return False
        spec = store.COLUMNS[index.column()]
        if role == Qt.ItemDataRole.CheckStateRole and spec.kind == "bool":
            converted = 1 if value == Qt.CheckState.Checked.value or value == Qt.CheckState.Checked else 0
        elif role == Qt.ItemDataRole.EditRole:
            try:
                converted = store.normalize(spec.key, value)
            except ValueError as exc:
                self.status_changed.emit(str(exc))
                return False
        else:
            return False
        if self._row(index.row()) is None:
            self.status_changed.emit("Cell is still loading; retry in a moment")
            return False
        self.pending[(index.row(), index.column())] = converted
        self.dataChanged.emit(index, index)
        self.edit_requested.emit(index.row(), index.column(), converted)
        return True

    def _request_page(self, page: int):
        if page in self.loading or page in self.cache:
            return
        generation = self.generation
        view = self.view
        self.loading.add(page)
        self.workers.read(
            lambda: store.load_page(self.path, view, page, PAGE_SIZE),
            callback=lambda values: self._page_ready(generation, page, values),
            error=lambda msg: self._page_error(generation, page, msg),
        )

    def _page_error(self, generation, page, message):
        if generation == self.generation:
            self.loading.discard(page)
            self.status_changed.emit(message.splitlines()[0])

    def _page_ready(self, generation: int, page: int, values):
        if generation != self.generation:
            return
        self.loading.discard(page)
        self.cache[page] = values
        self.cache.move_to_end(page)
        if len(self.cache) > MAX_PAGES:
            self.cache.popitem(last=False)
        if values:
            lo = page * PAGE_SIZE
            hi = min(self.total - 1, lo + len(values) - 1)
            self.dataChanged.emit(self.index(lo, 0), self.index(hi, len(store.COLUMNS) - 1))

    def refresh(self):
        self.generation += 1
        generation = self.generation
        self.beginResetModel()
        self.total = 0
        self.cache.clear()
        self.loading.clear()
        self.pending.clear()
        self.endResetModel()
        view = self.view
        self.workers.read(
            lambda: store.count_rows(self.path, view),
            callback=lambda count: self._count_ready(generation, count),
            error=lambda msg: self.status_changed.emit(msg.splitlines()[0]),
        )

    def _count_ready(self, generation, count):
        if generation != self.generation:
            return
        self.beginResetModel()
        self.total = count
        self.endResetModel()
        self.count_changed.emit(count)
        if count:
            self._request_page(0)

    def confirm_edit(self, row: int, col: int, value: Any) -> None:
        """Confirm a cell without resetting selection/focus after every keystroke."""
        self.pending.pop((row, col), None)
        page = row // PAGE_SIZE
        offset = row % PAGE_SIZE
        if page in self.cache and offset < len(self.cache[page]):
            old = self.cache[page][offset]
            updated = list(old)
            updated[col + 1] = value
            self.cache[page][offset] = tuple(updated)
        key = store.FIELDS[col]
        if self.view.search or self.view.protocol != "All" or self.view.sort_field == key:
            # Changing a filter/sort key can move or remove the row.
            self.refresh()
        else:
            index = self.index(row, col)
            self.dataChanged.emit(index, index)

    def cancel_edit(self, row: int, col: int) -> None:
        self.pending.pop((row, col), None)
        index = self.index(row, col)
        self.dataChanged.emit(index, index)

    def change_view(self, *, search=None, protocol=None):
        previous = self.view
        self.view = store.View(
            search=previous.search if search is None else search,
            protocol=previous.protocol if protocol is None else protocol,
            sort_field=previous.sort_field,
            descending=previous.descending,
        )
        self.refresh()

    def sort(self, column, order=Qt.SortOrder.AscendingOrder):
        if not 0 <= column < len(store.COLUMNS):
            return
        previous = self.view
        self.view = store.View(previous.search, previous.protocol,
                               store.COLUMNS[column].key, order == Qt.SortOrder.DescendingOrder)
        self.refresh()
