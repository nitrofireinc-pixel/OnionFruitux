"""Put back a DNS snapshot left by an older OnionFruitux.

Current versions do not change resolv.conf, systemd-resolved, or any VPN.
If a previous version wrote dns.json, disconnect and panic-off put those
saved values back and then delete the file. NetworkManager is not asked to
reapply a connection, and Tailscale is not reconfigured.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

from onionfruitux.paths import DNS_SAVE_PATH, RESOLV_BACKUP_PATH

_RESOLV = Path("/etc/resolv.conf")


def restore_saved_dns() -> None:
    """Undo a DNS change saved by an older version. Safe when nothing was saved."""
    if not DNS_SAVE_PATH.exists():
        return
    try:
        saved = json.loads(DNS_SAVE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        saved = {}
    if not isinstance(saved, dict):
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
    _flush()


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


def _flush() -> None:
    if shutil.which("resolvectl"):
        _run(["resolvectl", "flush-caches"])


def _run(argv: list[str], timeout: int = 5) -> subprocess.CompletedProcess[str] | None:
    command = os.path.basename(argv[0])
    # Restoring a saved resolver must not reconfigure NetworkManager or a VPN.
    if command in {"nmcli", "nmtui", "networkctl", "tailscale", "nft", "iptables"}:
        raise RuntimeError(f"refusing to run {command}")
    if command.startswith("pvpn") or command.startswith("proton"):
        raise RuntimeError(f"refusing to run {command}")
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
