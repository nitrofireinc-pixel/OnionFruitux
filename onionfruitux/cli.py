"""Command line for OnionFruitux."""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
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
from onionfruitux.paths import BOOT_PATH, config_path, error_path
from onionfruitux.preflight import validate_for_connect
from onionfruitux.torrc import render_torrc


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
) -> None:
    cfg = _config_for(system=system, config=config)
    errors = validate_for_connect(cfg)
    if errors:
        raise OnionError("\n".join(errors))
    if _needs_admin(privileged, system):
        code = escalate(
            ["connect", "--config", str(_user_config(config))],
            error_file=error_file,
        )
        if code != 0:
            raise OnionError(_escalate_failure(error_file))
        if cfg.network.open_check_page:
            open_check_page()
        return
    system_mod.connect(cfg)
    if not system and not privileged and cfg.network.open_check_page:
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
    if open_page and not privileged:
        open_check_page()


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
    return sys.executable


def escalate(args: list[str], error_file: str | None = None) -> int:
    target = error_file or str(error_path())
    Path(target).parent.mkdir(parents=True, exist_ok=True)
    exe = _executable()
    if exe == sys.executable:
        command = [exe, "-m", "onionfruitux", "--privileged", *args, "--error-file", target]
    else:
        command = [exe, "--privileged", *args, "--error-file", target]
    if not shutil.which("pkexec"):
        raise OnionError("pkexec is not installed; install polkit or run the command with sudo")
    return subprocess.call(["pkexec", *command])


def _escalate_failure(error_file: str | None) -> str:
    path = Path(error_file) if error_file else error_path()
    if path.exists():
        text = path.read_text(encoding="utf-8").strip()
        if text:
            return text
    return "Could not get permission to change the firewall."


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
