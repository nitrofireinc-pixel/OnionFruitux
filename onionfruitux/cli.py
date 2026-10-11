"""Command line for OnionFruitux."""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

from onionfruitux import system as system_mod
from onionfruitux.checkpage import open_check_page
from onionfruitux.config import (
    Config,
    Route,
    load_config,
    remove_route,
    save_config,
    upsert_route,
    use_route,
)
from onionfruitux.doctor import collect_checks, doctor_status, format_report
from onionfruitux.errors import OnionError
from onionfruitux.firewall import render_firewall
from onionfruitux.paths import BOOT_PATH, config_dir, config_path, error_path
from onionfruitux.preflight import validate_for_connect
from onionfruitux.torrc import render_torrc

# Long enough for the password dialog plus Tor's own bootstrap limit.
# The child stops itself sooner when Tor or nftables does not finish.
PRIVILEGED_TIMEOUT = 300

# Runs as its own one-thread process so it can ask the kernel to signal it
# when the desktop app exits. pkexec is its child and gets the same signal,
# which is what makes a force-quit roll the firewall back.
_PKEXEC_SUPERVISOR = r"""
import ctypes
import os
import signal
import subprocess
import sys


def _arm():
    try:
        libc = ctypes.CDLL("libc.so.6", use_errno=True)
        # 1 is PR_SET_PDEATHSIG.
        libc.prctl(1, int(signal.SIGTERM), 0, 0, 0)
    except Exception:
        return
    if os.getppid() == 1:
        os.kill(os.getpid(), signal.SIGTERM)


def main():
    _arm()
    proc = subprocess.Popen(sys.argv[1:], preexec_fn=_arm)

    def _stop(signum, _frame):
        proc.terminate()
        try:
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            proc.kill()
        os._exit(128 + int(signum))

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGHUP, _stop)
    raise SystemExit(proc.wait())


main()
"""


def main(argv: list[str] | None = None) -> int:
    args_list = list(sys.argv[1:] if argv is None else argv)
    if not args_list:
        return _call_window()
    args_list, flags = _extract_globals(args_list)
    if not args_list:
        return _call_window()
    parser = build_parser()
    args = parser.parse_args(args_list)
    args.privileged = flags["privileged"]
    args.system = flags["system"]
    args.config = flags["config"]
    args.error_file = flags["error_file"]
    args.progress_file = flags["progress_file"]
    args.heartbeat_file = flags["heartbeat_file"]
    try:
        _dispatch(args)
    except OnionError as exc:
        _record_error(args, str(exc))
        print(str(exc), file=sys.stderr)
        return 1
    except ValueError as exc:
        _record_error(args, str(exc))
        print(str(exc), file=sys.stderr)
        return 1
    _clear_error(args)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="onionfruitux",
        description="Send this computer's internet through Tor.",
    )
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("tray", help="panel or dock icon with the same switch")
    sub.add_parser("connect", help="turn the switch on")
    sub.add_parser("disconnect", help="turn the switch off")
    sub.add_parser(
        "panic-off",
        help="remove the firewall table and OnionFruitux's Tor immediately",
    )
    sub.add_parser("status", help="show whether the switch is on")
    sub.add_parser("new-circuit", help="ask Tor for a fresh path")
    sub.add_parser("doctor", help="check this machine without changing the network")
    sub.add_parser("plan", help="print the torrc and firewall rules, and change nothing")

    route = sub.add_parser("route", help="named routes")
    route_sub = route.add_subparsers(dest="route_command", required=True)
    add = route_sub.add_parser("add", help="create or replace a named route")
    add.add_argument("name")
    add.add_argument("--entry", default="")
    add.add_argument("--exit", default="")
    add.add_argument("--bridge", default="")
    add.add_argument(
        "--strict",
        action="store_true",
        help="only use the countries on this route",
    )
    listed = route_sub.add_parser("list", help="list routes")
    del listed
    use = route_sub.add_parser("use", help="choose the route the switch will use")
    use.add_argument("name")
    remove = route_sub.add_parser("remove", help="delete a named route")
    remove.add_argument("name")

    boot = sub.add_parser("boot", help="turn routing on or off at startup")
    boot_sub = boot.add_subparsers(dest="boot_command", required=True)
    boot_sub.add_parser("enable", help="save the current settings and route at startup")
    boot_sub.add_parser("disable", help="do not route at startup")
    return parser


