"""Look at this machine without changing its network."""

from __future__ import annotations

import pwd
import shutil
from dataclasses import dataclass

from onionfruitux import bridges
from onionfruitux.config import Config
from onionfruitux.listeners import distro_tor_listeners, our_port_conflicts
from onionfruitux.paths import (
    CONTROL_PORT,
    DISTRO_TOR_PORT,
    SOCKS_PORT,
    TOR_USER,
)
from onionfruitux.torrc import geoip_paths


@dataclass
class Check:
    name: str
    ok: bool
    detail: str
    required: bool = True


def collect_checks(cfg: Config) -> list[Check]:
    checks: list[Check] = []
    tor = shutil.which("tor")
    checks.append(Check("tor", bool(tor), tor or "install the tor package"))
    checks.append(
        Check(
            "proxy",
            True,
            "system SOCKS proxy; firewall rules are not installed",
            required=False,
        )
    )
    try:
        user = pwd.getpwnam(TOR_USER)
        checks.append(Check("user onionfruitux", True, f"uid {user.pw_uid}"))
    except KeyError:
        checks.append(
            Check(
                "user onionfruitux",
                False,
                "run sudo ./install.sh",
            )
        )
    ipv4, ipv6 = geoip_paths()
    route = cfg.current_route()
    needs_geo = bool(route.entry or route.exit)
    if ipv4:
        detail = str(ipv4)
        if ipv6 is None:
            detail += " (geoip6 not found)"
        checks.append(Check("geoip", True, detail, required=needs_geo or True))
    else:
        checks.append(
            Check(
                "geoip",
                False,
                "install the tor package to get /usr/share/tor/geoip",
                required=needs_geo,
            )
        )
    if route.bridge:
        lines = bridges.lines_for_route(cfg, route)
        checks.append(
            Check(
                f"bridge lines ({route.bridge})",
                bool(lines),
                f"{len(lines)} line(s)" if lines else "paste bridge lines in Settings",
            )
        )
        if route.bridge != "plain":
            exe = bridges.transport_executable(route.bridge)
            checks.append(
                Check(
                    f"transport ({route.bridge})",
                    bool(exe),
                    exe or f"install {bridges.preferred_executable(route.bridge)}",
                )
            )
    for kind, names in (
        ("lyrebird", ("lyrebird",)),
        ("obfs4proxy", ("obfs4proxy",)),
        ("snowflake-client", ("snowflake-client",)),
        ("conjure-client", ("conjure-client",)),
    ):
        found = next((shutil.which(name) for name in names if shutil.which(name)), None)
        checks.append(
            Check(kind, bool(found), found or "not installed", required=False)
        )
    pkexec = shutil.which("pkexec")
    checks.append(
        Check("pkexec", bool(pkexec), pkexec or "install polkit", required=False)
    )
    checks.append(_qt_check())
    checks.extend(_port_checks())
    return checks


def format_report(checks: list[Check]) -> str:
    lines = []
    for check in checks:
        state = "ok" if check.ok else "missing"
        lines.append(f"{check.name}: {state} ({check.detail})")
    lines.append("Doctor does not change the network or start Tor.")
    return "\n".join(lines) + "\n"


def doctor_status(checks: list[Check]) -> int:
    if all(check.ok or not check.required for check in checks):
        return 0
    return 1


def _port_checks() -> list[Check]:
    checks = []
    conflicts = our_port_conflicts()
    if conflicts:
        for message in conflicts:
            checks.append(Check("ports", False, message))
    else:
        checks.append(
            Check(
                "ports",
                True,
                f"{SOCKS_PORT} and {CONTROL_PORT} are free",
            )
        )
    holders = distro_tor_listeners()
    if holders:
        who = "; ".join(holders)
        checks.append(
            Check(
                "system Tor",
                True,
                (
                    f"another Tor is listening on 127.0.0.1:{DISTRO_TOR_PORT} ({who}). "
                    "That is usually Ubuntu's tor.service. OnionFruitux uses its own "
                    "ports and does not stop that service. To stop it yourself: "
                    "sudo systemctl disable --now tor.service tor@default.service"
                ),
                required=False,
            )
        )
    else:
        checks.append(
            Check(
                "system Tor",
                True,
                f"nothing is listening on {DISTRO_TOR_PORT}",
                required=False,
            )
        )
    return checks


def _qt_check() -> Check:
    try:
        import PyQt6  # noqa: F401

        return Check("window", True, "PyQt6", required=False)
    except ImportError:
        pass
    try:
        import PySide6  # noqa: F401

        return Check("window", True, "PySide6", required=False)
    except ImportError:
        return Check(
            "window",
            False,
            "install python3-pyqt6 or python3-pyside6",
            required=False,
        )
