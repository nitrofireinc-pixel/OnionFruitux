"""Sanity check for a previous session that did not close cleanly.

On launch, and again just before the switch turns on, this looks for an
orphaned OnionFruitux Tor, leftover firewall or DNS changes from an older
version, a system proxy still pointing at us, and stale pid or state files.

If our Tor is still healthy, the session is reattached and the switch shows
On. Otherwise leftovers are cleaned up so turning the switch on can start
a new Tor instead of failing with a port-in-use error.
"""

from __future__ import annotations

import socket
from dataclasses import dataclass, field

from onionfruitux.errors import DirectUnreachable, OnionError
from onionfruitux.paths import (
    DNS_SAVE_PATH,
    PID_PATH,
    RESOLV_BACKUP_PATH,
    SOCKS_PORT,
    STATE_PATH,
    TABLE_NAME,
)
from onionfruitux.proxy import (
    apply_proxy,
    capture_snapshot,
    disable_session,
    parse_ignore_hosts,
    proxy_is_ours,
    snapshot_path,
    write_fallback_snapshot,
)

ATTACHED_NOTE = (
    "A previous Tor session was still running, so the switch was turned back on."
)
CLEARED_NOTE = "A leftover session was cleaned up. The switch is off."
LEFTOVER_NOTE = (
    "A leftover Tor is still running. Turning the switch on will replace it."
)


@dataclass
class SessionReport:
    tor_pids: list[int] = field(default_factory=list)
    socks_healthy: bool = False
    foreign_ports: list[str] = field(default_factory=list)
    nft_table: bool = False
    dns_snapshot: bool = False
    proxy_snapshot: bool = False
    proxy_ours: bool = False
    stale_pidfile: bool = False
    state_running: bool = False
    state_pid: int | None = None

    def our_tor_healthy(self) -> bool:
        return bool(self.tor_pids) and self.socks_healthy

    def needs_cleanup(self) -> bool:
        if self.our_tor_healthy():
            return False
        return bool(
            self.tor_pids
            or self.nft_table
            or self.dns_snapshot
            or self.proxy_snapshot
            or self.proxy_ours
            or self.stale_pidfile
            or self.state_running
        )

    def needs_root(self) -> bool:
        return bool(self.tor_pids or self.nft_table or self.dns_snapshot or self.stale_pidfile or self.state_running)


@dataclass
class RecoverResult:
    action: str
    on: bool
    note: str = ""

    @property
    def attached(self) -> bool:
        return self.action == "attached"


def inspect_session() -> SessionReport:
    """Read-only look at leftover Tor, ports, firewall, DNS, and proxy state."""
    from onionfruitux import system
    from onionfruitux.listeners import our_port_conflicts

    pids = system.list_our_tor()
    pidfile = _read_pidfile()
    stale_pidfile = bool(pidfile is not None and pidfile not in pids)
    state = _read_state()
    state_pid = state.get("pid") if isinstance(state.get("pid"), int) else None
    state_running = bool(state.get("running"))
    return SessionReport(
        tor_pids=pids,
        socks_healthy=socks_is_healthy() if pids else False,
        foreign_ports=our_port_conflicts(),
        nft_table=_nft_table_present(),
        dns_snapshot=DNS_SAVE_PATH.exists() or RESOLV_BACKUP_PATH.exists(),
        proxy_snapshot=snapshot_path().exists(),
        proxy_ours=proxy_is_ours(),
        stale_pidfile=stale_pidfile,
        state_running=state_running,
        state_pid=state_pid,
    )


def socks_is_healthy(port: int = SOCKS_PORT, timeout: float = 0.6) -> bool:
    """True when Tor's SOCKS port accepts a no-auth greeting.

    This never connects to the old TransPort or the DNS port.
    """
    try:
        sock = socket.create_connection(("127.0.0.1", port), timeout)
    except OSError:
        return False
    try:
        sock.settimeout(timeout)
        sock.sendall(b"\x05\x01\x00")
        hello = sock.recv(2)
        return len(hello) >= 2 and hello[0] == 5
    except OSError:
        return False
    finally:
        sock.close()


