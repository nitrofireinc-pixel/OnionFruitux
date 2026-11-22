"""Panel or dock icon with the same switch as the window."""

from __future__ import annotations

import os
import sys

from onionfruitux.cli import new_circuit_command, shutdown_session
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
from onionfruitux.proxy import switch_is_on
from onionfruitux.qtutil import load_qt
from onionfruitux.switchjob import SwitchJob

APP_NAME = "OnionFruiTux"

NO_TRAY_NOTE = (
    "This session has no panel tray. Closing the window turns OnionFruiTux off."
)
GNOME_TRAY_NOTE = (
    "GNOME needs the AppIndicator extension for a tray icon. "
    "Closing the window turns OnionFruiTux off."
)


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
        "off": "OnionFruiTux is off",
        "connecting": "OnionFruiTux is connecting",
        "disconnecting": "OnionFruiTux is turning off",
        "on": "OnionFruiTux is on",
    }.get(phase, APP_NAME)


def switch_label(running: bool) -> str:
    return "Disconnect" if running else "Connect"


def hide_label(visible: bool) -> str:
    return "Hide" if visible else "Show"


def desktop_is_gnome() -> bool:
    desktop = " ".join(
        os.environ.get(name, "")
        for name in ("XDG_CURRENT_DESKTOP", "XDG_SESSION_DESKTOP", "DESKTOP_SESSION")
    ).upper()
    return "GNOME" in desktop


def status_notifier_watcher() -> bool:
    """True when a StatusNotifier host (AppIndicator, KDE, etc.) is on the bus."""
    try:
        QtDBus = _load_qtdbus()
    except Exception:
        QtDBus = None
    if QtDBus is not None:
        bus = QtDBus.QDBusConnection.sessionBus()
        if bus.isConnected():
            iface = QtDBus.QDBusInterface(
                "org.kde.StatusNotifierWatcher",
                "/StatusNotifierWatcher",
                "org.kde.StatusNotifierWatcher",
                bus,
            )
            if iface.isValid():
                return True
    return _busctl_has_watcher()


def tray_is_usable(QtWidgets) -> tuple[bool, str]:
    """Whether a tray icon will actually appear, and a fallback note if not."""
    if not QtWidgets.QSystemTrayIcon.isSystemTrayAvailable():
        return False, NO_TRAY_NOTE
    if desktop_is_gnome() and not status_notifier_watcher():
        return False, GNOME_TRAY_NOTE
    return True, ""


def attach_tray(app, window=None):
    """Install the tray icon. Returns (tray, note). tray is None when unusable."""
    QtCore, QtGui, QtWidgets = load_qt()
    usable, reason = tray_is_usable(QtWidgets)
    if not usable:
        return None, reason
    icon = app.windowIcon()
    if icon.isNull():
        icon = install_app_icon(app, QtGui)
    tray = QtWidgets.QSystemTrayIcon(icon)
    tray.setObjectName("onionfruituxTray")
    tray.setToolTip(APP_NAME)
    menu = QtWidgets.QMenu()
    visibility = menu.addAction("Hide" if window is not None and window.isVisible() else "Show")
    toggle = menu.addAction(switch_label(False))
    circuit = menu.addAction("New circuit")
    menu.addSeparator()
    quit_action = menu.addAction("Quit")
    window_holder: dict[str, object] = {"window": window}
    state = {"busy": False, "phase": "off", "job": None}

    def current_window():
        existing = window_holder.get("window")
        if existing is not None:
            return existing
        opened = MainWindow()
        opened.restore_on_close = False
        opened._tray = tray
        window_holder["window"] = opened
        return opened

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
        opened = window_holder.get("window")
        if opened is not None:
            visibility.setText(hide_label(opened.isVisible()))

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
        opened = window_holder.get("window")
        if opened is not None and not opened._busy:
            opened.refresh()

    def on_failed(message: str):
        turning_on = bool(state["job"] and state["job"].turn_on)
        state["busy"] = False
        apply_phase("off" if turning_on else "on")
        present_switch_error(window_holder.get("window"), message)
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
            QtWidgets.QMessageBox.warning(None, APP_NAME, str(exc))

    def on_visibility():
        opened = current_window()
        if opened.isVisible():
            opened.hide()
        else:
            opened.show()
            opened.raise_()
            opened.activateWindow()
        visibility.setText(hide_label(opened.isVisible()))

    def on_quit():
        try:
            shutdown_session()
        except DirectUnreachable as exc:
            present_switch_error(None, RESET_MARKER + "\n" + str(exc))
        except OnionError as exc:
            present_switch_error(None, str(exc))
        app.quit()

    visibility.triggered.connect(on_visibility)
    toggle.triggered.connect(on_toggle)
    circuit.triggered.connect(on_circuit)
    quit_action.triggered.connect(on_quit)
    tray.activated.connect(
        lambda reason: on_visibility()
        if reason == QtWidgets.QSystemTrayIcon.ActivationReason.Trigger
        else None
    )
    tray.setContextMenu(menu)
    tray.show()
    if window is not None:
        window._tray = tray
        window.restore_on_close = False
    refresh()
    timer = QtCore.QTimer()
    timer.setInterval(2000)
    timer.timeout.connect(refresh)
    timer.start()
    app._onionfruitux_timer = timer  # type: ignore[attr-defined]
    app._onionfruitux_tray = tray  # type: ignore[attr-defined]
    return tray, ""


def run_tray() -> int:
    QtCore, QtGui, QtWidgets = load_qt()
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setQuitOnLastWindowClosed(False)
    _install_quit_restore()
    app.aboutToQuit.connect(_silent_quit_restore)
    install_app_icon(app, QtGui)
    tray, reason = attach_tray(app, None)
    if tray is None:
        print(reason, file=sys.stderr)
        window = MainWindow()
        window.show()
        if reason:
            window.status.setText(reason)
        return app.exec()
    return app.exec()


def _load_qtdbus():
    try:
        from PyQt6 import QtDBus

        return QtDBus
    except ImportError:
        pass
    from PySide6 import QtDBus

    return QtDBus


def _busctl_has_watcher() -> bool:
    import shutil
    import subprocess

    if not shutil.which("busctl"):
        return False
    try:
        result = subprocess.run(
            [
                "busctl",
                "--user",
                "status",
                "org.kde.StatusNotifierWatcher",
            ],
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0
