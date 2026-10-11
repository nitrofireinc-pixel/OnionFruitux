"""See who is listening on a port. This does not change the network."""

from __future__ import annotations

import os
import pwd
import shutil
import subprocess
from pathlib import Path

from onionfruitux.paths import (
    CONTROL_PORT,
    DNS_PORT,
    DISTRO_TOR_PORT,
    SOCKS_PORT,
    TORRC_PATH,
    TRANS_PORT,
)

# name, port. The firewall redirects into the first two. All four must be free.
APP_PORTS = (
    ("transparent proxy", TRANS_PORT),
    ("DNS", DNS_PORT),
    ("SOCKS", SOCKS_PORT),
    ("control", CONTROL_PORT),
)


def our_port_conflicts() -> list[str]:
    """Ports OnionFruitux needs that some other process already holds."""
    messages = []
    owners = _socket_owners()
    for name, port in APP_PORTS:
        holders = describe_listeners(port, owners=owners, ignore_our_tor=True)
        if not holders:
            continue
        who = "; ".join(holders)
        messages.append(
            f"OnionFruitux's {name} port {port} is already in use by {who}. "
            "The firewall was not changed."
        )
    return messages


def distro_tor_listeners() -> list[str]:
    """Who is listening on the port Ubuntu's Tor package uses."""
    return describe_listeners(DISTRO_TOR_PORT, ignore_our_tor=False)


def describe_listeners(
    port: int,
    owners: dict[str, tuple[int, str]] | None = None,
    ignore_our_tor: bool = True,
) -> list[str]:
    """Human descriptions of processes bound to ``port``."""
    if owners is None:
        owners = _socket_owners()
    found: list[str] = []
    seen_pid: set[int] = set()
    seen_user: set[str] = set()
    for inode, uid in _bound_sockets(port):
        owner = owners.get(inode)
        if owner is not None:
            pid, comm = owner
            if ignore_our_tor and _is_our_tor(pid):
                continue
            if pid in seen_pid:
                continue
            seen_pid.add(pid)
            found.append(f"{comm} (pid {pid}) running as {_user_name(uid)}")
            continue
        user = _user_name(uid)
        if user not in seen_user:
            seen_user.add(user)
    # ss can name a process when /proc fd lookup only knows the user id.
    if not found:
        for pid, comm in _ss_listeners(port):
            if ignore_our_tor and _is_our_tor(pid):
                continue
            if pid in seen_pid:
                continue
            seen_pid.add(pid)
            found.append(f"{comm} (pid {pid})")
    if found:
        return found
    return [f"user {user}" for user in seen_user]


def parse_proc_net(text: str, port: int, *, tcp: bool) -> list[tuple[str, int]]:
    """Return (inode, uid) rows in ``/proc/net/tcp`` or ``udp`` text bound to ``port``."""
    rows: list[tuple[str, int]] = []
    for line in text.splitlines():
        fields = line.split()
        if len(fields) < 10 or not fields[0].endswith(":"):
            continue
        local = fields[1]
        remote = fields[2]
        state = fields[3]
        try:
            local_port = int(local.rsplit(":", 1)[1], 16)
            remote_port = int(remote.rsplit(":", 1)[1], 16)
            uid = int(fields[7])
        except (ValueError, IndexError):
            continue
        if local_port != port or remote_port != 0:
            continue
        # 0A is TCP LISTEN. UDP sockets show as bound with a zero remote port.
        if tcp and state != "0A":
            continue
        rows.append((fields[9], uid))
    return rows


def _bound_sockets(port: int) -> list[tuple[str, int]]:
    rows: list[tuple[str, int]] = []
    for name, tcp in (
        ("tcp", True),
        ("tcp6", True),
        ("udp", False),
        ("udp6", False),
    ):
        path = Path("/proc/net") / name
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        rows.extend(parse_proc_net(text, port, tcp=tcp))
    return rows


def _socket_owners() -> dict[str, tuple[int, str]]:
    """Map a socket inode to the process that holds it, when fds are readable."""
    owners: dict[str, tuple[int, str]] = {}
    try:
        entries = list(Path("/proc").iterdir())
    except OSError:
        return owners
    for entry in entries:
        if not entry.name.isdigit():
            continue
        fd_dir = entry / "fd"
        try:
            fds = list(fd_dir.iterdir())
        except OSError:
            continue
        try:
            comm = (entry / "comm").read_text(encoding="utf-8", errors="replace").strip()
        except OSError:
            comm = "process"
        pid = int(entry.name)
        for fd in fds:
            try:
                target = os.readlink(fd)
            except OSError:
                continue
            if target.startswith("socket:[") and target.endswith("]"):
                owners.setdefault(target[8:-1], (pid, comm or "process"))
    return owners


def _ss_listeners(port: int) -> list[tuple[int, str]]:
    if not shutil.which("ss"):
        return []
    try:
        result = subprocess.run(
            ["ss", "-H", "-lptun", f"sport = :{port}"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    import re

    found = []
    seen: set[int] = set()
    for match in re.finditer(r'\("([^"]+)",pid=(\d+)', result.stdout):
        pid = int(match.group(2))
        if pid in seen:
            continue
        seen.add(pid)
        found.append((pid, match.group(1)))
    return found


def _user_name(uid: int) -> str:
    try:
        return pwd.getpwuid(uid).pw_name
    except KeyError:
        return str(uid)


def _is_our_tor(pid: int) -> bool:
    try:
        raw = Path(f"/proc/{pid}/cmdline").read_bytes()
    except OSError:
        return False
    cmdline = raw.replace(b"\x00", b" ").decode(errors="replace")
    if not cmdline:
        return False
    executable = cmdline.split(" ", 1)[0]
    if executable.rsplit("/", 1)[-1] != "tor":
        return False
    return str(TORRC_PATH) in cmdline
