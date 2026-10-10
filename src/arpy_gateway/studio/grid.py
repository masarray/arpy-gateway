"""Virtual Qt view with identity-correct asynchronous editing.

Only ~24*256 rows may exist in the display page cache; data creation and
mutations occur off the main Qt thread. This layer does NOT own persistence.
"""
from __future__ import annotations

from collections import OrderedDict
import csv
import io

from PySide6.QtCore import Qt, QAbstractTableModel, QModelIndex, Signal
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (QApplication, QAbstractItemView, QComboBox,
                               QMessageBox, QStyledItemDelegate, QTableView)

PAGE = 256
MAX_PAGES = 24
MAX_CLIP = 20_000


class ChoiceDelegate(QStyledItemDelegate):
    def createEditor(self, parent, option, index):
        field = index.model().fields[index.column()]
        if field.options:
            box = QComboBox(parent)
            box.setEditable(False)
            box.addItems(list(field.options))
            return box
        return super().createEditor(parent, option, index)

    def setEditorData(self, editor, index):
        if isinstance(editor, QComboBox):
            val = str(index.data(Qt.ItemDataRole.EditRole) or "")
            editor.setCurrentIndex(max(0, editor.findText(val)))
        else:
            super().setEditorData(editor, index)

    def setModelData(self, editor, model, index):
        if isinstance(editor, QComboBox):
            model.setData(index, editor.currentText(), Qt.ItemDataRole.EditRole)
        else:
            super().setModelData(editor, model, index)


class VirtualSignals(QAbstractTableModel):
    info = Signal(str)
    failure = Signal(str)
    row_count_changed = Signal(int)

    def __init__(self, demo, workers, instance_id: str, parent=None):
        super().__init__(parent)
        self.demo, self.workers, self.instance_id = demo, workers, instance_id
        self.fields = demo.fields(instance_id)
        self.total = 0
        self.search = ""
        self.sort_key = "name"
        self.descending = False
        self.generation = 0
        self._cached = OrderedDict()
        self._loading = set()
        self.reload()

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else self.total

    def columnCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.fields)

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if role != Qt.ItemDataRole.DisplayRole:
            return None
        if orientation == Qt.Orientation.Vertical:
            return str(section + 1)
        return self.fields[section].title

    def flags(self, index):
        if not index.isValid():
            return Qt.ItemFlag.NoItemFlags
        return Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsEditable

    def _request(self, page):
        if page in self._cached or page in self._loading or len(self._loading) >= 4:
            return
        epoch, iid, search, descending, sort_key = (
            self.generation, self.instance_id, self.search, self.descending, self.sort_key)
        self._loading.add(page)

        def work():
            return self.demo.page(iid, page * PAGE, PAGE, search, descending, sort_key)

        def done(rows):
            if epoch != self.generation:
                return
            self._loading.discard(page)
            self._cached[page] = rows
            self._cached.move_to_end(page)
            while len(self._cached) > MAX_PAGES:
                self._cached.popitem(last=False)
            top = page * PAGE
            bottom = min(self.total - 1, top + len(rows) - 1)
            if bottom >= top:
                self.dataChanged.emit(self.index(top, 0),
                                      self.index(bottom, len(self.fields)-1),
                                      [Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.EditRole])
        def failed(message):
            if epoch == self.generation:
                self._loading.discard(page)
                self.failure.emit(message)
        if not self.workers.submit(work, done, error=failed):
            self._loading.discard(page)

    def _cached_row(self, row):
        page = row // PAGE
        chunk = self._cached.get(page)
        if chunk is None:
            self._request(page)
            return None
        self._cached.move_to_end(page)
        offset = row % PAGE
        return chunk[offset] if offset < len(chunk) else None

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or role not in (
            Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.EditRole, Qt.ItemDataRole.ToolTipRole):
            return None
        record = self._cached_row(index.row())
        if record is None:
            return "…" if role == Qt.ItemDataRole.DisplayRole else None
        if role == Qt.ItemDataRole.ToolTipRole:
            return record[0]  # immutable signal ID
        return record[index.column() + 2]

    def setData(self, index, value, role=Qt.ItemDataRole.EditRole):
        if role != Qt.ItemDataRole.EditRole or not index.isValid():
            return False
        record = self._cached_row(index.row())
        if record is None:
            return False
        self.submit([(record[0], self.fields[index.column()].key, value)])
        return True

    def submit(self, changes):
        if not changes:
            return
        iid = self.instance_id  # capture immutable identity BEFORE view changes
        def done(count):
            self.info.emit(f"Updated {count:,} cells — session-only draft")
            self.reload()
        self.workers.submit(lambda: self.demo.edit(iid, changes), done,
                            write=True, error=self.failure.emit)

    def reload(self, *, instance_id=None, search=None, descending=None, sort_key=None):
        self.generation += 1
        if instance_id is not None:
            self.instance_id = instance_id
            self.fields = self.demo.fields(instance_id)
        if search is not None:
            self.search = search
        if descending is not None:
            self.descending = descending
        if sort_key is not None:
            self.sort_key = sort_key
        self.beginResetModel()
        self.total = 0
        self._cached.clear()
        self._loading.clear()
        self.endResetModel()
        epoch, query = self.generation, self.search
        def done(total):
            if epoch != self.generation:
                return
            self.beginResetModel()
            self.total = total
            self.endResetModel()
            self.row_count_changed.emit(total)
            if total:
                self._request(0)
        self.workers.submit(lambda: self.demo.row_count(query), done,
                            error=self.failure.emit)

    def sort(self, column, order=Qt.SortOrder.AscendingOrder):
        self.reload(sort_key=self.fields[column].key,
                    descending=(order == Qt.SortOrder.DescendingOrder))

    def view_signal_id(self, view_row):
        if view_row < 0 or view_row >= self.total:
            raise ValueError("Row outside current view")
        # Stable identity resolution is O(1) for the DEMO provider. The STORE
        # adapter (#3) will use a paged ID query, not infer IDs from row number.
        idx = self.demo.index_for_view(self.instance_id, view_row, self.search,
                                       self.descending, self.sort_key)
        return self.demo.signal_id(self.instance_id, idx)


