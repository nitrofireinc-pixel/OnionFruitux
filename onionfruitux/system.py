"""Privileged Tor and firewall changes.

connect() and disconnect() are the switch. They are not called by plan,
doctor, or the tests. Turning the switch off deletes only the onionfruitux
table and stops the Tor process this program started.
"""

from __future__ import annotations

import json
import os
import pwd
import shutil
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

# The firewall is installed only after Tor reports Bootstrapped 100%.
# These limits are what make a hung Tor or nftables command fail cleanly.
PORT_TIMEOUT = 25
BOOTSTRAP_TIMEOUT = 90
NFT_TIMEOUT = 15
HEARTBEAT_GRACE = 8

_helper_pid: int | None = None


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


def connect(cfg: Config, progress=None, heartbeat_file: str | None = None) -> None:
    """Start Tor, wait until it is fully connected, then install the firewall.

    The nftables table is not installed until Tor's log says Bootstrapped 100%.
    A timeout, a dead Tor, or the desktop process disappearing removes the
    table and stops only OnionFruitux's Tor.
    """
    if os.geteuid() != 0:
        raise OnionError("connecting needs an administrator password")
    _arm_parent_death()

    def report(message: str) -> None:
        if progress is not None:
            progress(message)

    def abort() -> None:
        _check_heartbeat(heartbeat_file)

    _prepare_runtime()
    rules = render_firewall(cfg.network)
    _assert_only_our_table(rules)
    _install_torrc(cfg)
    # Replace a previous OnionFruitux Tor before the new one binds the ports.
    stop_tor()
    _reset_log()
    report("Starting Tor…")
    try:
        proc = _spawn_tor()
        report("Waiting for Tor to open its proxy…")
        if not _wait_port(TRANS_PORT, PORT_TIMEOUT, abort=abort):
            raise OnionError(
                "Tor did not open its transparent proxy port. The firewall was not changed."
            )
        report("Waiting for Tor to finish connecting…")
        percent = _wait_bootstrap(BOOTSTRAP_TIMEOUT, progress=progress, abort=abort)
        if percent != 100:
            raise OnionError(
                "Tor did not finish connecting before the time limit. "
                "The firewall was not changed."
            )
        abort()
        report("Applying the firewall…")
        apply_ruleset(rules)
        _write_state(True, proc.pid, cfg.current_route().name)
        report("Connected.")
    except Exception:
        _rollback_network()
        raise


def disconnect() -> None:
    """Delete the onionfruitux table, then stop only our Tor process."""
    if os.geteuid() != 0:
        raise OnionError("disconnecting needs an administrator password")
    delete_table()
    stop_tor()
    _write_state(False, None, "")


def panic_off() -> None:
    """Remove OnionFruitux's firewall and Tor even when state says the switch is off.

    Safe to run twice. Does not open a browser. DNS redirects live only in the
    onionfruitux table, so deleting that table restores normal name lookup.
    """
    if os.geteuid() != 0:
        raise OnionError("clearing the firewall needs an administrator password")
    delete_table()
    stop_tor()
    _write_state(False, None, "")
    _flush_dns_cache()


def new_circuit() -> None:
    if os.geteuid() != 0:
        raise OnionError("a new circuit needs an administrator password")
    status = read_status()
    if not status["running"]:
        raise OnionError("OnionFruitux is off")
    _signal_newnym()


