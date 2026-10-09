"""Panel or dock icon with the same switch as the window."""

from __future__ import annotations

import sys

from onionfruitux.cli import connect_command, disconnect_command, new_circuit_command
from onionfruitux.errors import OnionError
from onionfruitux.gui import MainWindow, _load_icon
from onionfruitux.qtutil import load_qt
from onionfruitux.system import read_status


def switch_label(running: bool) -> str:
    return "Turn off" if running else "Turn on"


def run_tray() -> int:
    QtCore, QtGui, QtWidgets = load_qt()
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)
    app.setApplicationName("OnionFruitux")
    app.setQuitOnLastWindowClosed(False)
    if not QtWidgets.QSystemTrayIcon.isSystemTrayAvailable():
        print("This session has no panel tray. Opening the window.", file=sys.stderr)
        window = MainWindow()
        window.show()
        return app.exec()
    icon = _load_icon(QtGui) or app.style().standardIcon(
        QtWidgets.QStyle.StandardPixmap.SP_ComputerIcon
    )
    tray = QtWidgets.QSystemTrayIcon(icon)
    tray.setToolTip("OnionFruitux")
    menu = QtWidgets.QMenu()
    toggle = menu.addAction(switch_label(False))
    circuit = menu.addAction("New circuit")
    show = menu.addAction("Show OnionFruitux")
    menu.addSeparator()
    quit_action = menu.addAction("Quit")
    window_holder: dict[str, object] = {}

    def refresh():
        running = bool(read_status()["running"])
        toggle.setText(switch_label(running))
        circuit.setEnabled(running)
        tray.setToolTip("OnionFruitux is on" if running else "OnionFruitux is off")

    def on_toggle():
        running = bool(read_status()["running"])
        try:
            if running:
                disconnect_command()
            else:
                connect_command()
        except OnionError as exc:
            QtWidgets.QMessageBox.warning(None, "OnionFruitux", str(exc))
        refresh()

    def on_circuit():
        try:
            new_circuit_command()
        except OnionError as exc:
            QtWidgets.QMessageBox.warning(None, "OnionFruitux", str(exc))

    def on_show():
        window = window_holder.get("window")
        if window is None:
            window = MainWindow()
            window_holder["window"] = window
        window.show()
        window.raise_()
        window.activateWindow()

    toggle.triggered.connect(on_toggle)
    circuit.triggered.connect(on_circuit)
    show.triggered.connect(on_show)
    quit_action.triggered.connect(app.quit)
    tray.activated.connect(
        lambda reason: on_toggle()
        if reason == QtWidgets.QSystemTrayIcon.ActivationReason.Trigger
        else None
    )
    tray.setContextMenu(menu)
    tray.show()
    refresh()
    timer = QtCore.QTimer()
    timer.setInterval(2000)
    timer.timeout.connect(refresh)
    timer.start()
    # Keep the timer alive for the life of the process.
    app._onionfruitux_timer = timer  # type: ignore[attr-defined]
    return app.exec()
