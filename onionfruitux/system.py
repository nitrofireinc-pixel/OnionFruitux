"""Privileged Tor and firewall changes.

connect() and disconnect() are the switch. They are not called by plan,
doctor, or the tests. Turning the switch off deletes only the onionfruitux
table and stops the Tor process this program started.
"""

from __future__ import annotations

import json
import os
import pwd
import signal
import socket
import subprocess
import time
from pathlib import Path

from onionfruitux.config import Config
from onionfruitux.errors import OnionError
from onionfruitux.firewall import render_firewall
from onionfruitux.paths import (
    BOOT_PATH,
    COOKIE_PATH,
    CONTROL_PORT,
    LOG_PATH,
    PID_PATH,
    STATE_DIR,
    STATE_PATH,
    TABLE_NAME,
    TOR_DATA,
    TOR_USER,
    TORRC_PATH,
    TRANS_PORT,
    UNIT_PATH,
    UNIT_PATH_FALLBACK,
)
from onionfruitux.torrc import render_torrc

UNIT_TEXT = """[Unit]
Description=OnionFruitux whole-computer Tor routing
Documentation=man:onionfruitux(1)
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=/usr/bin/onionfruitux --system connect
ExecStop=/usr/bin/onionfruitux --system disconnect

[Install]
WantedBy=multi-user.target
"""


