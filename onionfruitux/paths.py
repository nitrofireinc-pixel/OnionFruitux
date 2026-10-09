"""Filesystem locations shared by the window, the command line, and the installer."""

from __future__ import annotations

import os
from pathlib import Path

STATE_DIR = Path("/var/lib/onionfruitux")
TOR_DATA = STATE_DIR / "tor"
TORRC_PATH = STATE_DIR / "torrc"
COOKIE_PATH = TOR_DATA / "control.authcookie"
LOG_PATH = TOR_DATA / "notice.log"
STDERR_PATH = STATE_DIR / "tor.stderr"
PID_PATH = Path("/run/onionfruitux.pid")
STATE_PATH = STATE_DIR / "state.json"
BOOT_PATH = Path("/etc/onionfruitux/boot.json")
UNIT_PATH = Path("/usr/lib/systemd/system/onionfruitux.service")
UNIT_PATH_FALLBACK = Path("/lib/systemd/system/onionfruitux.service")

# OnionFruitux's own Tor. These avoid Ubuntu's tor service (9050/9051),
# Tor Browser (9150/9151), and mDNS (5353).
TRANS_PORT = 9140
DNS_PORT = 9153
SOCKS_PORT = 9155
CONTROL_PORT = 9156

# Ports other programs commonly bind. Doctor mentions them. We do not use them.
DISTRO_TOR_PORT = 9050

TOR_USER = "onionfruitux"
TABLE_NAME = "onionfruitux"
CHECK_URL = "https://check.torproject.org/"


def config_dir() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME")
    if base:
        return Path(base) / "onionfruitux"
    return Path.home() / ".config" / "onionfruitux"


def config_path() -> Path:
    return config_dir() / "config.json"


def error_path() -> Path:
    return config_dir() / "last-error"
