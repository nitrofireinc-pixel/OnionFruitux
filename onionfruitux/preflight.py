"""Decide whether the switch is allowed to move. Does not move it."""

from __future__ import annotations

import pwd
import shutil

from onionfruitux import bridges
from onionfruitux.config import Config, parse_relay_ports
from onionfruitux.paths import TOR_USER
from onionfruitux.torrc import geoip_paths


def validate_for_connect(cfg: Config) -> list[str]:
    errors: list[str] = []
    if not shutil.which("tor"):
        errors.append("tor is not installed")
    if not shutil.which("nft"):
        errors.append("nftables is not installed")
    try:
        pwd.getpwnam(TOR_USER)
    except KeyError:
        errors.append("system user onionfruitux does not exist; run sudo ./install.sh")
    route = cfg.current_route()
    if (route.entry or route.exit) and geoip_paths()[0] is None:
        errors.append("Tor's GeoIP database is missing; install the tor package")
    if route.bridge:
        lines = bridges.lines_for_route(cfg, route)
        if not lines:
            errors.append(
                f"the {route.bridge} route has no bridge lines; paste some in Settings"
            )
        if route.bridge != "plain" and bridges.transport_executable(route.bridge) is None:
            errors.append(
                f"the {route.bridge} transport is not installed "
                f"({bridges.preferred_executable(route.bridge)})"
            )
    try:
        parse_relay_ports(cfg.network.relay_ports)
    except ValueError as exc:
        errors.append(str(exc))
    return errors