def connect(cfg: Config) -> None:
    """Start OnionFruitux's Tor, then install the onionfruitux firewall table."""
    if os.geteuid() != 0:
        raise OnionError("connecting needs an administrator password")
    _prepare_runtime()
    torrc = render_torrc(cfg)
    rules = render_firewall(cfg.network)
    _assert_only_our_table(rules)
    TORRC_PATH.write_text(torrc, encoding="utf-8")
    os.chmod(TORRC_PATH, 0o640)
    try:
        user = pwd.getpwnam(TOR_USER)
    except KeyError as exc:
        raise OnionError("system user onionfruitux does not exist") from exc
    os.chown(TORRC_PATH, 0, user.pw_gid)
    # Replace a previous OnionFruitux Tor before the new one binds the ports.
    # The table is installed only after the new process is listening, so a
    # failed start does not leave the computer with nowhere to send traffic.
    stop_tor()
    try:
        proc = subprocess.Popen(
            ["tor", "-f", str(TORRC_PATH)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except FileNotFoundError as exc:
        raise OnionError("tor is not installed") from exc
    PID_PATH.write_text(f"{proc.pid}\n", encoding="utf-8")
    try:
        if not _wait_port(TRANS_PORT, 30):
            raise OnionError("Tor did not open its transparent proxy port")
        apply_ruleset(rules)
        _write_state(True, proc.pid, cfg.current_route().name)
        _wait_bootstrap(20)
    except Exception:
        delete_table()
        stop_tor()
        _write_state(False, None, "")
        raise


def disconnect() -> None:
    """Delete the onionfruitux table, then stop only our Tor process."""
    if os.geteuid() != 0:
        raise OnionError("disconnecting needs an administrator password")
    delete_table()
    stop_tor()
    _write_state(False, None, "")


def new_circuit() -> None:
    if os.geteuid() != 0:
        raise OnionError("a new circuit needs an administrator password")
    status = read_status()
    if not status["running"]:
        raise OnionError("OnionFruitux is off")
    _signal_newnym()


def apply_ruleset(rules: str) -> None:
    """Replace the onionfruitux table with rules OnionFruitux generated."""
    _assert_only_our_table(rules)
    delete_table()
    result = subprocess.run(
        ["nft", "-f", "-"],
        input=rules,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "nft failed").strip()
        raise OnionError(detail)


def delete_table() -> None:
    subprocess.run(
        ["nft", "delete", "table", "inet", TABLE_NAME],
        capture_output=True,
        text=True,
        check=False,
    )


def stop_tor() -> None:
    pid = _read_pid()
    if pid and _is_our_tor(pid):
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            pid = None
        if pid:
            for _ in range(50):
                if not _alive(pid):
                    break
                time.sleep(0.1)
            else:
                if _is_our_tor(pid):
                    try:
                        os.kill(pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
    if PID_PATH.exists():
        try:
            PID_PATH.unlink()
        except OSError:
            pass


def read_status() -> dict:
    running = False
    route = ""
    pid = _read_pid()
    data: dict = {}
    if STATE_PATH.exists():
        try:
            data = json.loads(STATE_PATH.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            data = {}
    if pid is None and isinstance(data.get("pid"), int):
        pid = data["pid"]
    route = str(data.get("route") or "")
    if data.get("running") and pid and _alive(pid) and _is_our_tor(pid):
        running = True
    return {"running": running, "route": route if running else "", "pid": pid if running else None}


def enable_boot(cfg: Config) -> None:
    """Save the current settings and enable routing at startup. Does not connect."""
    if os.geteuid() != 0:
        raise OnionError("enabling boot needs an administrator password")
    BOOT_PATH.parent.mkdir(parents=True, exist_ok=True)
    from onionfruitux.config import save_config

    save_config(cfg, BOOT_PATH)
    os.chmod(BOOT_PATH, 0o644)
    unit = _unit_destination()
    unit.parent.mkdir(parents=True, exist_ok=True)
    unit.write_text(UNIT_TEXT, encoding="utf-8")
    os.chmod(unit, 0o644)
    _systemctl("daemon-reload")
    _systemctl("enable", "onionfruitux.service")


def disable_boot() -> None:
    """Turn off routing at startup. Does not change a switch that is already on."""
    if os.geteuid() != 0:
        raise OnionError("disabling boot needs an administrator password")
    _systemctl("disable", "onionfruitux.service")
    if BOOT_PATH.exists():
        BOOT_PATH.unlink()


def _systemctl(*args: str) -> None:
    result = subprocess.run(
        ["systemctl", *args],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "systemctl failed").strip()
        raise OnionError(detail)


def _unit_destination() -> Path:
    if UNIT_PATH.parent.is_dir():
        return UNIT_PATH
    if UNIT_PATH_FALLBACK.parent.is_dir():
        return UNIT_PATH_FALLBACK
    return Path("/etc/systemd/system/onionfruitux.service")


def _prepare_runtime() -> None:
    try:
        user = pwd.getpwnam(TOR_USER)
    except KeyError as exc:
        raise OnionError(
            "system user onionfruitux does not exist; run sudo ./install.sh"
        ) from exc
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    os.chmod(STATE_DIR, 0o755)
    TOR_DATA.mkdir(parents=True, exist_ok=True)
    os.chown(TOR_DATA, user.pw_uid, user.pw_gid)
    os.chmod(TOR_DATA, 0o700)
    LOG_PATH.touch(exist_ok=True)
    os.chown(LOG_PATH, user.pw_uid, user.pw_gid)
    os.chmod(LOG_PATH, 0o640)
    PID_PATH.parent.mkdir(parents=True, exist_ok=True)


def _assert_only_our_table(rules: str) -> None:
    import re

    names = re.findall(r"(?m)^table\s+\S+\s+(\S+)", rules)
    if names != [TABLE_NAME]:
        raise OnionError("refusing to load a firewall that is not the onionfruitux table")
    if "flush ruleset" in rules:
        raise OnionError("refusing to flush the firewall")


def _write_state(running: bool, pid: int | None, route: str) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    payload = {"running": running, "pid": pid, "route": route}
    STATE_PATH.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    os.chmod(STATE_PATH, 0o644)


def _read_pid() -> int | None:
    if not PID_PATH.exists():
        return None
    try:
        text = PID_PATH.read_text(encoding="utf-8").strip()
        return int(text)
    except (OSError, ValueError):
        return None


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _is_our_tor(pid: int) -> bool:
    cmdline = _cmdline(pid)
    if not cmdline:
        return False
    executable = cmdline.split(" ", 1)[0]
    if executable.rsplit("/", 1)[-1] != "tor":
        return False
    return str(TORRC_PATH) in cmdline


def _cmdline(pid: int) -> str:
    try:
        raw = Path(f"/proc/{pid}/cmdline").read_bytes()
    except OSError:
        return ""
    return raw.replace(b"\x00", b" ").decode(errors="replace")


def _wait_port(port: int, timeout: float) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.4):
                return True
        except OSError:
            time.sleep(0.2)
    return False


def _wait_bootstrap(timeout: float) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            text = LOG_PATH.read_text(encoding="utf-8", errors="replace")
        except OSError:
            text = ""
        if "Bootstrapped 100%" in text:
            return
        time.sleep(0.4)


def _signal_newnym() -> None:
    if not COOKIE_PATH.exists():
        raise OnionError("Tor's control cookie is missing")
    cookie = COOKIE_PATH.read_bytes()
    try:
        sock = socket.create_connection(("127.0.0.1", CONTROL_PORT), timeout=10)
    except OSError as exc:
        raise OnionError(f"cannot reach Tor's control port: {exc}") from exc
    with sock:
        file = sock.makefile("rwb", buffering=0)
        file.readline()
        file.write(b"AUTHENTICATE " + cookie.hex().encode("ascii") + b"\r\n")
        auth = file.readline()
        if not auth.startswith(b"250"):
            raise OnionError("Tor refused the control cookie")
        file.write(b"SIGNAL NEWNYM\r\n")
        reply = file.readline()
        if not reply.startswith(b"250"):
            raise OnionError("Tor did not accept the new circuit request")
        file.write(b"QUIT\r\n")