def apply_ruleset(rules: str) -> None:
    """Replace the onionfruitux table with rules OnionFruitux generated."""
    global _helper_pid
    _assert_only_our_table(rules)
    delete_table()
    try:
        proc = subprocess.Popen(
            ["nft", "-f", "-"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
    except FileNotFoundError as exc:
        raise OnionError("nftables is not installed") from exc
    _helper_pid = proc.pid
    try:
        try:
            stdout, stderr = proc.communicate(rules, timeout=NFT_TIMEOUT)
        except subprocess.TimeoutExpired as exc:
            proc.kill()
            proc.communicate()
            raise OnionError(
                "nftables did not finish; the firewall was not left in place"
            ) from exc
    finally:
        _helper_pid = None
    if proc.returncode != 0:
        detail = (stderr or stdout or "nft failed").strip()
        raise OnionError(detail)


def delete_table() -> None:
    try:
        subprocess.run(
            ["nft", "delete", "table", "inet", TABLE_NAME],
            capture_output=True,
            text=True,
            timeout=NFT_TIMEOUT,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return


def stop_tor() -> None:
    for pid in _our_tor_pids():
        _stop_pid(pid)
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


def _install_torrc(cfg: Config) -> None:
    torrc = render_torrc(cfg)
    TORRC_PATH.write_text(torrc, encoding="utf-8")
    os.chmod(TORRC_PATH, 0o640)
    try:
        user = pwd.getpwnam(TOR_USER)
    except KeyError as exc:
        raise OnionError("system user onionfruitux does not exist") from exc
    os.chown(TORRC_PATH, 0, user.pw_gid)


def _spawn_tor() -> subprocess.Popen:
    try:
        proc = subprocess.Popen(
            ["tor", "-f", str(TORRC_PATH)],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except FileNotFoundError as exc:
        raise OnionError("tor is not installed") from exc
    PID_PATH.write_text(f"{proc.pid}\n", encoding="utf-8")
    return proc


def _reset_log() -> None:
    """Drop a previous bootstrap line so a new Tor has to reach 100% itself."""
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    LOG_PATH.write_text("", encoding="utf-8")
    try:
        user = pwd.getpwnam(TOR_USER)
    except KeyError:
        user = None
    if user is not None:
        os.chown(LOG_PATH, user.pw_uid, user.pw_gid)
    os.chmod(LOG_PATH, 0o640)


def _rollback_network() -> None:
    _stop_helper()
    delete_table()
    stop_tor()
    _write_state(False, None, "")


def _stop_helper() -> None:
    pid = _helper_pid
    if not pid:
        return
    try:
        os.kill(pid, signal.SIGKILL)
    except OSError:
        return


def _arm_parent_death() -> None:
    """If pkexec's parent disappears, roll the firewall back and exit.

    The desktop process is not this process's parent. A supervisor between
    them sets the same parent-death signal on pkexec, so closing or killing
    the window during connect reaches this handler.
    """
    try:
        import ctypes

        libc = ctypes.CDLL("libc.so.6", use_errno=True)
        # 1 is PR_SET_PDEATHSIG.
        if libc.prctl(1, int(signal.SIGTERM), 0, 0, 0) != 0:
            return
    except (OSError, AttributeError):
        return
    signal.signal(signal.SIGTERM, _rollback_signal)
    signal.signal(signal.SIGHUP, _rollback_signal)
    # Do not exit when the parent is pid 1. `onionfruitux --system connect`
    # is started by systemd, and systemd is pid 1. A dead pkexec still
    # delivers the parent-death signal armed above; the desktop heartbeat
    # covers the moment before that signal is armed.


def _rollback_signal(signum, _frame) -> None:
    try:
        _rollback_network()
        time.sleep(0.2)
        delete_table()
    finally:
        os._exit(1)


def _check_heartbeat(path: str | None) -> None:
    if not path:
        return
    try:
        stamp = float(Path(path).read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return
    if time.time() - stamp > HEARTBEAT_GRACE:
        raise OnionError("Connecting was cancelled because OnionFruitux closed")


def _flush_dns_cache() -> None:
    if not shutil.which("resolvectl"):
        return
    try:
        subprocess.run(
            ["resolvectl", "flush-caches"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return


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


def _our_tor_pids() -> list[int]:
    found: list[int] = []
    pid = _read_pid()
    if pid:
        found.append(pid)
    try:
        entries = list(Path("/proc").iterdir())
    except OSError:
        entries = []
    for entry in entries:
        if not entry.name.isdigit():
            continue
        candidate = int(entry.name)
        if candidate in found:
            continue
        if _is_our_tor(candidate):
            found.append(candidate)
    return found


def _stop_pid(pid: int) -> None:
    if not _is_our_tor(pid):
        return
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    for _ in range(50):
        if not _alive(pid):
            return
        time.sleep(0.1)
    if _is_our_tor(pid):
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            return


def _wait_port(port: int, timeout: float, abort=None) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if abort is not None:
            abort()
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.4):
                return True
        except OSError:
            time.sleep(0.2)
    return False


def _bootstrap_percent() -> int | None:
    import re

    try:
        text = LOG_PATH.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    found = re.findall(r"Bootstrapped (\d+)%", text)
    if not found:
        return None
    return int(found[-1])


def _wait_bootstrap(timeout: float, progress=None, abort=None) -> int:
    deadline = time.time() + timeout
    last = -1
    while time.time() < deadline:
        if abort is not None:
            abort()
        percent = _bootstrap_percent()
        if percent is not None and percent != last:
            last = percent
            if progress is not None:
                progress(f"Tor bootstrap {percent}%…")
        if percent == 100:
            return 100
        time.sleep(0.4)
    percent = _bootstrap_percent()
    return percent if percent is not None else 0


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