def _call_window() -> int:
    try:
        return launch_window()
    except OnionError as exc:
        print(str(exc), file=sys.stderr)
        return 1


def launch_window() -> int:
    try:
        from onionfruitux.gui import run_window
    except ImportError as exc:
        raise OnionError(
            "The window needs PyQt6 or PySide6. Install them with sudo ./install.sh, "
            "or use onionfruitux plan, connect, and the other commands."
        ) from exc
    return run_window()


def launch_tray() -> int:
    try:
        from onionfruitux.tray import run_tray
    except ImportError as exc:
        raise OnionError(
            "The tray icon needs PyQt6 or PySide6. Install them with sudo ./install.sh."
        ) from exc
    return run_tray()


def connect_command(
    *,
    privileged: bool = False,
    system: bool = False,
    config: str | None = None,
    error_file: str | None = None,
    progress=None,
    open_page: bool | None = None,
    progress_file: str | None = None,
    heartbeat_file: str | None = None,
) -> None:
    cfg = _config_for(system=system, config=config)
    errors = validate_for_connect(cfg)
    if errors:
        raise OnionError("\n".join(errors))
    reporter = _publish_progress(progress_file, progress)
    if _needs_admin(privileged, system):
        progress_path, heartbeat_path = _switch_paths(error_file)
        code = escalate(
            [
                "connect",
                "--config",
                str(_user_config(config)),
                "--progress-file",
                str(progress_path),
                "--heartbeat-file",
                str(heartbeat_path),
            ],
            error_file=error_file,
            progress=progress,
            progress_file=str(progress_path),
            heartbeat_file=str(heartbeat_path),
        )
        if code != 0:
            raise OnionError(_escalate_failure(error_file))
        # The page is opened by this desktop process after pkexec returns.
        # The root child never opens a browser.
        if _wants_check_page(cfg, open_page):
            open_check_page()
        return
    system_mod.connect(cfg, progress=reporter, heartbeat_file=heartbeat_file)
    if (
        not system
        and not privileged
        and os.geteuid() != 0
        and _wants_check_page(cfg, open_page)
    ):
        open_check_page()


def disconnect_command(
    *,
    privileged: bool = False,
    system: bool = False,
    config: str | None = None,
    error_file: str | None = None,
) -> None:
    # System disconnect must work even when boot settings were not saved.
    # The check page is a desktop action, so the boot service never opens it.
    open_page = False
    if not system:
        cfg = load_config(_user_config(config))
        open_page = cfg.network.open_check_page
    if _needs_admin(privileged, system):
        code = escalate(
            ["disconnect", "--config", str(_user_config(config))],
            error_file=error_file,
        )
        if code != 0:
            raise OnionError(_escalate_failure(error_file))
        if open_page:
            open_check_page()
        return
    system_mod.disconnect()
    if open_page and not privileged and os.geteuid() != 0:
        open_check_page()


def panic_off_command(
    *,
    privileged: bool = False,
    system: bool = False,
    error_file: str | None = None,
) -> None:
    """Clear the firewall without opening a browser."""
    if _needs_admin(privileged, system):
        code = escalate(["panic-off"], error_file=error_file)
        if code != 0:
            raise OnionError(_escalate_failure(error_file))
        return
    system_mod.panic_off()


def new_circuit_command(
    *,
    privileged: bool = False,
    system: bool = False,
    error_file: str | None = None,
) -> None:
    if not system_mod.read_status()["running"]:
        raise OnionError("OnionFruitux is off")
    if _needs_admin(privileged, system):
        code = escalate(["new-circuit"], error_file=error_file)
        if code != 0:
            raise OnionError(_escalate_failure(error_file))
        return
    system_mod.new_circuit()


def plan_command(config: str | None = None) -> str:
    cfg = load_config(_user_config(config))
    route = cfg.current_route()
    title = route.name or "(no named route)"
    body = "\n".join(
        [
            f"# OnionFruitux plan for {title}",
            "# This command changes nothing.",
            "",
            "### torrc",
            render_torrc(cfg, route).rstrip(),
            "",
            "### nftables",
            render_firewall(cfg.network).rstrip(),
            "",
        ]
    )
    return body


