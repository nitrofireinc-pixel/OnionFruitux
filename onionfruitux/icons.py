"""Onion artwork for the window and the tray.

The desktop menu already uses Icon=onionfruitux. A running window is a
separate icon: GNOME shows a gear when the process is not tied to that
desktop entry. These helpers load the packaged onion PNGs and register
the desktop file name so the shell uses the same artwork.
"""

from __future__ import annotations

from pathlib import Path

_SIZES = (16, 32, 48, 64, 128, 256)


def onion_png_paths() -> list[Path]:
    """PNG files of the onion icon, largest set that actually exists."""
    roots = (
        Path("/usr/share/icons/hicolor"),
        Path(__file__).resolve().parents[1] / "share" / "icons" / "hicolor",
    )
    for root in roots:
        paths = [
            root / f"{size}x{size}" / "apps" / "onionfruitux.png"
            for size in _SIZES
            if (root / f"{size}x{size}" / "apps" / "onionfruitux.png").is_file()
        ]
        if paths:
            return paths
    bundled = Path(__file__).resolve().parent / "data" / "icons"
    return [bundled / f"{size}.png" for size in _SIZES if (bundled / f"{size}.png").is_file()]


def load_onion_icon(QtGui):
    """A QIcon built from decoded onion PNGs. Never a theme gear."""
    icon = QtGui.QIcon()
    for path in onion_png_paths():
        pixmap = QtGui.QPixmap(str(path))
        if not pixmap.isNull():
            icon.addPixmap(pixmap)
    if not icon.isNull():
        return icon
    themed = QtGui.QIcon.fromTheme("onionfruitux")
    sample = themed.pixmap(64, 64)
    if not sample.isNull():
        loaded = QtGui.QIcon()
        loaded.addPixmap(sample)
        return loaded
    return QtGui.QIcon(_painted_onion(QtGui, 64))


def install_app_icon(app, QtGui):
    """Tell the desktop and Qt to use the onion icon for this process."""
    setter = getattr(app, "setDesktopFileName", None)
    if setter is not None:
        setter("onionfruitux")
    icon = load_onion_icon(QtGui)
    app.setWindowIcon(icon)
    return icon


def _painted_onion(QtGui, size: int):
    """Last-resort onion drawn in code, used only when no PNG can be decoded."""
    from onionfruitux.qtutil import load_qt

    QtCore, _QtGui, _QtWidgets = load_qt()
    image = QtGui.QImage(size, size, QtGui.QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(QtCore.Qt.GlobalColor.transparent)
    painter = QtGui.QPainter(image)
    painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
    painter.scale(size / 64.0, size / 64.0)
    background = QtGui.QPainterPath()
    background.addRoundedRect(QtCore.QRectF(0, 0, 64, 64), 14, 14)
    painter.fillPath(background, QtGui.QColor("#241c38"))
    painter.setPen(QtCore.Qt.PenStyle.NoPen)
    painter.setBrush(QtGui.QColor("#7c5cbf"))
    painter.drawEllipse(QtCore.QRectF(14, 27, 32, 30))
    painter.setBrush(QtGui.QColor("#d9cff2"))
    painter.drawEllipse(QtCore.QRectF(22, 29, 16, 24))
    sprout = QtGui.QPainterPath()
    sprout.moveTo(30, 11)
    sprout.cubicTo(34, 17, 33, 22, 30, 26)
    sprout.cubicTo(27, 21, 26, 16, 30, 11)
    painter.fillPath(sprout, QtGui.QColor("#6aaa5a"))
    painter.end()
    return QtGui.QPixmap.fromImage(image)
