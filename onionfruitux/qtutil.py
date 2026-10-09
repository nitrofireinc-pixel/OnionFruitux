"""Import PyQt6 or PySide6. The window uses the Qt widgets API both provide."""

from __future__ import annotations


def load_qt():
    try:
        from PyQt6 import QtCore, QtGui, QtWidgets

        return QtCore, QtGui, QtWidgets
    except ImportError:
        from PySide6 import QtCore, QtGui, QtWidgets

        return QtCore, QtGui, QtWidgets
