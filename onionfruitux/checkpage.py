"""Open the Tor Project check page after a successful connection.

The caller is the desktop user. The pkexec child that changes the firewall
does not call this. Popen returns immediately, so the window is not blocked
on the browser.
"""

from __future__ import annotations

import subprocess

from onionfruitux.paths import CHECK_URL


def open_check_page() -> None:
    """Ask the desktop to open the check page. Failure is not fatal."""
    try:
        subprocess.Popen(
            ["xdg-open", CHECK_URL],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except OSError:
        return
