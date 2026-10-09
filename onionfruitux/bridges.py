"""Bridge lines and the programs that carry them onto the Tor network."""

from __future__ import annotations

import json
import shutil
from functools import lru_cache
from pathlib import Path

from onionfruitux.config import BRIDGE_TYPES, Config, Route

_DATA = Path(__file__).resolve().parent / "data"
_PT_CONFIG = _DATA / "pt_config.json"

# Tor Browser now ships Snowflake inside lyrebird. Older packages still
# install a separate snowflake-client or obfs4proxy binary.
_EXECUTABLES = {
    "obfs4": ("lyrebird", "obfs4proxy"),
    "meek": ("lyrebird",),
    "webtunnel": ("lyrebird",),
    "snowflake": ("lyrebird", "snowflake-client"),
    "conjure": ("conjure-client",),
}


@lru_cache(maxsize=1)
def load_pt_config() -> dict:
    return json.loads(_PT_CONFIG.read_text(encoding="utf-8"))


def pt_config_source() -> str:
    path = _DATA / "PT_CONFIG_SOURCE.txt"
    if not path.exists():
        return ""
    for line in path.read_text(encoding="utf-8").splitlines():
        text = line.strip()
        if text.startswith("http://") or text.startswith("https://"):
            return text
    return ""


def parse_bridge_lines(text: str) -> list[str]:
    lines: list[str] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.lower().startswith("bridge "):
            line = line[7:].strip()
        if not line:
            continue
        if any(ord(ch) < 32 for ch in line):
            raise ValueError("a bridge line cannot contain a new line or control character")
        lines.append(line)
    return lines


def default_bridge_lines(kind: str) -> list[str]:
    """Published defaults for obfs4, snowflake, and meek. Other kinds have none."""
    key = {"obfs4": "obfs4", "snowflake": "snowflake", "meek": "meek"}.get(kind)
    if key is None:
        return []
    bridges = load_pt_config().get("bridges") or {}
    found = bridges.get(key) or []
    return [str(line) for line in found]


def lines_for_route(cfg: Config, route: Route) -> list[str]:
    if not route.bridge:
        return []
    custom = parse_bridge_lines(cfg.bridge_lines.get(route.bridge, ""))
    if custom:
        return custom
    return default_bridge_lines(route.bridge)


def transport_executable(kind: str) -> str | None:
    """Absolute path of the transport program, when this machine has one."""
    if kind not in _EXECUTABLES:
        return None
    for name in _EXECUTABLES[kind]:
        found = shutil.which(name)
        if found:
            return found
    return None


def preferred_executable(kind: str) -> str:
    """Path written into torrc. A missing program stays as /usr/bin/<name>."""
    found = transport_executable(kind)
    if found:
        return found
    names = _EXECUTABLES.get(kind) or ()
    if not names:
        return ""
    return f"/usr/bin/{names[0]}"


def transport_names(lines: list[str]) -> list[str]:
    names: list[str] = []
    for line in lines:
        token = line.split(None, 1)[0]
        if token and token not in names and not _looks_like_address(token):
            names.append(token)
    return names


def conjure_arguments() -> str:
    raw = str((load_pt_config().get("pluggableTransports") or {}).get("conjure") or "")
    marker = "-registerURL"
    if marker in raw:
        return raw[raw.index(marker) :].strip()
    return "-registerURL https://registration.refraction.network/api"


def plugin_lines(kind: str, bridge_lines: list[str]) -> list[str]:
    if kind == "plain" or kind not in BRIDGE_TYPES:
        return []
    executable = preferred_executable(kind)
    if not executable:
        return []
    names = transport_names(bridge_lines) or [_default_transport_name(kind)]
    suffix = ""
    if kind == "conjure":
        suffix = " " + conjure_arguments()
    return [
        f"ClientTransportPlugin {name} exec {executable}{suffix}" for name in names
    ]


def _default_transport_name(kind: str) -> str:
    if kind == "meek":
        return "meek_lite"
    return kind


def _looks_like_address(token: str) -> bool:
    return ":" in token or token[:1].isdigit()