def status_command() -> str:
    cfg = load_config()
    state = system_mod.read_status()
    if state["running"]:
        route = cfg.route_named(state["route"]) or Route(name=state["route"] or "")
    else:
        route = cfg.current_route()
    if not state["running"]:
        lines = ["OnionFruitux is off"]
        if cfg.active_route:
            lines.append(f"Route: {cfg.active_route}")
        return "\n".join(lines) + "\n"
    lines = ["OnionFruitux is on", f"Route: {route.name or '(none)'}"]
    if route.entry and not route.bridge:
        lines.append(f"Entry: {route.entry}")
    if route.exit:
        lines.append(f"Exit: {route.exit}")
    lines.append(f"Bridge: {route.bridge or 'none'}")
    return "\n".join(lines) + "\n"


def format_route_list(cfg: Config) -> str:
    if not cfg.routes:
        return "No routes.\n"
    lines = []
    for route in cfg.routes:
        mark = "*" if route.name == cfg.active_route else " "
        bits = [f"{mark} {route.name}"]
        if route.entry:
            bits.append(f"entry {route.entry}")
        if route.exit:
            bits.append(f"exit {route.exit}")
        if route.bridge:
            bits.append(f"bridge {route.bridge}")
        if route.strict:
            bits.append("strict")
        lines.append("  ".join(bits))
    return "\n".join(lines) + "\n"


def _dispatch(args: argparse.Namespace) -> None:
    if args.command == "tray":
        launch_tray()
        return
    if args.command == "connect":
        connect_command(
            privileged=args.privileged,
            system=args.system,
            config=args.config,
            error_file=args.error_file,
            progress_file=args.progress_file,
            heartbeat_file=args.heartbeat_file,
        )
        return
    if args.command == "disconnect":
        disconnect_command(
            privileged=args.privileged,
            system=args.system,
            config=args.config,
            error_file=args.error_file,
        )
        return
    if args.command == "panic-off":
        panic_off_command(
            privileged=args.privileged,
            system=args.system,
            error_file=args.error_file,
        )
        print("OnionFruitux is off. The firewall table has been removed.")
        return
    if args.command == "status":
        print(status_command(), end="")
        return
    if args.command == "new-circuit":
        new_circuit_command(
            privileged=args.privileged,
            system=args.system,
            error_file=args.error_file,
        )
        print("Tor is building a new circuit.")
        return
    if args.command == "doctor":
        cfg = load_config(_user_config(args.config))
        checks = collect_checks(cfg)
        print(format_report(checks), end="", flush=True)
        if doctor_status(checks) != 0:
            raise SystemExit(1)
        return
    if args.command == "plan":
        print(plan_command(args.config), end="")
        return
    if args.command == "route":
        _route_command(args)
        return
    if args.command == "boot":
        _boot_command(args)
        return
    raise OnionError("unknown command")


def _route_command(args: argparse.Namespace) -> None:
    path = _user_config(args.config)
    cfg = load_config(path)
    if args.route_command == "add":
        route = Route(
            name=args.name,
            entry=args.entry,
            exit=args.exit,
            bridge=args.bridge,
            strict=args.strict,
        )
        saved = upsert_route(cfg, route)
        if not cfg.active_route:
            cfg.active_route = saved.name
        save_config(cfg, path)
        print(f"Saved route {saved.name}.")
        return
    if args.route_command == "list":
        print(format_route_list(cfg), end="")
        return
    if args.route_command == "use":
        use_route(cfg, args.name)
        save_config(cfg, path)
        print(f"Using route {args.name}.")
        if system_mod.read_status()["running"]:
            print("Turn the switch off and on to use it.")
        return
    if args.route_command == "remove":
        remove_route(cfg, args.name)
        save_config(cfg, path)
        print(f"Removed route {args.name}.")
        return
    raise OnionError("unknown route command")


def _boot_command(args: argparse.Namespace) -> None:
    if args.boot_command == "enable":
        path = _user_config(args.config)
        cfg = load_config(path)
        if _needs_admin(args.privileged, args.system):
            code = escalate(
                ["boot", "enable", "--config", str(path)],
                error_file=args.error_file,
            )
            if code != 0:
                raise OnionError(_escalate_failure(args.error_file))
            print("Routing will turn on at startup.")
            return
        system_mod.enable_boot(cfg)
        print("Routing will turn on at startup.")
        return
    if args.boot_command == "disable":
        if _needs_admin(args.privileged, args.system):
            code = escalate(["boot", "disable"], error_file=args.error_file)
            if code != 0:
                raise OnionError(_escalate_failure(args.error_file))
            print("Routing will stay off at startup.")
            return
        system_mod.disable_boot()
        print("Routing will stay off at startup.")
        return
    raise OnionError("unknown boot command")


