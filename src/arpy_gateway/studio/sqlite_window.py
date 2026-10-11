"""Primary SQLite-backed Studio. DEMO is strictly opt-in, never the default.

All engineering projections are read from one persisted Store v2 project,
and all mutations use that same Store's optimistic generation transaction.
No simulated IED rows, no invented runtime samples, no deploy on Save.
"""
from __future__ import annotations

from pathlib import Path
from dataclasses import replace
from arpy_gateway.domain.models import to_mutable
from uuid import uuid4

from PySide6.QtCore import Qt, QModelIndex, QAbstractTableModel
from PySide6.QtWidgets import (
    QApplication, QAbstractItemView, QCheckBox, QComboBox, QDialog,
    QDialogButtonBox, QFormLayout, QFrame, QHBoxLayout, QInputDialog,
    QLabel, QLineEdit, QMainWindow, QPushButton, QSpinBox, QDoubleSpinBox, QSplitter,
    QStackedWidget, QTableView, QTreeView, QVBoxLayout, QWidget,
)
from .design_tokens import apply_theme
from .explorer import ProtocolExplorer
from .live_ipc import LiveRuntimePanel
from .persisted_devices import TCPDeviceDialog, DevicesModel
from .sqlite_project import SQLiteEngineeringProject, Topology
from .v2_window import WorkspaceTabs, title
from .workers import WorkerHub


class ReadModel(QAbstractTableModel):
    def __init__(self, columns, getter, parent=None):
        super().__init__(parent)
        self.columns, self.getter, self.rows=tuple(columns),getter,()
    def rowCount(self,p=QModelIndex()):
        return 0 if p.isValid() else len(self.rows)
    def columnCount(self,p=QModelIndex()):
        return 0 if p.isValid() else len(self.columns)
    def headerData(self,n,orientation,role=Qt.ItemDataRole.DisplayRole):
        if role!=Qt.ItemDataRole.DisplayRole:return None
        return self.columns[n] if orientation==Qt.Orientation.Horizontal else str(n+1)
    def data(self,index,role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or role not in (Qt.ItemDataRole.DisplayRole,
                                               Qt.ItemDataRole.ToolTipRole):
            return None
        values=self.getter(self.rows[index.row()])
        value=values[-1] if role==Qt.ItemDataRole.ToolTipRole else values[index.column()]
        return "—" if value is None else str(value)
    def replace(self,rows):
        self.beginResetModel()
        self.rows=tuple(rows)[:128]
        self.endResetModel()


class SignalEditDialog(QDialog):
    def __init__(self,current=None,parent=None):
        super().__init__(parent)
        self.setWindowTitle("Edit Signal" if current else "Add Modbus Signal")
        lay=QVBoxLayout(self)
        form=QFormLayout()
        self.name=QLineEdit(current.name if current else "")
        self.family=QComboBox();self.family.addItems(["analog","binary","counter"])
        self.dtype=QComboBox();self.dtype.addItems(
            ["float32","uint16","int16","float64","uint32","int32","bool"])
        self.table=QComboBox();self.table.addItems(["holding","input","coil","discrete"])
        self.address=QSpinBox();self.address.setRange(0,65535)
        self.byte_order=QComboBox();self.byte_order.addItems(["big","little"])
        self.word_order=QComboBox();self.word_order.addItems(["big","little"])
        self.scale=QDoubleSpinBox();self.scale.setRange(-1000000,1000000)
        self.scale.setDecimals(6);self.scale.setValue(1.0)
        self.unit=QLineEdit(current.unit if current else "")
        self.enabled=QCheckBox("Enabled");self.enabled.setChecked(
            current.enabled if current else True)
        if current:
            self.family.setCurrentText(current.family.value)
            self.dtype.setCurrentText(current.value_type.value)
            self.table.setCurrentText(current.parameters.get("register_table","holding"))
            self.address.setValue(int(current.parameters.get("native_address",0)))
            self.byte_order.setCurrentText(str(current.parameters.get("byte_order","big")))
            self.word_order.setCurrentText(str(current.parameters.get("word_order","big")))
            self.scale.setValue(float(current.parameters.get("scale",1.0)))
        for caption,field in (
            ("Signal name",self.name),("Family",self.family),
            ("Value type",self.dtype),("Register table",self.table),
            ("Native 0-based address",self.address),
            ("Byte Order",self.byte_order),("Word Order",self.word_order),
            ("Scaling Factor",self.scale),("Unit",self.unit),
            ("Enabled",self.enabled)):
            form.addRow(caption,field)
        lay.addLayout(form)
        lay.addWidget(QLabel("Saved to selected IED in SQLite · read-only field telemetry"))
        controls=QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save|
            QDialogButtonBox.StandardButton.Cancel)
        controls.accepted.connect(self.accept)
        controls.rejected.connect(self.reject)
        lay.addWidget(controls)
    def fields(self):
        return {"name":self.name.text(),"family":self.family.currentText(),
                "value_type":self.dtype.currentText(),"direction":"inbound",
                "register_table":self.table.currentText(),
                "native_address":self.address.value(),
                "byte_order":self.byte_order.currentText(),
                "word_order":self.word_order.currentText(),
                "scale":self.scale.value(),
                "unit":self.unit.text(),"enabled":self.enabled.isChecked()}


