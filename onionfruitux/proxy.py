"""System proxy for the desktop session.

Enabling writes a snapshot, then points GNOME, KDE, and new-session
environment variables at Tor's SOCKS port. Disabling puts that snapshot
back and deletes it. Nothing in this module changes the firewall, DNS,
NetworkManager, or any VPN connection.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

from onionfruitux.errors import DirectUnreachable, OnionError
from onionfruitux.health import (
    leftover_firewall_hint,
    resolv_conf_hint,
    verify_direct,
    verify_tor,
)
from onionfruitux.paths import SOCKS_PORT

SNAPSHOT_NAME = "saved-state.json"
ENV_FILE_NAME = "onionfruitux.conf"
KDE_GROUP = "Proxy Settings"
SOCKS_HOST = "127.0.0.1"

# Variables set for programs started after this session picks them up.
# http_proxy is left alone: Tor's port speaks SOCKS, not HTTP proxy.
SESSION_KEYS = (
    "ALL_PROXY",
    "all_proxy",
    "SOCKS_PROXY",
    "socks_proxy",
    "NO_PROXY",
    "no_proxy",
)

_GNOME_KEYS = (
    ("org.gnome.system.proxy", "mode"),
    ("org.gnome.system.proxy", "autoconfig-url"),
    ("org.gnome.system.proxy", "ignore-hosts"),
    ("org.gnome.system.proxy", "use-same-proxy"),
    ("org.gnome.system.proxy.http", "enabled"),
    ("org.gnome.system.proxy.http", "host"),
    ("org.gnome.system.proxy.http", "port"),
    ("org.gnome.system.proxy.http", "use-authentication"),
    ("org.gnome.system.proxy.http", "authentication-user"),
    ("org.gnome.system.proxy.http", "authentication-password"),
    ("org.gnome.system.proxy.https", "host"),
    ("org.gnome.system.proxy.https", "port"),
    ("org.gnome.system.proxy.ftp", "host"),
    ("org.gnome.system.proxy.ftp", "port"),
    ("org.gnome.system.proxy.socks", "host"),
    ("org.gnome.system.proxy.socks", "port"),
)

# These commands change routing, DNS, or VPN state. This module refuses them.
_FORBIDDEN = {
    "nft",
    "iptables",
    "ip6tables",
    "nftables",
    "nmcli",
    "nmtui",
    "resolvectl",
    "systemd-resolve",
    "tailscale",
    "wg",
    "wg-quick",
    "protonvpn",
    "protonvpn-cli",
    "proton-vpn",
}


def state_dir() -> Path:
    base = os.environ.get("XDG_STATE_HOME")
    if base:
        return Path(base) / "onionfruitux"
    return Path.home() / ".local" / "state" / "onionfruitux"


def snapshot_path() -> Path:
    return state_dir() / SNAPSHOT_NAME


def parse_ignore_hosts(value: str) -> list[str]:
    hosts: list[str] = []
    for part in value.replace("\n", ",").replace(";", ",").split(","):
        item = part.strip()
        if item and item not in hosts:
            hosts.append(item)
    return hosts


def capture_snapshot() -> dict:
    """Write every proxy setting this program is about to change."""
    payload = {
        "version": 1,
        "gnome": _read_gnome(),
        "kde": _read_kde(),
        "environment": _read_environment(),
    }
    _write_snapshot(payload)
    return payload


def apply_proxy(port: int = SOCKS_PORT, ignore_hosts: list[str] | None = None) -> None:
    """Point the system proxy at Tor. The snapshot must already exist."""
    hosts = list(ignore_hosts or [])
    if not snapshot_path().exists():
        raise OnionError("proxy settings were not saved, so they were not changed")
    _apply_gnome(port, hosts)
    _apply_kde(port, hosts)
    _apply_environment(port, hosts)


def restore_snapshot(*, delete: bool) -> bool:
    """Put the snapshot back. Returns False when there is nothing to restore."""
    path = snapshot_path()
    if not path.exists():
        return False
    try:
        saved = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise OnionError(f"cannot read the saved proxy settings: {exc}") from exc
    if not isinstance(saved, dict):
        raise OnionError("the saved proxy settings are not a settings file")
    _restore_gnome(saved.get("gnome") or {})
    _restore_kde(saved.get("kde") or {})
    _restore_environment(saved.get("environment") or {})
    if delete:
        _delete_snapshot()
    return True


def enable_session(ignore_hosts: list[str], port: int = SOCKS_PORT, progress=None) -> None:
    """Save the current proxy, switch it to Tor, and check Tor answers."""
    report = progress or (lambda _message: None)
    report("Saving current proxy settings…")
    capture_snapshot()
    try:
        report("Setting the system proxy to Tor…")
        apply_proxy(port, ignore_hosts)
        report("Checking that traffic through the proxy is using Tor…")
        verify_tor(port)
    except Exception:
        report("Putting the previous proxy settings back…")
        try:
            restore_snapshot(delete=True)
        except OnionError:
            pass
        raise


def disable_session() -> None:
    """Restore the snapshot and prove the machine can reach the internet directly.

    The snapshot is kept until that check passes, so a failed check can be
    restored a second time. A second failure leaves the file in place.
    """
    restore_snapshot(delete=False)
    report = verify_direct()
    if not report.ok:
        restore_snapshot(delete=False)
        report = verify_direct()
    if report.ok:
        _delete_snapshot()
        return
    raise DirectUnreachable(direct_failure_message(report))


def quit_restore() -> None:
    """Restore on exit. Does nothing when this session did not change the proxy."""
    if not snapshot_path().exists():
        return
    disable_session()


def reset_proxy_defaults() -> None:
    """Set the system proxy mode to none and drop OnionFruitux's session file.

    This does not change DNS, the firewall, or VPN connections.
    """
    if _gnome_available():
        _gset("org.gnome.system.proxy", "mode", "'none'")
        _gset("org.gnome.system.proxy.socks", "host", "''")
        _gset("org.gnome.system.proxy.socks", "port", "0")
        _gset("org.gnome.system.proxy", "use-same-proxy", "false")
    if _kde_session() or kioslaverc_path().exists():
        _write_kde_keys({"ProxyType": "0", "socksProxy": ""})
    path = environment_file()
    try:
        path.unlink()
    except OSError:
        pass
    _unset_session_keys()
    _delete_snapshot()


def describe_current_settings() -> str:
    """What the proxy is set to right now, for the failure message."""
    lines: list[str] = []
    gnome = _read_gnome()
    if gnome.get("available"):
        values = gnome.get("values") or {}
        lines.append(f"GNOME proxy mode: {_plain(values.get(_gnome_id('mode'), ''))}")
        lines.append(
            "GNOME SOCKS: "
            f"{_plain(values.get(_gnome_id('socks-host'), ''))}:"
            f"{_plain(values.get(_gnome_id('socks-port'), ''))}"
        )
        lines.append(
            "GNOME ignore-hosts: " + _plain(values.get(_gnome_id("ignore-hosts"), ""))
        )
    else:
        lines.append("GNOME proxy settings are not available in this session.")
    kde = _read_kde()
    if kde.get("applicable"):
        group = kde.get("group") or {}
        if kde.get("existed") and kde.get("had_group"):
            lines.append(
                "KDE proxy type: "
                f"{group.get('ProxyType', '(unset)')}, socks: {group.get('socksProxy', '(unset)')}"
            )
        else:
            lines.append("KDE proxy settings were not set.")
    env = _read_environment()
    session = (env.get("session") or {}).get("values") or {}
    shown = [f"{key}={value}" for key, value in session.items() if value]
    if shown:
        lines.append("Session environment: " + ", ".join(shown))
    else:
        lines.append("Session environment has no OnionFruitux proxy variables.")
    if env.get("existed"):
        lines.append(f"New-session file is present: {env.get('path')}")
    else:
        lines.append("New-session file is not present.")
    hint = resolv_conf_hint()
    if hint:
        lines.append(hint)
    firewall = leftover_firewall_hint()
    if firewall:
        lines.append(firewall)
    return "\n".join(lines)


def direct_failure_message(report) -> str:
    return "\n".join(
        [
            "The computer still cannot reach the internet directly.",
            report.text(),
            "",
            "Settings that are still in place:",
            describe_current_settings(),
            "",
            "Reset network settings to defaults sets the system proxy mode to none.",
            "It does not change DNS, the firewall, or VPN connections.",
        ]
    )


def switch_is_on() -> bool:
    """The desktop switch is on when Tor is up and this session saved its proxy."""
    from onionfruitux.system import read_status

    return bool(read_status()["running"]) and snapshot_path().exists()


def proxy_plan(port: int, ignore_hosts: str) -> str:
    hosts = ", ".join(parse_ignore_hosts(ignore_hosts)) or "(none)"
    return "\n".join(
        [
            "### system proxy",
            f"SOCKS {SOCKS_HOST}:{port}",
            f"Ignore hosts: {hosts}",
            "GNOME: org.gnome.system.proxy mode manual, socks host and port",
            "KDE: kioslaverc [Proxy Settings] socksProxy, when this is a KDE session",
            f"New sessions: ALL_PROXY and SOCKS_PROXY socks5h://{SOCKS_HOST}:{port}",
            "This does not change iptables, nftables, resolv.conf, or VPN connections.",
        ]
    )


def environment_file() -> Path:
    return _config_home() / "environment.d" / ENV_FILE_NAME


def kioslaverc_path() -> Path:
    return _config_home() / "kioslaverc"


def _config_home() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME")
    if base:
        return Path(base)
    return Path.home() / ".config"


def _gnome_id(name: str) -> str:
    if name == "mode":
        return "org.gnome.system.proxy mode"
    if name == "ignore-hosts":
        return "org.gnome.system.proxy ignore-hosts"
    if name == "socks-host":
        return "org.gnome.system.proxy.socks host"
    if name == "socks-port":
        return "org.gnome.system.proxy.socks port"
    return name


def _plain(value: str) -> str:
    text = value.strip()
    if len(text) >= 2 and text[0] == "'" and text[-1] == "'":
        return text[1:-1]
    return text


def _read_gnome() -> dict:
    if not _gnome_available():
        return {"available": False, "values": {}}
    values: dict[str, str] = {}
    for schema, key in _GNOME_KEYS:
        result = _run(["gsettings", "get", schema, key])
        if result is None or result.returncode != 0:
            continue
        values[f"{schema} {key}"] = result.stdout.strip()
    return {"available": True, "values": values}


def _gnome_available() -> bool:
    if not shutil.which("gsettings"):
        return False
    result = _run(["gsettings", "get", "org.gnome.system.proxy", "mode"])
    return result is not None and result.returncode == 0


def _apply_gnome(port: int, hosts: list[str]) -> None:
    if not _gnome_available():
        return
    _gset("org.gnome.system.proxy", "mode", "'manual'")
    _gset("org.gnome.system.proxy", "use-same-proxy", "false")
    _gset("org.gnome.system.proxy.socks", "host", f"'{SOCKS_HOST}'")
    _gset("org.gnome.system.proxy.socks", "port", str(port))
    _gset("org.gnome.system.proxy", "ignore-hosts", _variant_array(hosts))


def _restore_gnome(saved: dict) -> None:
    if not isinstance(saved, dict) or not saved.get("available"):
        return
    if not shutil.which("gsettings"):
        return
    values = saved.get("values") or {}
    if not isinstance(values, dict):
        return
    for name, raw in values.items():
        parts = str(name).split(" ", 1)
        if len(parts) != 2 or not isinstance(raw, str):
            continue
        _gset(parts[0], parts[1], raw)


def _gset(schema: str, key: str, value: str) -> None:
    result = _run(["gsettings", "set", schema, key, value])
    if result is None or result.returncode != 0:
        detail = ""
        if result is not None:
            detail = (result.stderr or result.stdout).strip()
        raise OnionError(detail or f"could not set {schema} {key}")


def _variant_array(hosts: list[str]) -> str:
    quoted = []
    for host in hosts:
        escaped = host.replace("\\", "\\\\").replace("'", "\\'")
        quoted.append("'" + escaped + "'")
    return "[" + ", ".join(quoted) + "]"


def _kde_session() -> bool:
    desktop = " ".join(
        os.environ.get(name, "")
        for name in ("XDG_CURRENT_DESKTOP", "XDG_SESSION_DESKTOP", "DESKTOP_SESSION")
    ).upper()
    if "KDE" in desktop or "PLASMA" in desktop:
        return True
    return bool(os.environ.get("KDE_FULL_SESSION"))


def _read_kde() -> dict:
    path = kioslaverc_path()
    applicable = _kde_session()
    if not applicable:
        return {"applicable": False, "path": str(path), "existed": path.exists(), "had_group": False, "group": {}}
    existed = path.exists()
    group: dict[str, str] = {}
    had_group = False
    if existed:
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            text = ""
        found = _read_group(text, KDE_GROUP)
        if found is not None:
            had_group = True
            group = found
    return {
        "applicable": True,
        "path": str(path),
        "existed": existed,
        "had_group": had_group,
        "group": group,
    }


def _apply_kde(port: int, hosts: list[str]) -> None:
    if not _kde_session():
        return
    _write_kde_keys(
        {
            "ProxyType": "1",
            "socksProxy": f"socks://{SOCKS_HOST}:{port}",
            "NoProxyFor": ",".join(hosts),
        }
    )
    _notify_kde()


def _restore_kde(saved: dict) -> None:
    if not isinstance(saved, dict) or not saved.get("applicable"):
        return
    path = Path(str(saved.get("path") or kioslaverc_path()))
    group = saved.get("group") if isinstance(saved.get("group"), dict) else {}
    if not saved.get("existed"):
        try:
            path.unlink()
        except OSError:
            pass
        return
    try:
        text = path.read_text(encoding="utf-8") if path.exists() else ""
    except OSError:
        text = ""
    if saved.get("had_group"):
        updated = _replace_group(text, KDE_GROUP, {str(k): str(v) for k, v in group.items()})
    else:
        updated = _remove_group(text, KDE_GROUP)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(updated, encoding="utf-8")
        os.chmod(path, 0o644)
    except OSError as exc:
        raise OnionError(f"could not restore KDE proxy settings: {exc}") from exc
    _notify_kde()


def _write_kde_keys(keys: dict[str, str]) -> None:
    path = kioslaverc_path()
    try:
        text = path.read_text(encoding="utf-8") if path.exists() else ""
    except OSError:
        text = ""
    current = _read_group(text, KDE_GROUP) or {}
    current.update(keys)
    updated = _replace_group(text, KDE_GROUP, current)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(updated, encoding="utf-8")
        os.chmod(path, 0o644)
    except OSError as exc:
        raise OnionError(f"could not write KDE proxy settings: {exc}") from exc
    for key, value in keys.items():
        for tool in ("kwriteconfig6", "kwriteconfig5"):
            if not shutil.which(tool):
                continue
            _run(
                [
                    tool,
                    "--file",
                    "kioslaverc",
                    "--group",
                    KDE_GROUP,
                    "--key",
                    key,
                    value,
                ]
            )
            break


def _notify_kde() -> None:
    if not shutil.which("dbus-send"):
        return
    _run(
        [
            "dbus-send",
            "--session",
            "--type=signal",
            "/KIO/Scheduler",
            "org.kde.KIO.Scheduler.reparseSlaveConfiguration",
            "string:",
        ]
    )


def _read_group(text: str, group: str) -> dict[str, str] | None:
    header = f"[{group}]"
    lines = text.splitlines()
    start = None
    for index, line in enumerate(lines):
        if line.strip() == header:
            start = index
            break
    if start is None:
        return None
    found: dict[str, str] = {}
    for line in lines[start + 1 :]:
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            break
        if not stripped or stripped.startswith(("#", ";")) or "=" not in line:
            continue
        key, value = line.split("=", 1)
        found[key.strip()] = value.strip()
    return found


def _replace_group(text: str, group: str, values: dict[str, str]) -> str:
    body = _remove_group(text, group).rstrip("\n")
    block = [f"[{group}]"]
    for key, value in values.items():
        block.append(f"{key}={value}")
    addition = "\n".join(block) + "\n"
    if not body:
        return addition
    return body + "\n" + addition


def _remove_group(text: str, group: str) -> str:
    header = f"[{group}]"
    lines = text.splitlines(keepends=True)
    output: list[str] = []
    skipping = False
    for line in lines:
        stripped = line.strip()
        if stripped == header:
            skipping = True
            continue
        if skipping and stripped.startswith("[") and stripped.endswith("]"):
            skipping = False
        if skipping:
            continue
        output.append(line)
    return "".join(output)


def _read_environment() -> dict:
    path = environment_file()
    existed = path.exists()
    text = None
    if existed:
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            text = None
            existed = False
    session_values: dict[str, str | None] = {}
    available = False
    if shutil.which("systemctl"):
        result = _run(["systemctl", "--user", "show-environment"])
        if result is not None and result.returncode == 0:
            available = True
            found = _parse_environment(result.stdout)
            for key in SESSION_KEYS:
                session_values[key] = found.get(key)
    return {
        "path": str(path),
        "existed": existed,
        "text": text,
        "session": {"available": available, "values": session_values},
    }


def _apply_environment(port: int, hosts: list[str]) -> None:
    no_proxy = ",".join(hosts)
    url = f"socks5h://{SOCKS_HOST}:{port}"
    lines = [
        f"ALL_PROXY={url}",
        f"all_proxy={url}",
        f"SOCKS_PROXY={url}",
        f"socks_proxy={url}",
        f"NO_PROXY={no_proxy}",
        f"no_proxy={no_proxy}",
        "",
    ]
    path = environment_file()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(lines), encoding="utf-8")
        os.chmod(path, 0o644)
    except OSError as exc:
        raise OnionError(f"could not write the session environment file: {exc}") from exc
    if not shutil.which("systemctl"):
        return
    assignments = [f"{key}={value}" for key, value in _session_assignments(port, hosts)]
    _run(["systemctl", "--user", "set-environment", *assignments])


def _session_assignments(port: int, hosts: list[str]) -> list[tuple[str, str]]:
    url = f"socks5h://{SOCKS_HOST}:{port}"
    no_proxy = ",".join(hosts)
    return [
        ("ALL_PROXY", url),
        ("all_proxy", url),
        ("SOCKS_PROXY", url),
        ("socks_proxy", url),
        ("NO_PROXY", no_proxy),
        ("no_proxy", no_proxy),
    ]


def _restore_environment(saved: dict) -> None:
    if not isinstance(saved, dict):
        return
    path = Path(str(saved.get("path") or environment_file()))
    if saved.get("existed") and isinstance(saved.get("text"), str):
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(saved["text"], encoding="utf-8")
            os.chmod(path, 0o644)
        except OSError as exc:
            raise OnionError(f"could not restore the session environment file: {exc}") from exc
    else:
        try:
            path.unlink()
        except OSError:
            pass
    session = saved.get("session") or {}
    if not isinstance(session, dict) or not session.get("available"):
        return
    if not shutil.which("systemctl"):
        return
    values = session.get("values") or {}
    if not isinstance(values, dict):
        return
    unset = [key for key in SESSION_KEYS if not values.get(key)]
    pairs = [f"{key}={values[key]}" for key in SESSION_KEYS if values.get(key)]
    if pairs:
        _run(["systemctl", "--user", "set-environment", *pairs])
    if unset:
        _run(["systemctl", "--user", "unset-environment", *unset])


def _unset_session_keys() -> None:
    if not shutil.which("systemctl"):
        return
    _run(["systemctl", "--user", "unset-environment", *SESSION_KEYS])


def _parse_environment(text: str) -> dict[str, str]:
    found: dict[str, str] = {}
    for line in text.splitlines():
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        found[key] = value
    return found


def _write_snapshot(payload: dict) -> None:
    path = snapshot_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    blob = json.dumps(payload, indent=2) + "\n"
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(blob, encoding="utf-8")
    os.chmod(temporary, 0o600)
    temporary.replace(path)


def _delete_snapshot() -> None:
    try:
        snapshot_path().unlink()
    except OSError:
        pass


def _run(argv: list[str], timeout: int = 8) -> subprocess.CompletedProcess[str] | None:
    command = os.path.basename(argv[0])
    if command in _FORBIDDEN or command.startswith("pvpn"):
        raise OnionError(f"refusing to run {command}")
    try:
        return subprocess.run(
            argv,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
