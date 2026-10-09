"""Open the Tor Project check page after the firewall has changed."""

from __future__ import annotations

import subprocess

from onionfruitux.paths import CHECK_URL


def open_check_page() -> None:
    """Ask the desktop to open the check page. Failure is not fatal."""
    try:
        subprocess.Popen(
            ["xdg-open", CHECK_URL],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except OSError:
        return
