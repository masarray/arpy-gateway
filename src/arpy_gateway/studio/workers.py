"""All SQLite reads and writes happen off the Qt GUI thread.

One single-threaded writer pool preserves the order of editing/undo commands.
A separate reader pool keeps scrolling and searching responsive while saving.
"""
from __future__ import annotations

import traceback
from collections.abc import Callable
from typing import Any

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal


class TaskEvents(QObject):
    completed = Signal(object)
    failed = Signal(str)


class Task(QRunnable):
    def __init__(self, func: Callable[[], Any]):
        super().__init__()
        self.func = func
        self.events = TaskEvents()
        self.setAutoDelete(False)

    def run(self):
        try:
            self.events.completed.emit(self.func())
        except Exception as exc:
            self.events.failed.emit(f"{exc}\n\n{traceback.format_exc(limit=4)}")


class Workers(QObject):
    """Keep tasks alive until delivered to UI; no QObject widgets inside workers."""
    failed = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.read_pool = QThreadPool(self)
        self.read_pool.setMaxThreadCount(2)
        self.write_pool = QThreadPool(self)
        self.write_pool.setMaxThreadCount(1)
        self._active: dict[int, Task] = {}
        self._next = 0

    def _submit(self, pool: QThreadPool, fn: Callable[[], Any], callback=None, error=None):
        job = Task(fn)
        self._next += 1
        ident = self._next
        self._active[ident] = job

        def done(value):
            self._active.pop(ident, None)
            if callback is not None:
                callback(value)

        def failed(message):
            self._active.pop(ident, None)
            if error is not None:
                error(message)
            else:
                self.failed.emit(message)

        job.events.completed.connect(done)
        job.events.failed.connect(failed)
        pool.start(job)

    def read(self, fn, callback=None, error=None):
        self._submit(self.read_pool, fn, callback, error)

    def write(self, fn, callback=None, error=None):
        self._submit(self.write_pool, fn, callback, error)
