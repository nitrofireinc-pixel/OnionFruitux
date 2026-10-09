"""Look at this machine without changing its network."""

from __future__ import annotations

import pwd
import shutil
import subprocess
import tempfile
from dataclasses import dataclass

from onionfruitux import bridges
from onionfruitux.config import Config
from onionfruitux.firewall import render_firewall
from onionfruitux.paths import TOR_USER
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
    nft = shutil.which("nft")
    checks.append(Check("nft", bool(nft), nft or "install nftables"))
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
    checks.append(_nft_syntax_check(cfg, nft))
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


def _nft_syntax_check(cfg: Config, nft: str | None) -> Check:
    if not nft:
        return Check("firewall syntax", False, "nft is not installed", required=False)
    rules = render_firewall(cfg.network)
    with tempfile.NamedTemporaryFile("w", suffix=".nft", delete=False) as handle:
        handle.write(rules)
        name = handle.name
    try:
        result = subprocess.run(
            [nft, "-c", "-f", name],
            capture_output=True,
            text=True,
            check=False,
        )
    finally:
        try:
            import os

            os.unlink(name)
        except OSError:
            pass
    if result.returncode == 0:
        return Check("firewall syntax", True, "nft -c accepted the rules", required=False)
    detail = (result.stderr or result.stdout or "nft -c failed").strip()
    if "not permitted" in detail.lower() or "operation not permitted" in detail.lower():
        return Check(
            "firewall syntax",
            True,
            "nft -c needs permission on this machine; rules were not loaded",
            required=False,
        )
    lowered = detail.lower()
    if "no such file" in lowered or "could not process" in lowered and TOR_USER in detail:
        return Check("firewall syntax", False, detail, required=False)
    return Check("firewall syntax", False, detail, required=False)
