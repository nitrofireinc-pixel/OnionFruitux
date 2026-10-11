"""Run the switch off the Qt thread so the window can keep painting."""

from __future__ import annotations

from onionfruitux.errors import OnionError
from onionfruitux.qtutil import load_qt


def _signal(QtCore):
    factory = getattr(QtCore, "pyqtSignal", None) or QtCore.Signal
    return factory


def create_switch_job():
    QtCore, _QtGui, _QtWidgets = load_qt()
    signal = _signal(QtCore)

    class SwitchJob(QtCore.QThread):
        """Connect or disconnect without freezing the window.

        Connect asks the command not to open the check page. The window opens
        that page itself after this job reports success, still as the desktop
        user and still without waiting for the browser.
        """

        progress = signal(str)
        succeeded = signal()
        failed = signal(str)

        def __init__(self, turn_on: bool):
            super().__init__()
            self.turn_on = turn_on

        def run(self) -> None:
            from onionfruitux.cli import connect_command, disconnect_command

            try:
                if self.turn_on:
                    connect_command(
                        progress=self.progress.emit,
                        open_page=False,
                    )
                else:
                    disconnect_command()
            except OnionError as exc:
                self.failed.emit(str(exc))
                return
            except Exception as exc:
                self.failed.emit(str(exc))
                return
            self.succeeded.emit()

    return SwitchJob


_SWITCH_JOB = None


def SwitchJob(turn_on: bool):  # noqa: N802
    """Build a job. The class is created on first use so Qt is already loaded."""
    global _SWITCH_JOB
    if _SWITCH_JOB is None:
        _SWITCH_JOB = create_switch_job()
    return _SWITCH_JOB(turn_on)