def recover_session(
    *,
    ignore_hosts: list[str] | None = None,
    privileged: bool = False,
    system: bool = False,
    error_file: str | None = None,
    escalate=None,
) -> RecoverResult:
    """Reattach a healthy leftover Tor, or clean up so a new connect can bind."""
    report = inspect_session()
    if report.our_tor_healthy():
        _reattach_proxy(ignore_hosts)
        return RecoverResult("attached", True, ATTACHED_NOTE)

    if not report.needs_cleanup() and not report.foreign_ports:
        return RecoverResult("idle", False, "")

    cleanup_error = ""
    if report.needs_root() and not privileged and not system:
        from onionfruitux.cli import escalate as default_escalate

        runner = escalate if escalate is not None else default_escalate
        code = runner(["recover"], error_file=error_file)
        if code != 0:
            from onionfruitux.cli import _escalate_failure

            cleanup_error = _escalate_failure(error_file)
    elif report.needs_root() or privileged or system:
        _cleanup_privileged()

    _cleanup_user_proxy()
    leftover = inspect_session()
    if leftover.our_tor_healthy():
        _reattach_proxy(ignore_hosts)
        return RecoverResult("attached", True, ATTACHED_NOTE)
    if leftover.tor_pids or leftover.needs_root():
        note = cleanup_error or LEFTOVER_NOTE
        return RecoverResult("leftover", False, note)
    if report.needs_cleanup() or leftover.proxy_snapshot or leftover.proxy_ours:
        return RecoverResult("cleared", False, CLEARED_NOTE)
    return RecoverResult("idle", False, "")


def recover_command(
    *,
    privileged: bool = False,
    system: bool = False,
    error_file: str | None = None,
) -> RecoverResult:
    """CLI entry. The privileged child only stops leftovers; the desktop restores the proxy."""
    if privileged or system or _is_root():
        report = inspect_session()
        if report.our_tor_healthy():
            return RecoverResult("attached", True, ATTACHED_NOTE)
        _cleanup_privileged()
        return RecoverResult("cleared", False, CLEARED_NOTE)
    return recover_session(privileged=False, system=False, error_file=error_file)


def _reattach_proxy(ignore_hosts: list[str] | None) -> None:
    from onionfruitux.config import load_config

    hosts = list(ignore_hosts) if ignore_hosts is not None else parse_ignore_hosts(
        load_config().network.ignore_hosts
    )
    if proxy_is_ours() and snapshot_path().exists():
        return
    if proxy_is_ours() and not snapshot_path().exists():
        # The original settings were lost. Quit should not leave the machine
        # on Tor, so the restore target is "no proxy".
        write_fallback_snapshot()
        return
    if not snapshot_path().exists():
        capture_snapshot()
    apply_proxy(SOCKS_PORT, hosts)


def _cleanup_privileged() -> None:
    from onionfruitux import system

    system.panic_off()


def _cleanup_user_proxy() -> None:
    if snapshot_path().exists():
        try:
            disable_session()
        except DirectUnreachable:
            # Leave the snapshot in place; the window can offer a reset.
            return
        except OnionError:
            return
        return
    if proxy_is_ours():
        from onionfruitux.proxy import reset_proxy_defaults

        try:
            reset_proxy_defaults()
        except OnionError:
            return


def _nft_table_present() -> bool:
    import shutil
    import subprocess

    if not shutil.which("nft"):
        return False
    try:
        result = subprocess.run(
            ["nft", "list", "table", "inet", TABLE_NAME],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0


def _read_pidfile() -> int | None:
    if not PID_PATH.exists():
        return None
    try:
        return int(PID_PATH.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return None


def _read_state() -> dict:
    if not STATE_PATH.exists():
        return {}
    try:
        import json

        data = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _is_root() -> bool:
    import os

    return os.geteuid() == 0

