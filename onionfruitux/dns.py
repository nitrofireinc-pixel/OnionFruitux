"""Point name lookup at Tor while the switch is on, and put it back after.

Ubuntu's /etc/resolv.conf is usually a symlink to systemd-resolved's stub.
That symlink stays in place. resolvectl is told to forward through Tor's
DNSPort, and disconnect restores the previous per-link settings.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

from onionfruitux.paths import DNS_PORT, DNS_SAVE_PATH, RESOLV_BACKUP_PATH, STATE_DIR

_RESOLV = Path("/etc/resolv.conf")


def engage_dns(links: list[str] | None = None) -> None:
    """Send lookups to Tor. A second call replaces the previous change."""
    if DNS_SAVE_PATH.exists():
        restore_dns()
    chosen = _up_links() if links is None else list(links)
    if shutil.which("resolvectl") and chosen and _use_resolved():
        changed = [link for link in chosen if _point_link(link)]
        if changed:
            _write_save({"mode": "resolved", "links": changed})
            _flush()
            return
    if _resolv_is_regular_file():
        _replace_resolv_conf()
        return
    _write_save({"mode": "none", "links": []})


def restore_dns() -> None:
    """Undo engage_dns. Safe when DNS was not changed."""
    if not DNS_SAVE_PATH.exists():
        return
    try:
        saved = json.loads(DNS_SAVE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        saved = {}
    mode = saved.get("mode")
    if mode == "resolved" and shutil.which("resolvectl"):
        for link in saved.get("links") or []:
            _run(["resolvectl", "revert", str(link)])
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


def _run(argv: list[str]) -> subprocess.CompletedProcess[str] | None:
    try:
        return subprocess.run(
            argv,
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
