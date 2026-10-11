"""Point name lookup at Tor while the switch is on, and put it back after.

Ubuntu's /etc/resolv.conf is usually a symlink to systemd-resolved's stub.
That symlink stays in place. resolvectl is told to forward through Tor's
DNSPort. Disconnect restores the per-link DNS, domains, and default route
that were saved, then asks NetworkManager, systemd-resolved, and Tailscale
to publish their own settings again. resolvectl revert is not used: on
current systemd it clears those settings and leaves every link with no DNS.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

from onionfruitux.paths import DNS_PORT, DNS_SAVE_PATH, RESOLV_BACKUP_PATH, STATE_DIR

_RESOLV = Path("/etc/resolv.conf")
_DROPIN_DIRS = (
    Path("/etc/systemd/resolved.conf.d"),
    Path("/run/systemd/resolved.conf.d"),
)
_LINK_VALUE = re.compile(r"^Link\s+\d+\s+\(([^)]+)\):\s*(.*)$")


def engage_dns(links: list[str] | None = None) -> None:
    """Send lookups to Tor. A second call replaces the previous change."""
    if DNS_SAVE_PATH.exists():
        restore_dns()
    chosen = _up_links() if links is None else list(links)
    if shutil.which("resolvectl") and chosen and _use_resolved():
        snapshot = _snapshot(chosen)
        changed = [link for link in chosen if _point_link(link)]
        if changed:
            _write_save({"mode": "resolved", "links": changed, "snapshot": snapshot})
            _flush()
            return
    if _resolv_is_regular_file():
        _replace_resolv_conf()
        return
    _write_save({"mode": "none", "links": []})


def restore_dns() -> None:
    """Undo engage_dns. Safe when DNS was not changed.

    Also used by panic-off. When the saved snapshot is already gone, the
    NetworkManager, resolved, and Tailscale steps still run so a machine
    left with empty link DNS can get its resolver back.
    """
    saved: dict = {}
    if DNS_SAVE_PATH.exists():
        try:
            saved = json.loads(DNS_SAVE_PATH.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            saved = {}
        mode = saved.get("mode")
        if mode == "resolved" and shutil.which("resolvectl"):
            _restore_snapshot(saved.get("snapshot") or {}, saved.get("links") or [])
        elif mode == "file" and RESOLV_BACKUP_PATH.exists():
            try:
                _RESOLV.write_text(
                    RESOLV_BACKUP_PATH.read_text(encoding="utf-8"),
                    encoding="utf-8",
                )
            except OSError:
                pass
            try:
                RESOLV_BACKUP_PATH.unlink()
            except OSError:
                pass
        try:
            DNS_SAVE_PATH.unlink()
        except OSError:
            pass
    _reapply_network(list(saved.get("links") or []))
    _flush()


def _snapshot(links: list[str]) -> dict:
    per_link = {}
    for link in links:
        info: dict = {}
        dns = _query_words(link, "dns")
        if dns is not None:
            info["dns"] = dns
        domains = _query_words(link, "domain")
        if domains is not None:
            info["domains"] = domains
        route = _query_default_route(link)
        if route is not None:
            info["default_route"] = route
        per_link[link] = info
    return {"links": per_link, "dropins": _read_dropins()}


def _restore_snapshot(snapshot: dict, links: list) -> None:
    if not isinstance(snapshot, dict):
        snapshot = {}
    _restore_dropins(snapshot.get("dropins") or {})
    per_link = snapshot.get("links") or {}
    if not isinstance(per_link, dict):
        per_link = {}
    ordered = list(links) or list(per_link.keys())
    for link in ordered:
        info = per_link.get(link)
        if isinstance(info, dict):
            _restore_link(str(link), info)


def _restore_link(link: str, info: dict) -> None:
    if "dns" in info:
        servers = [str(item) for item in info.get("dns") or []]
        _run(["resolvectl", "dns", link, *(servers or [""])])
    if "domains" in info:
        domains = [str(item) for item in info.get("domains") or []]
        _run(["resolvectl", "domain", link, *(domains or [""])])
    route = info.get("default_route")
    if route in {"yes", "no"}:
        _run(["resolvectl", "default-route", link, route])


def _query_words(link: str, kind: str) -> list[str] | None:
    result = _run(["resolvectl", kind, link])
    if result is None or result.returncode != 0:
        return None
    matched = False
    value = ""
    for line in result.stdout.splitlines():
        match = _LINK_VALUE.match(line.strip())
        if match and match.group(1) == link:
            matched = True
            value = match.group(2).strip()
            break
    if not matched:
        if result.stdout.strip():
            return None
        return []
    if not value:
        return []
    return value.split()


def _query_default_route(link: str) -> str | None:
    result = _run(["resolvectl", "default-route", link])
    if result is None or result.returncode != 0:
        return None
    for line in result.stdout.splitlines():
        match = _LINK_VALUE.match(line.strip())
        if match and match.group(1) == link:
            value = match.group(2).strip().lower()
            if value in {"yes", "no"}:
                return value
            return None
    if result.stdout.strip():
        return None
    return None


def _read_dropins() -> dict[str, str]:
    found: dict[str, str] = {}
    for directory in _DROPIN_DIRS:
        if not directory.is_dir():
            continue
        for path in sorted(directory.glob("*.conf")):
            try:
                found[str(path)] = path.read_text(encoding="utf-8")
            except OSError:
                continue
    return found


def _restore_dropins(saved: dict) -> None:
    if not isinstance(saved, dict):
        return
    for name, content in saved.items():
        if not isinstance(content, str):
            continue
        path = Path(name)
        try:
            current = path.read_text(encoding="utf-8") if path.exists() else None
        except OSError:
            current = None
        if current == content:
            continue
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
            os.chmod(path, 0o644)
        except OSError:
            continue


def _reapply_network(links: list[str]) -> None:
    """Ask the network daemons to publish DNS again. Does not edit their config."""
    managed: set[str] = set()
    if shutil.which("nmcli"):
        listed = _run(["nmcli", "-t", "-f", "DEVICE,STATE", "device"], timeout=15)
        if listed and listed.returncode == 0:
            for line in listed.stdout.splitlines():
                if ":" not in line:
                    continue
                device, state = line.split(":", 1)
                device = device.strip()
                state = state.strip()
                if not device or device == "lo":
                    continue
                if not state.startswith("connected"):
                    continue
                outcome = _run(["nmcli", "device", "reapply", device], timeout=20)
                if outcome and outcome.returncode == 0:
                    managed.add(device)
    elif shutil.which("networkctl"):
        for link in links:
            if link in {"lo", "tailscale0"}:
                continue
            _run(["networkctl", "reconfigure", link], timeout=20)
    _nudge_tailscale()
    if shutil.which("systemctl"):
        reloaded = _run(["systemctl", "reload", "systemd-resolved"], timeout=15)
        if reloaded is None or reloaded.returncode != 0:
            _run(["systemctl", "try-reload-or-restart", "systemd-resolved"], timeout=20)


def _nudge_tailscale() -> None:
    if not shutil.which("tailscale"):
        return
    if not Path("/sys/class/net/tailscale0").exists():
        active = _run(["systemctl", "is-active", "--quiet", "tailscaled"], timeout=10)
        if active is None or active.returncode != 0:
            return
    accept = True
    prefs = _run(["tailscale", "debug", "prefs"], timeout=10)
    if prefs and prefs.returncode == 0 and prefs.stdout.strip():
        try:
            data = json.loads(prefs.stdout)
        except json.JSONDecodeError:
            data = {}
        if isinstance(data, dict) and "CorpDNS" in data:
            accept = bool(data["CorpDNS"])
    if accept:
        _run(["tailscale", "set", "--accept-dns=true"], timeout=20)


def _use_resolved() -> bool:
    try:
        text = _RESOLV.read_text(encoding="utf-8", errors="replace")
    except OSError:
        text = ""
    if "127.0.0.53" in text:
        return True
    try:
        target = os.path.realpath(_RESOLV)
    except OSError:
        return False
    return "systemd/resolve" in target


def _resolv_is_regular_file() -> bool:
    return _RESOLV.is_file() and not _RESOLV.is_symlink()


def _replace_resolv_conf() -> None:
    try:
        original = _RESOLV.read_text(encoding="utf-8")
    except OSError:
        return
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    RESOLV_BACKUP_PATH.write_text(original, encoding="utf-8")
    os.chmod(RESOLV_BACKUP_PATH, 0o644)
    _write_save({"mode": "file", "links": []})
    _RESOLV.write_text(
        "# Written by OnionFruitux. Removed when the switch turns off.\n"
        "nameserver 127.0.0.1\n",
        encoding="utf-8",
    )


def _point_link(link: str) -> bool:
    for server in (f"127.0.0.1:{DNS_PORT}", "127.0.0.1"):
        result = _run(["resolvectl", "dns", link, server])
        if result is not None and result.returncode == 0:
            _run(["resolvectl", "domain", link, "~."])
            return True
    return False


def _up_links() -> list[str]:
    root = Path("/sys/class/net")
    names = []
    try:
        entries = list(root.iterdir())
    except OSError:
        return names
    for entry in entries:
        if entry.name == "lo":
            continue
        try:
            state = (entry / "operstate").read_text(encoding="utf-8").strip()
        except OSError:
            continue
        if state in {"up", "unknown"}:
            names.append(entry.name)
    return sorted(names)


def _write_save(payload: dict) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    DNS_SAVE_PATH.write_text(json.dumps(payload) + "\n", encoding="utf-8")
    os.chmod(DNS_SAVE_PATH, 0o644)


def _flush() -> None:
    if shutil.which("resolvectl"):
        _run(["resolvectl", "flush-caches"])


def _run(argv: list[str], timeout: int = 5) -> subprocess.CompletedProcess[str] | None:
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
