"""Panel or dock icon with the same switch as the window."""

from __future__ import annotations

import sys

from onionfruitux.cli import new_circuit_command
from onionfruitux.errors import RESET_MARKER, DirectUnreachable, OnionError
from onionfruitux.gui import (
    PHASE_COLOR,
    MainWindow,
    _install_quit_restore,
    _load_icon,
    _silent_quit_restore,
    present_switch_error,
)
from onionfruitux.icons import install_app_icon
from onionfruitux.proxy import quit_restore, switch_is_on
from onionfruitux.qtutil import load_qt
from onionfruitux.switchjob import SwitchJob


def icon_for_phase(base, phase: str, QtGui, QtCore):
    """Tint the tray icon to the same gray, orange, or green as the switch."""
    color = QtGui.QColor(PHASE_COLOR.get(phase, PHASE_COLOR["off"]))
    source = base.pixmap(64, 64)
    if source.isNull():
        source = _load_icon(QtGui).pixmap(64, 64)
    if source.isNull():
        pixmap = QtGui.QPixmap(64, 64)
        pixmap.fill(QtCore.Qt.GlobalColor.transparent)
        painter = QtGui.QPainter(pixmap)
        painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
        painter.setBrush(color)
        painter.setPen(QtCore.Qt.PenStyle.NoPen)
        painter.drawEllipse(pixmap.rect().adjusted(8, 14, -8, -6))
        painter.end()
        return QtGui.QIcon(pixmap)
    tinted = QtGui.QPixmap(source.size())
    tinted.fill(QtCore.Qt.GlobalColor.transparent)
    painter = QtGui.QPainter(tinted)
    painter.drawPixmap(0, 0, source)
    painter.setCompositionMode(QtGui.QPainter.CompositionMode.CompositionMode_SourceIn)
    painter.fillRect(tinted.rect(), color)
    painter.end()
    return QtGui.QIcon(tinted)


def tray_tip(phase: str) -> str:
    return {
        "off": "OnionFruitux is off",
        "connecting": "OnionFruitux is connecting",
        "disconnecting": "OnionFruitux is turning off",
        "on": "OnionFruitux is on",
    }.get(phase, "OnionFruitux")


def switch_label(running: bool) -> str:
    return "Turn off" if running else "Turn on"


def run_tray() -> int:
    QtCore, QtGui, QtWidgets = load_qt()
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)
    app.setApplicationName("OnionFruitux")
    app.setQuitOnLastWindowClosed(False)
    _install_quit_restore()
    app.aboutToQuit.connect(_silent_quit_restore)
    icon = install_app_icon(app, QtGui)
    if not QtWidgets.QSystemTrayIcon.isSystemTrayAvailable():
        print("This session has no panel tray. Opening the window.", file=sys.stderr)
        window = MainWindow()
        window.show()
        return app.exec()
    tray = QtWidgets.QSystemTrayIcon(icon)
    tray.setToolTip("OnionFruitux")
    menu = QtWidgets.QMenu()
    toggle = menu.addAction(switch_label(False))
    circuit = menu.addAction("New circuit")
    show = menu.addAction("Show OnionFruitux")
    menu.addSeparator()
    quit_action = menu.addAction("Quit")
    window_holder: dict[str, object] = {}
    state = {"busy": False, "phase": "off", "job": None}

    def apply_phase(phase: str):
        state["phase"] = phase
        tray.setIcon(icon_for_phase(icon, phase, QtGui, QtCore))
        tray.setToolTip(tray_tip(phase))
        running = phase == "on"
        if state["busy"] and phase == "connecting":
            toggle.setText("Connecting…")
        elif state["busy"] and phase == "disconnecting":
            toggle.setText("Disconnecting…")
        else:
            toggle.setText(switch_label(running))
        circuit.setEnabled(running and not state["busy"])

    def refresh():
        if state["busy"]:
            return
        running = switch_is_on()
        apply_phase("on" if running else "off")

    def on_progress(message: str):
        tray.setToolTip(message)

    def on_succeeded():
        state["busy"] = False
        apply_phase("on" if state["job"] and state["job"].turn_on else "off")

    def on_failed(message: str):
        turning_on = bool(state["job"] and state["job"].turn_on)
        state["busy"] = False
        apply_phase("off" if turning_on else "on")
        present_switch_error(None, message)
        if message.startswith(RESET_MARKER):
            refresh()

    def on_toggle():
        if state["busy"]:
            return
        running = state["phase"] == "on"
        state["busy"] = True
        apply_phase("disconnecting" if running else "connecting")
        job = SwitchJob(not running)
        job.progress.connect(on_progress)
        job.succeeded.connect(on_succeeded)
        job.failed.connect(on_failed)
        state["job"] = job
        job.start()

    def on_circuit():
        try:
            new_circuit_command()
        except OnionError as exc:
            QtWidgets.QMessageBox.warning(None, "OnionFruitux", str(exc))

    def on_show():
        window = window_holder.get("window")
        if window is None:
            window = MainWindow()
            window.restore_on_close = False
            window_holder["window"] = window
        window.show()
        window.raise_()
        window.activateWindow()

    toggle.triggered.connect(on_toggle)
    circuit.triggered.connect(on_circuit)
    show.triggered.connect(on_show)
    def on_quit():
        try:
            quit_restore()
        except DirectUnreachable as exc:
            present_switch_error(None, RESET_MARKER + "\n" + str(exc))
        app.quit()

    quit_action.triggered.connect(on_quit)
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
