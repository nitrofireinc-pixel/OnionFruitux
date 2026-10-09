"""The window renders and the switch stays off unless a test drives it."""

from __future__ import annotations

import os
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from onionfruitux.errors import OnionError
from onionfruitux.qtutil import load_qt


class GuiTests(unittest.TestCase):
    def setUp(self):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        self.tmp = tempfile.TemporaryDirectory()
        self._old = os.environ.get("XDG_CONFIG_HOME")
        os.environ["XDG_CONFIG_HOME"] = self.tmp.name
        QtCore, QtGui, QtWidgets = load_qt()
        self.QtWidgets = QtWidgets
        self.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def tearDown(self):
        if self._old is None:
            os.environ.pop("XDG_CONFIG_HOME", None)
        else:
            os.environ["XDG_CONFIG_HOME"] = self._old
        self.tmp.cleanup()

    def test_window_starts_off_and_settings_use_the_spec_labels(self):
        from onionfruitux.gui import MainWindow, create_main_window
        from onionfruitux.tray import switch_label

        window = MainWindow()
        window.show()
        self.app.processEvents()
        self.assertFalse(window.switch.isChecked())
        self.assertIn("normal network", window.status.text())
        self.assertFalse(window.circuit.isEnabled())
        self.assertEqual(switch_label(False), "Turn on")
        self.assertEqual(switch_label(True), "Turn off")

        _window_cls, settings_cls, route_cls = create_main_window()
        settings = settings_cls()
        settings.show()
        self.app.processEvents()
        check = settings.findChild(self.QtWidgets.QCheckBox, "openCheckPage")
        self.assertIsNotNone(check)
        self.assertEqual(check.text(), "Open the Tor check page when the switch changes")
        self.assertTrue(check.isChecked())
        self.assertEqual(
            settings.findChild(self.QtWidgets.QCheckBox, "lanDirect").text(),
            "Keep the local network off Tor",
        )
        self.assertEqual(
            settings.findChild(self.QtWidgets.QCheckBox, "rejectNonTor").text(),
            "Block traffic that cannot use Tor",
        )
        new_route = settings.findChild(self.QtWidgets.QPushButton, "newRouteButton")
        self.assertEqual(new_route.text(), "New route")
        route = route_cls(None)
        self.assertEqual(route.strict.text(), "Only use the countries I picked")
        self.assertFalse(window.switch.isChecked())

        shot = os.environ.get("ONIONFRUITUX_SCREENSHOT")
        if shot:
            window.grab().save(shot)
            settings.grab().save(os.path.splitext(shot)[0] + "-settings.png")

    def _track_color(self, switch, x: int):
        image = switch.grab().toImage()
        self.assertFalse(image.isNull())
        return image.pixelColor(x, 22)

    def test_switch_turns_orange_on_a_worker_then_green(self):
        from onionfruitux import cli
        from onionfruitux.gui import PHASE_COLOR, MainWindow

        seen = {}

        def fake_connect(**kwargs):
            seen["thread"] = threading.get_ident()
            seen["open_page"] = kwargs.get("open_page")
            progress = kwargs.get("progress")
            if progress is not None:
                progress("Starting Tor…")
            time.sleep(0.35)
            if progress is not None:
                progress("Tor bootstrap 40%…")
            time.sleep(0.25)

        window = MainWindow()
        window._timer.stop()
        window.show()
        self.app.processEvents()
        self.assertEqual(window.switch.phase, "off")
        gray = self._track_color(window.switch, 70)
        self.assertLess(gray.red(), 80)

        with patch.object(cli, "connect_command", side_effect=fake_connect), \
             patch("onionfruitux.gui.open_check_page") as page:
            window.switch.click()
            self.app.processEvents()
            self.assertTrue(window._busy)
            self.assertEqual(window.switch.phase, "connecting")
            self.assertEqual(PHASE_COLOR["connecting"], "#e08a1e")
            self.assertEqual(window.switch_label.text(), "Connecting")
            orange = self._track_color(window.switch, 30)
            self.assertGreater(orange.red(), 180)
            self.assertGreater(orange.red(), orange.green())
            window.refresh()
            self.assertEqual(window.switch.phase, "connecting")
            self.assertTrue(window.switch.isChecked())
            page.assert_not_called()

            deadline = time.time() + 3
            while time.time() < deadline and window.status.text() != "Tor bootstrap 40%…":
                self.app.processEvents()
                time.sleep(0.02)
            self.assertEqual(window.status.text(), "Tor bootstrap 40%…")
            self.assertIn("thread", seen)
            self.assertNotEqual(seen["thread"], threading.get_ident())
            self.assertIs(seen["open_page"], False)
            page.assert_not_called()

            while time.time() < deadline and window.switch.phase != "on":
                self.app.processEvents()
                time.sleep(0.02)
            self.assertEqual(window.switch.phase, "on")
            self.assertIn("through Tor", window.status.text())
            green = self._track_color(window.switch, 30)
            self.assertGreater(green.green(), green.red())
            page.assert_called_once()
            window._job.wait(2000)

    def test_failed_connect_slides_off_and_skips_the_check_page(self):
        from onionfruitux import cli
        from onionfruitux.gui import MainWindow

        def fake_connect(**kwargs):
            time.sleep(0.15)
            raise OnionError("Tor did not finish connecting before the time limit")

        window = MainWindow()
        window._timer.stop()
        window.show()
        self.app.processEvents()
        with patch.object(cli, "connect_command", side_effect=fake_connect), \
             patch("onionfruitux.gui.open_check_page") as page, \
             patch.object(self.QtWidgets.QMessageBox, "warning") as box:
            window.switch.click()
            deadline = time.time() + 3
            while time.time() < deadline and window._busy:
                self.app.processEvents()
                time.sleep(0.02)
            self.assertFalse(window._busy)
            self.assertFalse(window.switch.isChecked())
            self.assertEqual(window.switch.phase, "off")
            self.assertIn("normal network", window.status.text())
            page.assert_not_called()
            self.assertIn("time limit", box.call_args.args[2])
            window._job.wait(2000)

    def test_tray_icon_uses_the_same_three_colors(self):
        from onionfruitux.gui import PHASE_COLOR
        from onionfruitux.tray import icon_for_phase, tray_tip

        QtCore, QtGui, _QtWidgets = load_qt()
        base = QtGui.QIcon()
        samples = {}
        for phase in ("off", "connecting", "on"):
            icon = icon_for_phase(base, phase, QtGui, QtCore)
            image = icon.pixmap(64, 64).toImage()
            self.assertFalse(image.isNull())
            samples[phase] = image.pixelColor(32, 32)
            self.assertTrue(tray_tip(phase).startswith("OnionFruitux is "))
        self.assertGreater(samples["connecting"].red(), samples["off"].red())
        self.assertGreater(samples["on"].green(), samples["on"].red())
        self.assertEqual(PHASE_COLOR["on"], "#3dba7a")
        self.assertEqual(PHASE_COLOR["connecting"], "#e08a1e")

    def test_tray_tints_the_installed_artwork(self):
        from onionfruitux.tray import icon_for_phase

        QtCore, QtGui, _QtWidgets = load_qt()
        base = QtGui.QIcon("share/icons/hicolor/64x64/apps/onionfruitux.png")
        self.assertFalse(base.isNull())
        colors = {}
        for phase in ("off", "connecting", "on"):
            icon = icon_for_phase(base, phase, QtGui, QtCore)
            colors[phase] = icon.pixmap(64, 64).toImage().pixelColor(32, 32)
        self.assertGreater(colors["connecting"].red(), colors["off"].red())
        self.assertGreater(colors["on"].green(), colors["connecting"].green())