class SignalGrid(QTableView):
    info = Signal(str)
    failure = Signal(str)

    def __init__(self, model, parent=None):
        super().__init__(parent)
        self.setModel(model)
        self.setItemDelegate(ChoiceDelegate(self))
        self.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectItems)
        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.setAlternatingRowColors(True)
        self.setSortingEnabled(True)
        self.setEditTriggers(QAbstractItemView.EditTrigger.DoubleClicked |
                             QAbstractItemView.EditTrigger.EditKeyPressed |
                             QAbstractItemView.EditTrigger.AnyKeyPressed)
        self.verticalHeader().setDefaultSectionSize(23)
        self.verticalHeader().setMinimumSectionSize(20)
        self.horizontalHeader().setStretchLastSection(True)
        self.setWordWrap(False)
        self.setShowGrid(False)
        self.setStyleSheet("QTableView { selection-background-color: #dbeafe;"
                           "selection-color: #173458; }")
        for action, sequence in (
            (self.copy_cells, "Ctrl+C"), (self.paste_cells, "Ctrl+V"),
            (self.fill_down, "Ctrl+D"), (self.fill_right, "Ctrl+R"),
            (self.undo, "Ctrl+Z"), (self.redo, "Ctrl+Y"),
        ):
            key = QShortcut(QKeySequence(sequence), self)
            key.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
            key.activated.connect(action)
        self.model().modelReset.connect(self._apply_columns)
        self._apply_columns()

    def _apply_columns(self):
        for idx, col in enumerate(self.model().fields):
            self.setColumnWidth(idx, col.width)

    def _rect(self):
        selections = self.selectionModel().selection()
        if len(selections) != 1:
            raise ValueError("Select exactly one contiguous rectangular area")
        area = selections[0]
        count = area.width() * area.height()
        if count > MAX_CLIP:
            raise ValueError(f"Clipboard/fill preview limited to {MAX_CLIP:,} cells")
        return area.top(), area.left(), area.bottom(), area.right()

    def copy_cells(self):
        try:
            top, left, bottom, right = self._rect()
        except ValueError as exc:
            self.failure.emit(str(exc)); return
        model = self.model()
        iid, search, desc, key = model.instance_id, model.search, model.descending, model.sort_key
        generation = model.generation
        def work():
            chunks = []
            for offset in range(top, bottom + 1, PAGE):
                chunks.extend(model.demo.page(iid, offset,
                              min(PAGE, bottom + 1 - offset), search, desc, key))
            io = io_buffer = __import__("io").StringIO()
            writer = csv.writer(io_buffer, delimiter="\t", lineterminator="\n")
            for entry in chunks:
                writer.writerow(entry[left + 2:right + 3])
            return io.getvalue()
        def done(text):
            if generation == model.generation:
                QApplication.clipboard().setText(text)
                self.info.emit(f"Copied {(bottom-top+1)*(right-left+1):,} cells")
        model.workers.submit(work, done, error=self.failure.emit)

    def paste_cells(self):
        idx = self.currentIndex()
        if not idx.isValid():
            return
        text = QApplication.clipboard().text()
        matrix = list(csv.reader(io.StringIO(text), delimiter="\t"))
        if matrix and not matrix[-1]:
            matrix.pop()
        if not matrix or not any(matrix):
            return
        width = len(matrix[0])
        if width < 1 or any(len(row) != width for row in matrix):
            self.failure.emit("Clipboard rows must have identical column counts")
            return
        model = self.model()
        if (len(matrix)*width > MAX_CLIP or idx.row()+len(matrix) > model.total
                or idx.column()+width > len(model.fields)):
            self.failure.emit("Paste exceeds selected protocol grid or 20k-cell preview limit")
            return
        iid, search, desc, sort_key = model.instance_id, model.search, model.descending, model.sort_key
        colkeys = tuple(col.key for col in model.fields)
        top, left = idx.row(), idx.column()
        def work():
            batch = []
            for ri, row in enumerate(matrix):
                sid = model.demo.signal_id(iid,
                    model.demo.index_for_view(iid, top+ri, search, desc, sort_key))
                for ci, val in enumerate(row):
                    batch.append((sid, colkeys[left+ci], val))
            return model.demo.edit(iid, batch)
        def done(n):
            model.info.emit(f"Pasted {n:,} cells — session-only draft")
            model.reload()
        model.workers.submit(work, done, write=True, error=self.failure.emit)

    def _fill(self, down):
        try:
            top, left, bottom, right = self._rect()
        except ValueError as exc:
            self.failure.emit(str(exc)); return
        model = self.model()
        iid, search, desc, key = model.instance_id, model.search, model.descending, model.sort_key
        cols = tuple(c.key for c in model.fields)
        def work():
            batch = []
            anchor = model.demo.page(iid, top, 1, search, desc, key)[0]
            if down:
                for row in range(top + 1, bottom + 1):
                    sid = model.demo.signal_id(iid, model.demo.index_for_view(iid, row, search, desc, key))
                    for col in range(left, right+1):
                        batch.append((sid, cols[col], anchor[col+2]))
            else:
                for row in range(top, bottom+1):
                    first = model.demo.page(iid, row, 1, search, desc, key)[0]
                    for col in range(left+1, right+1):
                        batch.append((first[0], cols[col], first[left+2]))
            return model.demo.edit(iid, batch) if batch else 0
        def done(n):
            model.info.emit(f"Filled {n:,} cells")
            model.reload()
        model.workers.submit(work, done, write=True, error=self.failure.emit)

    def fill_down(self): self._fill(True)
    def fill_right(self): self._fill(False)

    def _history(self, redo=False):
        model = self.model()
        def done(count):
            model.info.emit(f"{'Redid' if redo else 'Undid'} {count:,} cells")
            model.reload()
        model.workers.submit(lambda: model.demo.history(redo), done,
                            write=True, error=self.failure.emit)
    def undo(self): self._history(False)
    def redo(self): self._history(True)

    def auto_number(self):
        try:
            top, left, bottom, right = self._rect()
            if left != right or self.model().fields[left].kind != "int":
                raise ValueError("Select one integer-address column")
        except ValueError as exc:
            self.failure.emit(str(exc)); return
        if bottom - top > 24:
            result = QMessageBox.question(self, "Auto-number preview",
                f"Assign ascending addresses to {bottom-top+1:,} cells?\n"
                "A single Undo will reverse the operation.\nNo runtime deployment occurs.")
            if result != QMessageBox.StandardButton.Yes:
                return
        model = self.model()
        iid, search, desc, key = model.instance_id, model.search, model.descending, model.sort_key
        field = model.fields[left].key
        def work():
            first = model.demo.page(iid, top, 1, search, desc, key)[0]
            start = int(first[left+2])
            batch = [(model.demo.signal_id(iid,
                      model.demo.index_for_view(iid, row, search, desc, key)),
                      field, start+row-top) for row in range(top, bottom+1)]
            return model.demo.edit(iid, batch)
        def done(n):
            model.info.emit(f"Auto-numbered {n:,} addresses")
            model.reload()
        model.workers.submit(work, done, write=True, error=self.failure.emit)
