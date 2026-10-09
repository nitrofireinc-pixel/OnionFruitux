"""The OnionFruitux window."""

from __future__ import annotations

import sys
from pathlib import Path

from onionfruitux.cli import connect_command, disconnect_command, new_circuit_command
from onionfruitux.config import (
    BRIDGE_TYPES,
    Config,
    Route,
    load_config,
    remove_route,
    save_config,
    upsert_route,
    use_route,
)
from onionfruitux.countries import country_choices
from onionfruitux.errors import OnionError
from onionfruitux.qtutil import load_qt
from onionfruitux.system import read_status

_DATA = Path(__file__).resolve().parent / "data"

_STYLE = """
QWidget { background: #16141f; color: #f4f1ea; font-size: 14px; }
QDialog { background: #16141f; }
QLabel#title { font-size: 22px; font-weight: 600; }
QLabel#muted, QLabel#footer { color: #b7b1c4; }
QTabWidget::pane { border: 1px solid #3a3548; border-radius: 8px; }
QTabBar::tab { background: #221f2e; padding: 8px 16px; margin-right: 4px; }
QTabBar::tab:selected { background: #3a2f57; }
QPushButton {
    background: #2c2840; border: 1px solid #4a4460; border-radius: 8px; padding: 8px 14px;
}
QPushButton:hover { background: #3a3454; }
QPushButton:disabled { color: #8d879c; }
QLineEdit, QPlainTextEdit, QComboBox, QListWidget {
    background: #221f2e; border: 1px solid #4a4460; border-radius: 6px; padding: 4px;
}
QCheckBox { spacing: 8px; }
QCheckBox::indicator { width: 16px; height: 16px; }
"""


def run_window() -> int:
    QtCore, QtGui, QtWidgets = load_qt()
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)
    app.setApplicationName("OnionFruitux")
    app.setStyle("Fusion")
    app.setStyleSheet(_STYLE)
    window = MainWindow()
    window.show()
    return app.exec()


