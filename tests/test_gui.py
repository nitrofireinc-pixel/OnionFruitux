"""The window renders and the switch stays off."""

from __future__ import annotations

import os
import tempfile
import unittest

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
