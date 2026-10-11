"""Lazy tree hierarchy for six protocol workspaces.

Small immutable topology nodes; no per-signal QTreeWidgetItem. Large signal
catalog remains in virtual QTableView, never added to explorer tree.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from PySide6.QtCore import Qt, QAbstractItemModel, QModelIndex


@dataclass
class ExplorerNode:
    key: str
    title: str
    kind: str
    instance_id: str = ""
    scope_id: str = ""
    parent_node: "ExplorerNode | None" = None
    children: list["ExplorerNode"] = field(default_factory=list)

    def add(self, key: str, title: str, kind: str, scope_id: str = ""):
        node = ExplorerNode(key, title, kind, self.instance_id, scope_id, self)
        self.children.append(node)
        return node


class ProtocolExplorer(QAbstractItemModel):
    """Each workspace projects same identity-based protocol topology."""

    def __init__(self, project, parent=None):
        super().__init__(parent)
        self.project = project
        self.root = ExplorerNode("root", "root", "root")
        self.workspace = 0
        self.include_disabled = False
        self.rebuild(0)

    def rebuild(self, workspace: int, include_disabled: bool | None = None):
        self.beginResetModel()
        self.workspace = workspace
        if include_disabled is not None:
            self.include_disabled = include_disabled
        self.root = ExplorerNode("root", "root", "root")
        for inst in self.project.instances(self.include_disabled):
            title = inst.name
            if not inst.enabled:
                title += "  [disabled]"
            root = ExplorerNode("protocol:"+inst.id, title, "protocol", inst.id, "", self.root)
            self.root.children.append(root)
            if workspace == 0:
                continue
            if workspace == 1:  # Protocol Config
                root.add("general:"+inst.id, "General Settings", "general")
                if inst.protocol == "iec104" and inst.role.value == "server":
                    stations = root.add("stations:"+inst.id, "ASDU Stations", "stations")
                    for station in self.project.stations(inst.id):
                        stations.add(station.id, station.name, "station", station.id)
                    peers = root.add("peers:"+inst.id, "Master Connections", "peers")
                    for point in self.project.points(inst.id):
                        peers.add(point.id, point.name, "peer", point.id)
                else:
                    caption = ("Slave Stations" if inst.protocol == "modbus_rtu" else
                               "IED Connections" if inst.role.value == "client" else
                               "Client Connections")
                    peers = root.add("peers:"+inst.id, caption, "peers")
                    for point in self.project.points(inst.id):
                        peers.add(point.id, point.name, "peer", point.id)
                    if self.project.stations(inst.id):
                        stations = root.add("stations:"+inst.id, "Local Stations", "stations")
                        for station in self.project.stations(inst.id):
                            stations.add(station.id, station.name, "station", station.id)
            elif workspace in (2, 4, 5):  # Signal editor, Monitoring, Simulation
                root.add("all:"+inst.id, "All Signals", "all")
                for point in self.project.points(inst.id):
                    if point.kind not in ("allowed_master", "authorized_master"):
                        root.add(point.id, point.name, "scope", point.id)
                for station in self.project.stations(inst.id):
                    root.add(station.id, station.name, "scope", station.id)
                if workspace == 4:
                    root.add("diag:"+inst.id, "Connection Diagnostics", "diagnostics")
            elif workspace == 3:
                root.add("routes:"+inst.id, "Mappings / Assignments", "routes")
        self.endResetModel()

    def rowCount(self, parent=QModelIndex()):
        node = parent.internalPointer() if parent.isValid() else self.root
        return len(node.children)

    def columnCount(self, parent=QModelIndex()):
        return 1

    def index(self, row, column, parent=QModelIndex()):
        if column != 0 or row < 0:
            return QModelIndex()
        node = parent.internalPointer() if parent.isValid() else self.root
        if row >= len(node.children):
            return QModelIndex()
        return self.createIndex(row, column, node.children[row])

    def parent(self, child):
        if not child.isValid():
            return QModelIndex()
        node = child.internalPointer()
        parent = node.parent_node
        if parent is None or parent is self.root:
            return QModelIndex()
        grand = parent.parent_node
        if grand is None:
            return QModelIndex()
        return self.createIndex(grand.children.index(parent), 0, parent)

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        node = index.internalPointer()
        if role == Qt.ItemDataRole.DisplayRole:
            return node.title
        if role == Qt.ItemDataRole.ToolTipRole:
            return f"{node.kind} · {node.key}"
        if role == Qt.ItemDataRole.UserRole:
            return node.key
        return None

    def flags(self, index):
        if not index.isValid():
            return Qt.ItemFlag.NoItemFlags
        return Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsEnabled

    def find(self, key: str) -> QModelIndex:
        def walk(parent):
            for row in range(self.rowCount(parent)):
                idx = self.index(row, 0, parent)
                if idx.internalPointer().key == key:
                    return idx
                found = walk(idx)
                if found.isValid():
                    return found
            return QModelIndex()
        return walk(QModelIndex())

    def first_for(self, instance_id: str, kind: str = "all"):
        result = self.find(f"{kind}:{instance_id}")
        if not result.isValid():
            result = self.find(f"protocol:{instance_id}")
        return result