def create_main_window():
    QtCore, QtGui, QtWidgets = load_qt()

    class TorSwitch(QtWidgets.QCheckBox):
        def __init__(self, parent=None):
            super().__init__(parent)
            self.setObjectName("torSwitch")
            self.setFixedSize(88, 44)
            self.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
            self.setAccessibleName("Tor switch")

        def paintEvent(self, event):  # noqa: N802
            painter = QtGui.QPainter(self)
            painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
            painter.setPen(QtCore.Qt.PenStyle.NoPen)
            painter.setBrush(QtGui.QColor("#3dba7a" if self.isChecked() else "#3a3548"))
            painter.drawRoundedRect(1, 6, 86, 32, 16, 16)
            knob = 48 if self.isChecked() else 4
            painter.setBrush(QtGui.QColor("#f7f4ef"))
            painter.drawEllipse(knob, 8, 28, 28)
            painter.end()

        def hitButton(self, pos):  # noqa: N802
            return self.rect().contains(pos)

    class RouteDialog(QtWidgets.QDialog):
        def __init__(self, route: Route | None, parent=None):
            super().__init__(parent)
            self.setWindowTitle("New route" if route is None else "Edit route")
            self.result_route: Route | None = None
            form = QtWidgets.QFormLayout()
            self.name = QtWidgets.QLineEdit(route.name if route else "")
            self.entry = _country_box(QtWidgets, route.entry if route else "")
            self.entry.setObjectName("entryCountry")
            self.exit = _country_box(QtWidgets, route.exit if route else "")
            self.exit.setObjectName("exitCountry")
            self.bridge = QtWidgets.QComboBox()
            self.bridge.setObjectName("bridgeChoice")
            self.bridge.addItem("None", "")
            labels = {
                "plain": "Plain",
                "obfs4": "obfs4",
                "snowflake": "Snowflake",
                "meek": "meek",
                "webtunnel": "WebTunnel",
                "conjure": "Conjure",
            }
            for kind in BRIDGE_TYPES:
                self.bridge.addItem(labels[kind], kind)
            if route and route.bridge:
                index = self.bridge.findData(route.bridge)
                if index >= 0:
                    self.bridge.setCurrentIndex(index)
            self.strict = QtWidgets.QCheckBox("Only use the countries I picked")
            self.strict.setObjectName("onlyCountries")
            self.strict.setChecked(bool(route and route.strict))
            note = QtWidgets.QLabel("A bridge replaces the entry country.")
            note.setObjectName("muted")
            self.bridge.currentIndexChanged.connect(self._bridge_changed)
            form.addRow("Name", self.name)
            form.addRow("Entry country", self.entry)
            form.addRow("Exit country", self.exit)
            form.addRow("Bridge", self.bridge)
            form.addRow("", self.strict)
            form.addRow("", note)
            buttons = QtWidgets.QDialogButtonBox(
                QtWidgets.QDialogButtonBox.StandardButton.Save
                | QtWidgets.QDialogButtonBox.StandardButton.Cancel
            )
            buttons.accepted.connect(self._save)
            buttons.rejected.connect(self.reject)
            layout = QtWidgets.QVBoxLayout(self)
            layout.addLayout(form)
            layout.addWidget(buttons)
            self._bridge_changed()

        def _bridge_changed(self):
            uses_bridge = bool(self.bridge.currentData())
            self.entry.setEnabled(not uses_bridge)

        def _save(self):
            try:
                self.result_route = Route(
                    name=self.name.text(),
                    entry="" if self.bridge.currentData() else self.entry.currentData(),
                    exit=self.exit.currentData() or "",
                    bridge=self.bridge.currentData() or "",
                    strict=self.strict.isChecked(),
                ).cleaned()
            except ValueError as exc:
                QtWidgets.QMessageBox.warning(self, "OnionFruitux", str(exc))
                return
            self.accept()

    class SettingsDialog(QtWidgets.QDialog):
        def __init__(self, parent=None):
            super().__init__(parent)
            self.setWindowTitle("Settings")
            self.resize(640, 480)
            self.cfg = load_config()
            tabs = QtWidgets.QTabWidget()
            tabs.addTab(self._routes_tab(QtWidgets), "Routes")
            tabs.addTab(self._bridges_tab(QtWidgets), "Bridges")
            tabs.addTab(self._network_tab(QtWidgets), "Network")
            buttons = QtWidgets.QDialogButtonBox(
                QtWidgets.QDialogButtonBox.StandardButton.Save
                | QtWidgets.QDialogButtonBox.StandardButton.Close
            )
            buttons.button(QtWidgets.QDialogButtonBox.StandardButton.Save).clicked.connect(
                self._save
            )
            buttons.rejected.connect(self.reject)
            layout = QtWidgets.QVBoxLayout(self)
            layout.addWidget(tabs)
            layout.addWidget(buttons)

        def _routes_tab(self, widgets):
            page = widgets.QWidget()
            self.route_list = widgets.QListWidget()
            self._fill_routes()
            new_button = widgets.QPushButton("New route")
            new_button.setObjectName("newRouteButton")
            edit_button = widgets.QPushButton("Edit")
            use_button = widgets.QPushButton("Use")
            remove_button = widgets.QPushButton("Remove")
            new_button.clicked.connect(lambda: self._edit_route(None))
            edit_button.clicked.connect(self._edit_selected)
            use_button.clicked.connect(self._use_selected)
            remove_button.clicked.connect(self._remove_selected)
            self.route_list.itemDoubleClicked.connect(lambda _item: self._edit_selected())
            row = widgets.QHBoxLayout()
            for button in (new_button, edit_button, use_button, remove_button):
                row.addWidget(button)
            layout = widgets.QVBoxLayout(page)
            layout.addWidget(self.route_list)
            layout.addLayout(row)
            return page

        def _bridges_tab(self, widgets):
            page = widgets.QWidget()
            self.bridge_kind = widgets.QComboBox()
            self.bridge_kind.setObjectName("bridgeKind")
            labels = {
                "plain": "Plain",
                "obfs4": "obfs4",
                "snowflake": "Snowflake",
                "meek": "meek",
                "webtunnel": "WebTunnel",
                "conjure": "Conjure",
            }
            for kind in BRIDGE_TYPES:
                self.bridge_kind.addItem(labels[kind], kind)
            self._bridge_drafts = {
                kind: self.cfg.bridge_lines.get(kind, "") for kind in BRIDGE_TYPES
            }
            self.bridge_text = widgets.QPlainTextEdit()
            self.bridge_text.setObjectName("bridgeLines")
            self.bridge_text.setPlaceholderText("Paste bridge lines, one per line")
            self.bridge_text.setPlainText(self._bridge_drafts["plain"])
            self._current_bridge = "plain"
            self.bridge_kind.currentIndexChanged.connect(self._switch_bridge_editor)
            help_text = widgets.QLabel(
                "Snowflake, meek, and obfs4 can use the published default bridges "
                "when this box is empty. Paste lines from https://bridges.torproject.org "
                "when a network blocks the defaults, or for plain, WebTunnel, and Conjure "
                "bridges. A bridge replaces the entry country."
            )
            help_text.setWordWrap(True)
            help_text.setObjectName("muted")
            layout = widgets.QVBoxLayout(page)
            layout.addWidget(self.bridge_kind)
            layout.addWidget(self.bridge_text)
            layout.addWidget(help_text)
            return page

        def _network_tab(self, widgets):
            page = widgets.QWidget()
            self.lan_direct = widgets.QCheckBox("Keep the local network off Tor")
            self.lan_direct.setObjectName("lanDirect")
            self.lan_direct.setChecked(self.cfg.network.lan_direct)
            self.reject_non_tor = widgets.QCheckBox("Block traffic that cannot use Tor")
            self.reject_non_tor.setObjectName("rejectNonTor")
            self.reject_non_tor.setChecked(self.cfg.network.reject_non_tor)
            self.check_page = widgets.QCheckBox(
                "Open the Tor check page when the switch changes"
            )
            self.check_page.setObjectName("openCheckPage")
            self.check_page.setChecked(self.cfg.network.open_check_page)
            self.relay_ports = widgets.QLineEdit(self.cfg.network.relay_ports)
            self.relay_ports.setObjectName("relayPorts")
            self.relay_ports.setPlaceholderText("80, 443")
            hint = widgets.QLabel(
                "List relay ports when a firewall only allows those outbound ports."
            )
            hint.setObjectName("muted")
            hint.setWordWrap(True)
            form = widgets.QFormLayout(page)
            form.addRow(self.lan_direct)
            form.addRow(self.reject_non_tor)
            form.addRow(self.check_page)
            form.addRow("Relay ports", self.relay_ports)
            form.addRow(hint)
            return page

        def _fill_routes(self):
            self.route_list.clear()
            for route in self.cfg.routes:
                mark = "* " if route.name == self.cfg.active_route else ""
                detail = []
                if route.bridge:
                    detail.append(f"bridge {route.bridge}")
                else:
                    detail.append(f"entry {route.entry or 'any'}")
                detail.append(f"exit {route.exit or 'any'}")
                self.route_list.addItem(f"{mark}{route.name}  ({', '.join(detail)})")
                self.route_list.item(self.route_list.count() - 1).setData(
                    _user_role(), route.name
                )

        def _selected_name(self) -> str:
            item = self.route_list.currentItem()
            if item is None:
                return ""
            return str(item.data(_user_role()) or "")

        def _edit_route(self, route: Route | None):
            dialog = RouteDialog(route, self)
            if not dialog.exec():
                return
            assert dialog.result_route is not None
            saved = upsert_route(self.cfg, dialog.result_route)
            if not self.cfg.active_route:
                self.cfg.active_route = saved.name
            self._persist()
            self._fill_routes()

        def _edit_selected(self):
            name = self._selected_name()
            if not name:
                return
            self._edit_route(self.cfg.route_named(name))

        def _use_selected(self):
            name = self._selected_name()
            if not name:
                return
            try:
                use_route(self.cfg, name)
            except OnionError as exc:
                QtWidgets.QMessageBox.warning(self, "OnionFruitux", str(exc))
                return
            self._persist()
            self._fill_routes()

        def _remove_selected(self):
            name = self._selected_name()
            if not name:
                return
            try:
                remove_route(self.cfg, name)
            except OnionError as exc:
                QtWidgets.QMessageBox.warning(self, "OnionFruitux", str(exc))
                return
            self._persist()
            self._fill_routes()

        def _persist(self):
            try:
                save_config(self.cfg)
            except (OnionError, ValueError) as exc:
                QtWidgets.QMessageBox.warning(self, "OnionFruitux", str(exc))

        def _switch_bridge_editor(self):
            self._bridge_drafts[self._current_bridge] = self.bridge_text.toPlainText()
            kind = self.bridge_kind.currentData()
            self._current_bridge = kind
            self.bridge_text.setPlainText(self._bridge_drafts.get(kind, ""))

        def _save(self):
            self._bridge_drafts[self._current_bridge] = self.bridge_text.toPlainText()
            self.cfg.bridge_lines = dict(self._bridge_drafts)
            self.cfg.network.lan_direct = self.lan_direct.isChecked()
            self.cfg.network.reject_non_tor = self.reject_non_tor.isChecked()
            self.cfg.network.open_check_page = self.check_page.isChecked()
            self.cfg.network.relay_ports = self.relay_ports.text()
            try:
                self.cfg.network = self.cfg.network.cleaned()
                save_config(self.cfg)
            except (OnionError, ValueError) as exc:
                QtWidgets.QMessageBox.warning(self, "OnionFruitux", str(exc))
                return
            self.accept()

    class Window(QtWidgets.QWidget):
        def __init__(self):
            super().__init__()
            self.setWindowTitle("OnionFruitux")
            self.setObjectName("mainWindow")
            self.resize(460, 420)
            self._busy = False
            self.cfg = load_config()
            icon = _load_icon(QtGui)
            if icon is not None:
                self.setWindowIcon(icon)

            title = QtWidgets.QLabel("OnionFruitux")
            title.setObjectName("title")
            subtitle = QtWidgets.QLabel("Send this computer's internet through Tor.")
            subtitle.setObjectName("muted")
            subtitle.setWordWrap(True)

            self.switch = TorSwitch()
            self.switch_label = QtWidgets.QLabel("Off")
            self.switch.toggled.connect(self._toggled)
            switch_row = QtWidgets.QHBoxLayout()
            switch_row.addWidget(self.switch)
            switch_row.addWidget(self.switch_label)
            switch_row.addStretch(1)

            self.status = QtWidgets.QLabel()
            self.status.setObjectName("statusLabel")
            self.status.setWordWrap(True)
            self.route_label = QtWidgets.QLabel()
            self.route_label.setObjectName("muted")
            self.route_label.setWordWrap(True)

            self.circuit = QtWidgets.QPushButton("New circuit")
            self.circuit.setObjectName("newCircuitButton")
            self.circuit.clicked.connect(self._new_circuit)
            settings = QtWidgets.QPushButton("Settings")
            settings.setObjectName("settingsButton")
            settings.clicked.connect(self._open_settings)
            buttons = QtWidgets.QHBoxLayout()
            buttons.addWidget(self.circuit)
            buttons.addWidget(settings)
            buttons.addStretch(1)

            footer = QtWidgets.QLabel(
                "Independent project by Nitrofire Computing. "
                "Not affiliated with The Tor Project or DragonFruit Network."
            )
            footer.setObjectName("footer")
            footer.setWordWrap(True)

            layout = QtWidgets.QVBoxLayout(self)
            layout.setContentsMargins(28, 28, 28, 28)
            layout.setSpacing(12)
            layout.addWidget(title)
            layout.addWidget(subtitle)
            layout.addSpacing(12)
            layout.addLayout(switch_row)
            layout.addWidget(self.status)
            layout.addWidget(self.route_label)
            layout.addLayout(buttons)
            layout.addStretch(1)
            layout.addWidget(footer)

            self._timer = QtCore.QTimer(self)
            self._timer.setInterval(2000)
            self._timer.timeout.connect(self.refresh)
            self._timer.start()
            self.refresh()

        def refresh(self):
            if self._busy:
                return
            self.cfg = load_config()
            state = read_status()
            self._busy = True
            self.switch.setChecked(bool(state["running"]))
            self._busy = False
            self._paint_status(bool(state["running"]))

        def _paint_status(self, running: bool):
            self.switch_label.setText("On" if running else "Off")
            if running:
                self.status.setText("This computer is sending new connections through Tor.")
            else:
                self.status.setText("This computer is using its normal network.")
            route = self.cfg.current_route()
            if route.name:
                bits = [route.name]
                if route.bridge:
                    bits.append(f"bridge {route.bridge}")
                else:
                    bits.append(f"entry {route.entry or 'any'}")
                bits.append(f"exit {route.exit or 'any'}")
                self.route_label.setText("Route: " + ", ".join(bits))
            else:
                self.route_label.setText("Route: none")
            self.circuit.setEnabled(running)

        def _toggled(self, checked: bool):
            if self._busy:
                return
            self._busy = True
            self.switch.setEnabled(False)
            try:
                if checked:
                    connect_command()
                else:
                    disconnect_command()
            except OnionError as exc:
                QtWidgets.QMessageBox.warning(self, "OnionFruitux", str(exc))
                self.switch.setChecked(not checked)
            finally:
                self.switch.setEnabled(True)
                self._busy = False
            self.refresh()

        def _new_circuit(self):
            try:
                new_circuit_command()
            except OnionError as exc:
                QtWidgets.QMessageBox.warning(self, "OnionFruitux", str(exc))
                return
            QtWidgets.QMessageBox.information(
                self, "OnionFruitux", "Tor is building a new circuit."
            )

        def _open_settings(self):
            dialog = SettingsDialog(self)
            dialog.exec()
            self.refresh()

    return Window, SettingsDialog, RouteDialog


def _country_box(widgets, selected: str):
    box = widgets.QComboBox()
    for code, label in country_choices():
        box.addItem(label, code)
    if selected:
        index = box.findData(selected)
        if index >= 0:
            box.setCurrentIndex(index)
    return box


def _load_icon(QtGui):
    path = _DATA / "onionfruitux.svg"
    if not path.exists():
        return None
    icon = QtGui.QIcon(str(path))
    if icon.isNull():
        return None
    return icon


# UserRole is 0x0100 in Qt.
def _user_role() -> int:
    QtCore, _QtGui, _QtWidgets = load_qt()
    return int(QtCore.Qt.ItemDataRole.UserRole)


class MainWindow:
    """The window. Constructing it builds the Qt widget."""

    def __new__(cls):
        window_cls, _settings, _route = create_main_window()
        return window_cls()
