"""Named routes and the settings the switch uses."""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path

from onionfruitux.countries import normalize_country
from onionfruitux.errors import OnionError
from onionfruitux.paths import config_dir, config_path

BRIDGE_TYPES = ("plain", "obfs4", "snowflake", "meek", "webtunnel", "conjure")

_BRIDGE_ALIASES = {
    "plain": "plain",
    "vanilla": "plain",
    "none": "",
    "obfs4": "obfs4",
    "snowflake": "snowflake",
    "meek": "meek",
    "meek_lite": "meek",
    "webtunnel": "webtunnel",
    "web-tunnel": "webtunnel",
    "web_tunnel": "webtunnel",
    "conjure": "conjure",
}


def normalize_bridge(value: str) -> str:
    text = value.strip().lower().replace(" ", "")
    if not text:
        return ""
    if text not in _BRIDGE_ALIASES:
        known = ", ".join(BRIDGE_TYPES)
        raise ValueError(f"unknown bridge {value!r}; choose one of: {known}")
    return _BRIDGE_ALIASES[text]


def parse_relay_ports(value: str) -> list[int]:
    """Parse a list such as ``80, 443`` into port numbers."""
    text = value.strip()
    if not text:
        return []
    ports: list[int] = []
    for part in text.replace(";", ",").split(","):
        item = part.strip()
        if not item:
            continue
        if ":" in item:
            item = item.rsplit(":", 1)[-1].strip()
        if not item.isdigit():
            raise ValueError(f"relay port {part.strip()!r} is not a port number")
        port = int(item)
        if port < 1 or port > 65535:
            raise ValueError(f"relay port {port} is outside 1-65535")
        if port not in ports:
            ports.append(port)
    return ports


@dataclass
class Route:
    name: str
    entry: str = ""
    exit: str = ""
    bridge: str = ""
    strict: bool = False

    def cleaned(self) -> Route:
        name = " ".join(self.name.split())
        if not name:
            raise ValueError("a route needs a name")
        if any(ord(ch) < 32 for ch in name):
            raise ValueError("a route name cannot contain a new line")
        return Route(
            name=name,
            entry=normalize_country(self.entry),
            exit=normalize_country(self.exit),
            bridge=normalize_bridge(self.bridge),
            strict=bool(self.strict),
        )


@dataclass
class NetworkSettings:
    lan_direct: bool = True
    reject_non_tor: bool = True
    relay_ports: str = ""
    open_check_page: bool = True

    def cleaned(self) -> NetworkSettings:
        ports = parse_relay_ports(self.relay_ports)
        shown = ", ".join(str(port) for port in ports)
        return NetworkSettings(
            lan_direct=bool(self.lan_direct),
            reject_non_tor=bool(self.reject_non_tor),
            relay_ports=shown,
            open_check_page=bool(self.open_check_page),
        )


@dataclass
class Config:
    routes: list[Route] = field(default_factory=list)
    active_route: str = ""
    bridge_lines: dict[str, str] = field(default_factory=dict)
    network: NetworkSettings = field(default_factory=NetworkSettings)

    def route_named(self, name: str) -> Route | None:
        for route in self.routes:
            if route.name == name:
                return route
        return None

    def current_route(self) -> Route:
        found = self.route_named(self.active_route)
        if found is not None:
            return found
        return Route(name="")


def load_config(path: Path | None = None) -> Config:
    target = Path(path) if path else config_path()
    if not target.exists():
        return Config()
    try:
        raw = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise OnionError(f"cannot read {target}: {exc}") from exc
    if not isinstance(raw, dict):
        raise OnionError(f"{target} is not an OnionFruitux settings file")
    try:
        routes = [_route_from(item) for item in raw.get("routes") or []]
    except (TypeError, ValueError) as exc:
        raise OnionError(f"{target}: {exc}") from exc
    net_raw = raw.get("network") or {}
    if not isinstance(net_raw, dict):
        raise OnionError(f"{target}: network settings are not an object")
    try:
        network = NetworkSettings(
            lan_direct=bool(net_raw.get("lan_direct", True)),
            reject_non_tor=bool(net_raw.get("reject_non_tor", True)),
            relay_ports=str(net_raw.get("relay_ports") or ""),
            open_check_page=bool(net_raw.get("open_check_page", True)),
        ).cleaned()
    except ValueError as exc:
        raise OnionError(f"{target}: {exc}") from exc
    lines_raw = raw.get("bridge_lines") or {}
    if not isinstance(lines_raw, dict):
        raise OnionError(f"{target}: bridge lines are not an object")
    bridge_lines = {str(key): str(value) for key, value in lines_raw.items()}
    active = str(raw.get("active_route") or "")
    return Config(
        routes=routes,
        active_route=active,
        bridge_lines=bridge_lines,
        network=network,
    )


def save_config(cfg: Config, path: Path | None = None) -> Path:
    target = Path(path) if path else config_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "routes": [asdict(route.cleaned()) for route in cfg.routes],
        "active_route": cfg.active_route,
        "bridge_lines": cfg.bridge_lines,
        "network": asdict(cfg.network.cleaned()),
    }
    blob = json.dumps(payload, indent=2) + "\n"
    temporary = target.with_suffix(".json.tmp")
    temporary.write_text(blob, encoding="utf-8")
    os.chmod(temporary, 0o600)
    temporary.replace(target)
    return target


def upsert_route(cfg: Config, route: Route) -> Route:
    cleaned = route.cleaned()
    replaced = False
    updated: list[Route] = []
    for current in cfg.routes:
        if current.name == cleaned.name:
            updated.append(cleaned)
            replaced = True
        else:
            updated.append(current)
    if not replaced:
        updated.append(cleaned)
    cfg.routes = updated
    return cleaned


def remove_route(cfg: Config, name: str) -> None:
    found = cfg.route_named(name)
    if found is None:
        raise OnionError(f"there is no route named {name!r}")
    cfg.routes = [route for route in cfg.routes if route.name != name]
    if cfg.active_route == name:
        cfg.active_route = ""


def use_route(cfg: Config, name: str) -> Route:
    found = cfg.route_named(name)
    if found is None:
        raise OnionError(f"there is no route named {name!r}")
    cfg.active_route = found.name
    return found


def _route_from(item: object) -> Route:
    if not isinstance(item, dict):
        raise ValueError("a route entry is not an object")
    return Route(
        name=str(item.get("name") or ""),
        entry=str(item.get("entry") or ""),
        exit=str(item.get("exit") or ""),
        bridge=str(item.get("bridge") or ""),
        strict=bool(item.get("strict", False)),
    ).cleaned()
