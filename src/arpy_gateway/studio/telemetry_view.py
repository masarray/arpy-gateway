"""Protocol-scoped monitoring/simulation projections; no physical telemetry fabrication.

These read-only virtual sources are plugged into existing bounded QTableView.
They will be replaced by IPC subscription/view-port projection under issue #8.
"""
from __future__ import annotations

from PySide6.QtCore import Qt

from .demo import Field
from .grid import VirtualSignals


class TelemetrySource:
    def __init__(self, signal_source, simulation=False):
        self.signals = signal_source
        self.demo = signal_source.demo
        self.instance_id = signal_source.instance_id
        self.simulation = simulation
        self.preview_values: dict[str, str] = {}

    def fields(self, instance_id):
        if self.demo.instance(instance_id).protocol == "iec104":
            base = (Field("name", "Signal", width=215),
                    Field("owner", "ASDU Station", width=160),
                    Field("common_address", "CA", width=75),
                    Field("ioa", "IOA", width=95),
                    Field("ti", "TI", width=65))
        else:
            base = (Field("name", "Signal", width=215),
                    Field("owner", "IED / Device", width=160),
                    Field("register_table", "Register FC", width=110),
                    Field("native_address", "PDU Address", width=115))
        if self.simulation:
            return base + (Field("real", "Real Value", width=115),
                           Field("simulation", "Preview Value", width=115),
                           Field("mode", "Source Mode", width=115),
                           Field("delivered", "Delivered", width=95))
        return base + (Field("value", "Value", width=105),
                       Field("quality", "Quality", width=116),
                       Field("timestamp", "Source Timestamp", width=165),
                       Field("cot", "COT", width=85),
                       Field("rx", "RX/TX", width=95))

    def row_count(self, search=""):
        return self.signals.row_count(search)

    def index_for_view(self, *args):
        return self.signals.index_for_view(*args)

    def signal_id(self, instance_id, index):
        return self.signals.signal_id(instance_id, index)

    def page(self, instance_id, offset, limit, search="", descending=False, sort_key="name"):
        records = self.signals.page(instance_id, offset, limit, search, descending, sort_key)
        originals = tuple(field.key for field in self.signals.fields(instance_id))
        fields = self.fields(instance_id)
        columns = tuple(f.key for f in fields)
        out = []
        for entry in records:
            vals = {}
            for key in columns:
                if key in originals:
                    vals[key] = entry[originals.index(key)+2]
            if self.simulation:
                vals.update({"real": "—", "simulation": self.preview_values.get(entry[0], "—"),
                             "mode": "PREVIEW ONLY" if entry[0] in self.preview_values else "Real / Unavailable",
                             "delivered": "NO"})
            else:
                vals.update({"value": "—", "quality": "Unavailable",
                             "timestamp": "—", "cot": "—", "rx": "— / —"})
            out.append((entry[0], entry[1], *(vals.get(k, "—") for k in columns)))
        return out

    def edit(self, *_):
        raise PermissionError("Monitoring and simulation preview cannot edit protocol signals")

    def history(self, *_):
        return 0


class ReadOnlySignalModel(VirtualSignals):
    def flags(self, index):
        if not index.isValid():
            return Qt.ItemFlag.NoItemFlags
        return Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable

    def setData(self, index, value, role=Qt.ItemDataRole.EditRole):
        return False