class SQLiteStudioWindow(QMainWindow):
    WORKSPACES=("Gateway Config","Protocol Config","Protocol Signals",
                "Protocol Mapping","Protocol Monitoring","Value Simulation")

    def __init__(self, path:Path, *, runtime_endpoint=None, parent=None):
        super().__init__(parent)
        self.repo=SQLiteEngineeringProject(Path(path))
        self.hub=WorkerHub(self)
        self.topology=Topology(0,(),(),())
        self.loaded=False
        self.busy=False
        self.instance_id=""
        self.scope_id=""
        self.node_kind=""
        self.signal_cursor=""
        self.mapping_cursor=""
        self.signal_more=False
        self.mapping_more=False
        self._first_open=True
        self._build(runtime_endpoint)
        self.setWindowTitle("ARPY Gateway Studio · SQLite Engineering Draft")
        self.resize(1450,850)
        self.setMinimumSize(1040,620)
        apply_theme(QApplication.instance(),"light")
        self._run(self.repo.prepare,self._topology_loaded)

    def _make_page(self,heading,subtitle):
        page=QWidget()
        layout=QVBoxLayout(page)
        layout.setContentsMargins(14,12,14,10)
        layout.setSpacing(9)
        layout.addWidget(title(heading,subtitle))
        return page,layout

    def _table(self,model):
        table=QTableView(self)
        table.setModel(model)
        table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        table.setAlternatingRowColors(True)
        table.verticalHeader().setDefaultSectionSize(23)
        table.horizontalHeader().setStretchLastSection(True)
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        return table

    def _button(self,caption,callback):
        button=QPushButton(caption)
        button.clicked.connect(callback)
        return button

    def _build(self,endpoint):
        root=QWidget()
        layout=QVBoxLayout(root);layout.setContentsMargins(0,0,0,0);layout.setSpacing(0)
        header=QFrame();header.setObjectName("AppHeader")
        row=QHBoxLayout(header);row.setContentsMargins(12,6,12,6)
        brand=QLabel("ARPY");brand.setObjectName("Brand");row.addWidget(brand)
        self.navigation=WorkspaceTabs()
        self.navigation.setObjectName("WorkspaceTabs")
        for name in self.WORKSPACES:self.navigation.addTab(name)
        row.addWidget(self.navigation,1)
        self.theme=QComboBox();self.theme.addItems(["Light","Dark"])
        self.theme.currentIndexChanged.connect(
            lambda i:apply_theme(QApplication.instance(),"light" if i==0 else "dark"))
        row.addWidget(self.theme)
        flag=QLabel("SAVED DRAFT · NOT DEPLOYED");flag.setObjectName("StatusWarning")
        row.addWidget(flag)
        layout.addWidget(header)
        split=QSplitter(Qt.Orientation.Horizontal)
        explorer=QWidget()
        side=QVBoxLayout(explorer);side.setContentsMargins(8,10,8,7)
        side.addWidget(QLabel("PROTOCOL EXPLORER · SQLite"))
        self.disabled=QCheckBox("Show disabled")
        self.disabled.toggled.connect(self._rebuild_tree)
        side.addWidget(self.disabled)
        self.tree_model=ProtocolExplorer(self.topology,self)
        self.tree=QTreeView()
        self.tree.setModel(self.tree_model)
        self.tree.setUniformRowHeights(True)
        self.tree.setHeaderHidden(True)
        self.tree.selectionModel().currentChanged.connect(self._selected_tree)
        side.addWidget(self.tree,1)
        split.addWidget(explorer)
        self.pages=QStackedWidget()

        gateway,g=self._make_page("Gateway Config",
            "Saved SQLite project; engineering revision ≠ runtime active revision")
        self.gateway_info=QLabel("Opening database…")
        self.gateway_info.setWordWrap(True);g.addWidget(self.gateway_info)
        g.addWidget(self._button("Refresh from Database",self.reload))
        g.addStretch()
        self.pages.addWidget(gateway)

        config,c=self._make_page("Protocol Config",
            "IED Connections and Station settings share the SAME SQLite database")
        self.connection_title=QLabel("Loading saved connections…")
        self.connection_title.setObjectName("SectionTitle")
        c.addWidget(self.connection_title)
        actions=QHBoxLayout()
        self.add_ied=self._button("+ Add IED",self.add_device)
        self.edit_ied=self._button("Edit Selected",self.edit_device)
        self.validate=self._button("Validate & Stage",self.stage)
        self.refresh=self._button("Refresh",self.reload)
        for btn in (self.add_ied,self.edit_ied,self.validate,self.refresh):
            actions.addWidget(btn)
        actions.addStretch();c.addLayout(actions)
        self.devices_model=DevicesModel(self)
        self.devices=self._table(self.devices_model)
        self.devices.selectionModel().currentChanged.connect(lambda *_:self._buttons())
        self.devices.doubleClicked.connect(lambda *_:self.edit_device())
        c.addWidget(self.devices,1)
        self.stations_model=ReadModel(
            ("Station","Common Address","Stable ID"),
            lambda s:(s.name,s.settings.get("common_address"),s.id),self)
        self.stations=self._table(self.stations_model)
        self.stations.hide();c.addWidget(self.stations,1)
        self.add_station=self._button("+ Add Data Station",self.create_station)
        self.add_station.hide();c.addWidget(self.add_station)
        self.edit_station=self._button("Edit Selected Station",self.edit_station_dialog)
        self.edit_station.hide();c.addWidget(self.edit_station)
        self.general_panel=QWidget(self)
        gp=QVBoxLayout(self.general_panel)
        form=QFormLayout()
        self.general_name=QLineEdit()
        self.general_enabled=QCheckBox("Enabled")
        self.general_identity=QLabel("Selected protocol instance")
        self.general_identity.setWordWrap(True)
        form.addRow("Protocol / Role",self.general_identity)
        form.addRow("Instance name",self.general_name)
        form.addRow("Instance status",self.general_enabled)
        self.general_fields={}
        self.general_dynamic=QFormLayout()
        gp.addLayout(form)
        gp.addLayout(self.general_dynamic)
        self.save_general=self._button("Save General Settings",self.save_general_settings)
        gp.addWidget(self.save_general)
        gp.addStretch()
        self.general_panel.hide()
        c.addWidget(self.general_panel,1)
        self.note=QLabel("Only saved IEDs are shown here. No demo Meter 01 / Meter 02.")
        c.addWidget(self.note)
        self.pages.addWidget(config)

        signals,s=self._make_page("Protocol Signals",
            "Stored signal attributes and explicit IED binding · no generated demo rows")
        self.signal_context=QLabel("Select an IED to edit its saved signals")
        s.addWidget(self.signal_context)
        bar=QHBoxLayout()
        self.add_signal=self._button("+ Add Signal",self.add_signal_dialog)
        self.edit_signal=self._button("Edit Selected",self.edit_signal_dialog)
        self.next_signal=self._button("Next 128",self.signal_next)
        self.first_signal=self._button("First Page",self.signal_first)
        for btn in (self.add_signal,self.edit_signal,self.next_signal,self.first_signal):
            bar.addWidget(btn)
        bar.addStretch();s.addLayout(bar)
        self.signal_model=ReadModel(
            ("Name","Family","Value type","Table","Native Addr","Byte Order",
             "Word Order","Scale","Unit","Owner ID","Signal ID"),
            lambda r:(r[0].name,r[0].family.value,r[0].value_type.value,
                      r[0].parameters.get("register_table","—"),
                      r[0].parameters.get("native_address","—"),
                      r[0].parameters.get("byte_order","big"),
                      r[0].parameters.get("word_order","big"),
                      r[0].parameters.get("scale",1.0),
                      r[0].unit,r[1] or r[2] or "Unassigned",r[0].id),self)
        self.signals=self._table(self.signal_model)
        self.signals.selectionModel().currentChanged.connect(lambda *_:self._buttons())
        self.signals.doubleClicked.connect(lambda *_:self.edit_signal_dialog())
        s.addWidget(self.signals,1)
        self.pages.addWidget(signals)

        mapping,m=self._make_page("Protocol Mapping",
            "SQLite mappings by immutable source/destination signal IDs")
        self.mapping_model=ReadModel(
            ("Source ID","Destination ID","Kind","Mapping ID"),
            lambda e:(e.source_signal_id,e.destination_signal_id,e.kind.value,e.id),self)
        b=QHBoxLayout()
        self.add_mapping=self._button("+ Add Mapping",self.create_mapping)
        self.next_mapping=self._button("Next 128",self.mapping_next)
        b.addWidget(self.add_mapping);b.addWidget(self.next_mapping);b.addStretch()
        m.addLayout(b)
        self.mappings=self._table(self.mapping_model)
        m.addWidget(self.mappings,1)
        self.pages.addWidget(mapping)

        monitoring,mn=self._make_page("Protocol Monitoring",
            "Live runtime values only via read-only authenticated IPC; no simulated Good")
        self.live=LiveRuntimePanel(self.hub,endpoint,self)
        mn.addWidget(self.live,1)
        self.pages.addWidget(monitoring)
        simulation,sm=self._make_page("Value Simulation",
            "Physical commands blocked; simulation requires runtime safety approval")
        sm.addWidget(QLabel("No simulated or physical values are being transmitted."))
        sm.addStretch()
        self.pages.addWidget(simulation)
        split.addWidget(self.pages)
        split.setChildrenCollapsible(False)
        split.setSizes([232,1190])
        layout.addWidget(split,1)
        self.setCentralWidget(root)
        self.navigation.currentChanged.connect(self.workspace_changed)
        self.navigation.setCurrentRow(1)
        self.statusBar().showMessage("Opening SQLite engineering database…")
        self._buttons()

    def _run(self,work,done):
        if self.busy:return False
        self.busy=True;self._buttons()
        def success(result):
            self.busy=False
            done(result)
            self._buttons()
        def fail(message):
            self.busy=False
            self.statusBar().showMessage(
                "NOT SAVED · "+str(message).splitlines()[0][:190],0)
            self._buttons()
        if not self.hub.submit(work,success,write=True,error=fail):
            self.busy=False;self._buttons()
            return False
        return True

    def _topology_loaded(self,topology):
        self.loaded=True
        self.topology=topology
        self.tree_model.project=topology
        self.gateway_info.setText(
            f"Project: {self.repo.project_id}\n"
            f"Draft revision: {topology.generation}\n"
            f"Instances: {len(topology.instance_rows)}\n"
            f"Saved IEDs/Peers: {len(topology.point_rows)}\n"
            f"Saved data stations: {len(topology.station_rows)}\n"
            "Active runtime revision: NOT DEPLOYED")
        self._rebuild_tree()
        if self._first_open:
            self._first_open=False
            inst=next((i for i in topology.instance_rows
                       if i.protocol=="modbus_tcp" and i.role.value=="client"),None)
            if inst and self.navigation.currentRow()==1:
                idx=self.tree_model.find("peers:"+inst.id)
                if idx.isValid():
                    self.tree.expand(idx)  # reveal saved IED rows on reopen
                    self.tree.setCurrentIndex(idx)
        self.statusBar().showMessage(
            f"Loaded engineering database generation {topology.generation} · NOT DEPLOYED",0)
        self._show_page()

    def _rebuild_tree(self,*args):
        if not self.loaded:return
        current=self.tree.currentIndex()
        key=current.internalPointer().key if current.isValid() else ""
        self.tree_model.rebuild(self.navigation.currentRow(),self.disabled.isChecked())
        for row in range(self.tree_model.rowCount()):
            self.tree.expand(self.tree_model.index(row,0))
        idx=self.tree_model.find(key) if key else QModelIndex()
        if not idx.isValid() and self.instance_id:
            idx=self.tree_model.first_for(self.instance_id)
        if idx.isValid():
            self.tree.setCurrentIndex(idx)
        elif self.tree_model.rowCount():
            self.tree.setCurrentIndex(self.tree_model.index(0,0))

    def _selected_tree(self,index,old):
        if not index.isValid():return
        node=index.internalPointer()
        self.instance_id,self.scope_id,self.node_kind=(
            node.instance_id,node.scope_id,node.kind)
        self.signal_cursor=""
        self._show_page()

    def workspace_changed(self,index):
        if index<0:return
        self.pages.setCurrentIndex(index)
        self.live.deactivate()
        if index==4:self.live.activate()
        self._rebuild_tree()
        self._show_page()

    def _show_page(self):
        if not self.loaded or self.busy:return
        tab=self.navigation.currentRow()
        if tab==1:self.show_connections()
        elif tab==2:self.show_signals()
        elif tab==3:self.show_mappings()
        self._buttons()

    def show_connections(self):
        iid=self.instance_id
        instance=next((x for x in self.topology.instance_rows if x.id==iid),None)
        if not instance:return
        station=self.node_kind in ("stations","station")
        general=self.node_kind=="general"
        self.connection_title.setText(
            instance.name+" / "+("General Settings" if general else
                                  "ASDU Stations" if station else "IED Connections"))
        self.general_panel.setVisible(general)
        self.devices.setVisible(not station and not general)
        self.stations.setVisible(station and not general)
        self.add_station.setVisible(station and not general)
        self.edit_station.setVisible(station and not general)
        self.add_ied.setVisible(not station and not general
                                and instance.id==self.repo.devices.instance_id)
        self.edit_ied.setVisible(self.add_ied.isVisible())
        if general:
            self._general_form(instance)
        elif station:
            self._run(lambda:self.repo.stations(iid),self.stations_model.replace)
        else:
            scope=self.scope_id if self.node_kind=="peer" else ""
            self._run(lambda:self.repo.connections(iid),
                      lambda rows:self._loaded_devices(rows,scope))

    def _general_form(self,instance):
        self.general_name.setText(instance.name)
        self.general_enabled.setChecked(instance.enabled)
        self.general_identity.setText(
            f"{instance.protocol} / {instance.role.value} · stable ID: {instance.id}")
        while self.general_dynamic.rowCount():
            self.general_dynamic.removeRow(0)
        self.general_fields={}
        for key,value in sorted(instance.settings.items()):
            editor=QLineEdit(str(value))
            editor.setMaximumWidth(340)
            self.general_dynamic.addRow(key.replace("_"," ").title(),editor)
            self.general_fields[key]=(editor,value)

    def save_general_settings(self):
        instance=next((i for i in self.topology.instance_rows
                       if i.id==self.instance_id),None)
        if instance is None:return
        values=to_mutable(instance.settings)
        for key,(editor,prior) in self.general_fields.items():
            raw=editor.text()
            if type(prior) is bool:
                if raw.lower() not in ("true","false","1","0"):
                    self.statusBar().showMessage(f"Invalid boolean for {key}",9000)
                    return
                values[key]=raw.lower() in ("true","1")
            elif type(prior) is int:
                try:values[key]=int(raw)
                except ValueError:
                    self.statusBar().showMessage(f"Invalid integer for {key}",9000)
                    return
            elif type(prior) is float:
                try:values[key]=float(raw)
                except ValueError:
                    self.statusBar().showMessage(f"Invalid number for {key}",9000)
                    return
            else:
                values[key]=raw
        item=replace(instance,name=self.general_name.text().strip(),
                     enabled=self.general_enabled.isChecked(),settings=values)
        gen=self.topology.generation
        self._run(lambda:self.repo.save_instance(
            item,expected_generation=gen),self._saved)

    def _loaded_devices(self,rows,scope=""):
        self.devices_model.set_rows(rows)
        for i,item in enumerate(rows):
            if item.id==scope:
                self.devices.selectRow(i);break

    def show_signals(self):
        iid=self.instance_id
        if not iid:return
        peer={p.id for p in self.topology.points(iid)}
        station={s.id for s in self.topology.stations(iid)}
        p=self.scope_id if self.scope_id in peer else None
        s=self.scope_id if self.scope_id in station else None
        self.signal_context.setText(iid+" / "+(self.scope_id or "All Signals"))
        self._run(lambda:self.repo.signal_page(
            iid,after_id=self.signal_cursor,communication_point_id=p,
            data_station_id=s),self._loaded_signals)

    def _loaded_signals(self,result):
        self.signal_more=result["has_more"]
        self.signal_model.replace(result["rows"])

    def show_mappings(self):
        self._run(lambda:self.repo.mapping_page(after_id=self.mapping_cursor),
                  self._loaded_mappings)

    def _loaded_mappings(self,result):
        self.mapping_more=result["has_more"]
        self.mapping_model.replace(result["rows"])

    def _selected_device(self):
        idx=self.devices.currentIndex()
        return (self.devices_model.rows[idx.row()] if idx.isValid() and
                idx.row()<len(self.devices_model.rows) else None)

    def _selected_signal(self):
        idx=self.signals.currentIndex()
        return (self.signal_model.rows[idx.row()] if idx.isValid() and
                idx.row()<len(self.signal_model.rows) else None)

    def _buttons(self):
        if not hasattr(self,"add_ied"):return
        ready=self.loaded and not self.busy
        for name in ("add_ied","validate","refresh","add_signal","first_signal",
                     "add_mapping","add_station"):
            getattr(self,name).setEnabled(ready)
        self.edit_ied.setEnabled(ready and self._selected_device() is not None)
        self.edit_signal.setEnabled(ready and self._selected_signal() is not None)
        self.save_general.setEnabled(ready)
        self.edit_station.setEnabled(ready and self.stations.currentIndex().isValid())
        self.next_signal.setEnabled(ready and self.signal_more)
        self.next_mapping.setEnabled(ready and self.mapping_more)

    def reload(self):
        self._run(self.repo.topology,self._topology_loaded)

    def _saved(self,result):
        self.signal_cursor=""
        self.mapping_cursor=""
        self.statusBar().showMessage(
            f"Saved SQLite revision {result['generation']} · NOT DEPLOYED",0)
        self.reload()

    def add_device(self):
        self._device_dialog(None)

    def edit_device(self):
        current=self._selected_device()
        if current is not None:self._device_dialog(current)

    def _device_dialog(self,original):
        if self.instance_id!=self.repo.devices.instance_id:
            self.statusBar().showMessage("Select saved Modbus TCP Master instance",9000)
            return
        dialog=TCPDeviceDialog(original,self)
        if dialog.exec()!=QDialog.DialogCode.Accepted:return
        details=dialog.details()
        gen=self.topology.generation
        self._run(lambda:self.repo.save_tcp_device(
            details,expected_generation=gen,original=original),self._saved)

    def add_signal_dialog(self):
        self._signal_dialog(None)

    def edit_signal_dialog(self):
        original=self._selected_signal()
        if original:self._signal_dialog(original[0])

    def _signal_dialog(self,original):
        scope=self.scope_id
        points={p.id for p in self.topology.points(self.instance_id)
                if p.kind=="remote_device"}
        if scope not in points:
            self.statusBar().showMessage("Select a saved IED first",9000)
            return
        dialog=SignalEditDialog(original,self)
        if dialog.exec()!=QDialog.DialogCode.Accepted:return
        fields=dialog.fields()
        iid=self.instance_id
        gen=self.topology.generation
        self._run(lambda:self.repo.save_signal(
            iid,scope,fields,expected_generation=gen,original=original),self._saved)

    def signal_next(self):
        if self.signal_more and self.signal_model.rows:
            self.signal_cursor=self.signal_model.rows[-1][0].id
            self.show_signals()

    def signal_first(self):
        self.signal_cursor=""
        self.show_signals()

    def mapping_next(self):
        if self.mapping_more and self.mapping_model.rows:
            self.mapping_cursor=self.mapping_model.rows[-1].id
            self.show_mappings()

    def create_mapping(self):
        src,ok=QInputDialog.getText(self,"New Mapping","Source stable Signal ID")
        if not ok:return
        dst,ok=QInputDialog.getText(self,"New Mapping","Destination stable Signal ID")
        if not ok:return
        gen=self.topology.generation
        self._run(lambda:self.repo.save_mapping(
            src.strip(),dst.strip(),expected_generation=gen),self._saved)

    def create_station(self):
        iid=self.instance_id
        inst=next((i for i in self.topology.instance_rows if i.id==iid),None)
        if not inst or inst.protocol!="iec104" or inst.role.value!="server":
            self.statusBar().showMessage("Select IEC-104 Server instance",9000)
            return
        ca,ok=QInputDialog.getInt(self,"IEC104 Data Station",
                                  "Common Address",1,1,65534)
        if not ok:return
        gen=self.topology.generation
        self._run(lambda:self.repo.save_station(
            iid,f"ASDU CA {ca}",ca,expected_generation=gen),self._saved)

    def edit_station_dialog(self):
        index=self.stations.currentIndex()
        if not index.isValid() or index.row()>=len(self.stations_model.rows):
            return
        item=self.stations_model.rows[index.row()]
        ca,ok=QInputDialog.getInt(
            self,"Edit IEC104 Data Station","Common Address",
            int(item.settings.get("common_address",1)),1,65534)
        if not ok:return
        gen=self.topology.generation
        self._run(lambda:self.repo.save_station(
            item.instance_id,item.name,ca,expected_generation=gen,original=item),
            self._saved)

    def stage(self):
        gen=self.topology.generation
        self._run(lambda:self.repo.stage(expected_generation=gen),
                  lambda record:self.statusBar().showMessage(
                      "Staged "+record["digest"][:16]+" · NOT DEPLOYED",0))

    def closeEvent(self,event):
        self.live.shutdown()
        self.hub.shutdown()
        super().closeEvent(event)