def _config_for(*, system: bool, config: str | None) -> Config:
    if system:
        if not BOOT_PATH.exists():
            raise OnionError(
                "boot settings are missing; run onionfruitux boot enable while the switch is set up"
            )
        return load_config(BOOT_PATH)
    return load_config(_user_config(config))


def _user_config(config: str | None) -> Path:
    if config:
        return Path(config)
    return config_path()


def _needs_admin(privileged: bool, system: bool) -> bool:
    if privileged or system:
        return False
    return os.geteuid() != 0


def _executable() -> str:
    override = os.environ.get("ONIONFRUITUX_BIN")
    if override:
        return override
    installed = Path("/usr/bin/onionfruitux")
    if installed.exists():
        return str(installed)
    found = shutil.which("onionfruitux")
    if found:
        return found
    return _interpreter()


def _is_python_binary(path: str) -> bool:
    name = os.path.basename(path)
    if name != "python3" and not name.startswith("python3."):
        return False
    return os.path.isfile(path) and os.access(path, os.X_OK)


def _interpreter() -> str:
    """Absolute path of Python, never the onionfruitux shell wrapper.

    v1.0.4 started the process with argv[0] set to the bare name
    onionfruitux. Python searched PATH, found /usr/bin/onionfruitux, and
    stored that script in sys.executable. The window used that path to
    start the permission helper, so the helper ran the script again and
    pkexec never started.
    """
    candidates: list[str] = []
    try:
        candidates.append(os.path.realpath("/proc/self/exe"))
    except OSError:
        pass
    override = os.environ.get("ONIONFRUITUX_PYTHON")
    if override:
        candidates.append(override)
    if sys.executable:
        candidates.append(sys.executable)
    found = shutil.which("python3")
    if found:
        candidates.append(found)
    for path in candidates:
        if _is_python_binary(path):
            return path
    raise OnionError("Could not find Python to ask for permission to change the firewall.")


def _pkexec_stderr_path(error_file: str) -> Path:
    return Path(str(error_file) + ".pkexec")


def _pkexec_status_path(error_file: str) -> Path:
    return Path(str(error_file) + ".pkexec-status")


def escalate(
    args: list[str],
    error_file: str | None = None,
    progress=None,
    progress_file: str | None = None,
    heartbeat_file: str | None = None,
    timeout: float | None = None,
) -> int:
    target = error_file or str(error_path())
    Path(target).parent.mkdir(parents=True, exist_ok=True)
    stderr_path = _pkexec_stderr_path(target)
    status_path = _pkexec_status_path(target)
    stderr_path.write_text("", encoding="utf-8")
    status_path.write_text("", encoding="utf-8")
    os.chmod(stderr_path, 0o644)
    os.chmod(status_path, 0o644)
    exe = _executable()
    interpreter = _interpreter()
    if exe == interpreter:
        command = [exe, "-m", "onionfruitux", "--privileged", *args, "--error-file", target]
    else:
        command = [exe, "--privileged", *args, "--error-file", target]
    if not shutil.which("pkexec"):
        raise OnionError("pkexec is not installed; install polkit or run the command with sudo")
    if progress is not None:
        progress("Asking for permission…")
    if heartbeat_file:
        _write_heartbeat(heartbeat_file)
    if progress_file:
        try:
            Path(progress_file).write_text("", encoding="utf-8")
        except OSError:
            pass
    # stdout stays on the terminal. A full stdout pipe is one way the window
    # used to hang. stderr goes to a file so a refused pkexec can be shown;
    # the password dialog itself is the desktop polkit agent, not the tty.
    with stderr_path.open("a", encoding="utf-8") as stderr_handle:
        proc = subprocess.Popen(
            [interpreter, "-c", _PKEXEC_SUPERVISOR, "pkexec", *command],
            stderr=stderr_handle,
        )
        code = _follow_privileged(
            proc,
            progress=progress,
            progress_file=progress_file,
            heartbeat_file=heartbeat_file,
            timeout=PRIVILEGED_TIMEOUT if timeout is None else timeout,
        )
    try:
        status_path.write_text(f"{code}\n", encoding="utf-8")
        os.chmod(status_path, 0o644)
    except OSError:
        pass
    return code


