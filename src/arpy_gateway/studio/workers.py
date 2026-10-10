"""Bounded background job coordinator. Callbacks return to GUI QObject thread."""
from __future__ import annotations

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal, Slot
import traceback


class JobSignals(QObject):
    succeeded = Signal(int, object)
    failed = Signal(int, str)


class Job(QRunnable):
    def __init__(self, identity, work):
        super().__init__()
        self.identity = identity
        self.work = work
        self.signals = JobSignals()
        self.setAutoDelete(False)

    def run(self):
        try:
            result = self.work()
        except Exception as exc:
            self.signals.failed.emit(self.identity, f"{exc}\n{traceback.format_exc(limit=3)}")
        else:
            self.signals.succeeded.emit(self.identity, result)


class WorkerHub(QObject):
    error = Signal(str)
    MAX_OUTSTANDING = 24

    def __init__(self, parent=None):
        super().__init__(parent)
        self._read = QThreadPool(self)
        self._read.setMaxThreadCount(2)
        self._write = QThreadPool(self)
        self._write.setMaxThreadCount(1)
        self._ids = 0
        self._active = {}  # identity -> (Job, callback, error callback)
        self._closing = False

    @property
    def outstanding(self):
        return len(self._active)

    def submit(self, work, callback, write=False, error=None):
        if self._closing or len(self._active) >= self.MAX_OUTSTANDING:
            self.error.emit("Worker queue full; wait for pending operations")
            return False
        self._ids += 1
        task = Job(self._ids, work)
        self._active[self._ids] = (task, callback, error)
        task.signals.succeeded.connect(self._on_success)
        task.signals.failed.connect(self._on_error)
        (self._write if write else self._read).start(task)
        return True

    @Slot(int, object)
    def _on_success(self, identity, result):
        state = self._active.pop(identity, None)
        if state and state[1] and not self._closing:
            state[1](result)

    @Slot(int, str)
    def _on_error(self, identity, message):
        state = self._active.pop(identity, None)
        if self._closing:
            return
        if state and state[2]:
            state[2](message)
        else:
            self.error.emit(message)

    def shutdown(self):
        # Threads never own GUI or domain runtime. Completed callbacks can
        # still be safely discarded when QObject disappears on app teardown.
        self._closing = True
        self._read.clear()
        self._write.clear()
        # Never synchronously wait on running jobs from Qt GUI thread.
        # QThreadPool lifecycle drains remaining bounded running jobs on teardown.