def _follow_privileged(
    proc,
    *,
    progress,
    progress_file: str | None,
    heartbeat_file: str | None,
    timeout: float,
) -> int:
    """Poll pkexec so the caller can show progress and give up if it sticks."""
    deadline = time.time() + timeout
    last = ""
    while True:
        if heartbeat_file:
            _write_heartbeat(heartbeat_file)
        if progress_file and progress is not None:
            text = _read_progress(progress_file)
            if text and text != last:
                last = text
                progress(text)
        code = proc.poll()
        if code is not None:
            if progress_file and progress is not None:
                text = _read_progress(progress_file)
                if text and text != last:
                    progress(text)
            return code
        if time.time() > deadline:
            proc.terminate()
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                proc.kill()
                try:
                    proc.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    pass
            raise OnionError(
                "That step took too long and was cancelled. "
                "If the switch was turning on, the firewall was rolled back."
            )
        time.sleep(0.2)


def _write_heartbeat(path: str) -> None:
    try:
        file = Path(path)
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text(f"{time.time()}\n", encoding="utf-8")
        os.chmod(file, 0o644)
    except OSError:
        return


def _read_progress(path: str) -> str:
    try:
        return Path(path).read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def _switch_paths(error_file: str | None) -> tuple[Path, Path]:
    base = Path(error_file).parent if error_file else config_dir()
    base.mkdir(parents=True, exist_ok=True)
    return base / "progress", base / "heartbeat"


def _publish_progress(progress_file: str | None, progress):
    def report(message: str) -> None:
        if progress_file:
            try:
                path = Path(progress_file)
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(message + "\n", encoding="utf-8")
                os.chmod(path, 0o644)
            except OSError:
                pass
        if progress is not None:
            progress(message)

    return report


def _wants_check_page(cfg: Config, open_page: bool | None) -> bool:
    if open_page is False:
        return False
    return bool(cfg.network.open_check_page)


def _escalate_failure(error_file: str | None) -> str:
    path = Path(error_file) if error_file else error_path()
    helper = ""
    if path.exists():
        helper = path.read_text(encoding="utf-8").strip()
    code = _read_pkexec_status(path)
    stderr = _read_pkexec_stderr(path)
    parts: list[str] = []
    if helper:
        parts.append(helper)
    else:
        parts.append("Could not get permission to change the firewall.")
    if code is not None:
        parts.append(f"pkexec exited {code}.")
    if stderr and stderr not in helper:
        parts.append(stderr)
    return "\n".join(parts)


def _read_pkexec_status(error_file: Path) -> int | None:
    try:
        text = _pkexec_status_path(str(error_file)).read_text(encoding="utf-8").strip()
    except OSError:
        return None
    if not text:
        return None
    try:
        return int(text)
    except ValueError:
        return None


def _read_pkexec_stderr(error_file: Path) -> str:
    try:
        return _pkexec_stderr_path(str(error_file)).read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def _record_error(args: argparse.Namespace, message: str) -> None:
    if not getattr(args, "error_file", None):
        return
    path = Path(args.error_file)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(message + "\n", encoding="utf-8")
        os.chmod(path, 0o644)
    except OSError:
        return


def _extract_globals(argv: list[str]) -> tuple[list[str], dict]:
    """Pull hidden flags out from anywhere on the command line.

    pkexec re-invokes ``onionfruitux --privileged connect --config PATH``,
    and those options have to work both before and after the subcommand.
    """
    flags: dict[str, object] = {
        "privileged": False,
        "system": False,
        "config": None,
        "error_file": None,
        "progress_file": None,
        "heartbeat_file": None,
    }
    cleaned: list[str] = []
    index = 0
    while index < len(argv):
        item = argv[index]
        if item == "--privileged":
            flags["privileged"] = True
        elif item == "--system":
            flags["system"] = True
        elif item == "--config" and index + 1 < len(argv):
            flags["config"] = argv[index + 1]
            index += 1
        elif item == "--error-file" and index + 1 < len(argv):
            flags["error_file"] = argv[index + 1]
            index += 1
        elif item == "--progress-file" and index + 1 < len(argv):
            flags["progress_file"] = argv[index + 1]
            index += 1
        elif item == "--heartbeat-file" and index + 1 < len(argv):
            flags["heartbeat_file"] = argv[index + 1]
            index += 1
        else:
            cleaned.append(item)
        index += 1
    return cleaned, flags


def _clear_error(args: argparse.Namespace) -> None:
    if not getattr(args, "privileged", False):
        return
    if not getattr(args, "error_file", None):
        return
    path = Path(args.error_file)
    if path.exists():
        try:
            path.unlink()
        except OSError:
            return
