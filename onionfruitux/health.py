"""Checks that run after the switch moves. They do not change settings."""

from __future__ import annotations

import json
import shutil
import socket
import ssl
import subprocess
from dataclasses import dataclass

from onionfruitux.errors import OnionError

CHECK_HOST = "check.torproject.org"
CHECK_PATH = "/api/ip"
DIRECT_HOSTS = ("1.1.1.1", "8.8.8.8")
DNS_NAMES = ("one.one.one.one", "dns.google")


@dataclass
class DirectReport:
    ok: bool
    lines: list[str]

    def text(self) -> str:
        return "\n".join(self.lines)


def verify_tor(port: int, timeout: float = 20) -> dict:
    """Fetch the Tor check API through the SOCKS port. Raise if it is not Tor."""
    body = fetch_through_socks("127.0.0.1", port, CHECK_HOST, 443, CHECK_PATH, timeout)
    return interpret_tor_check(body)


def interpret_tor_check(body: str) -> dict:
    try:
        data = json.loads(body)
    except json.JSONDecodeError as exc:
        raise OnionError(
            "The Tor check did not return the expected answer, so the proxy was not left on."
        ) from exc
    if not isinstance(data, dict) or not data.get("IsTor"):
        raise OnionError(
            "A request through the SOCKS proxy did not come from Tor, "
            "so the previous proxy settings were put back."
        )
    return data


def fetch_through_socks(
    proxy_host: str,
    proxy_port: int,
    dest_host: str,
    dest_port: int,
    path: str,
    timeout: float,
) -> str:
    """HTTPS GET through a SOCKS5 proxy. DNS is resolved by the proxy."""
    try:
        sock = socks5_connect(proxy_host, proxy_port, dest_host, dest_port, timeout)
    except OSError as exc:
        raise OnionError(f"Could not connect to the Tor SOCKS proxy: {exc}") from exc
    try:
        return tls_http_get(sock, dest_host, path, timeout)
    finally:
        sock.close()


def socks5_connect(
    proxy_host: str,
    proxy_port: int,
    dest_host: str,
    dest_port: int,
    timeout: float,
) -> socket.socket:
    """Open a SOCKS5 connection. The destination is a hostname, so Tor resolves it."""
    sock = socket.create_connection((proxy_host, proxy_port), timeout)
    sock.settimeout(timeout)
    sock.sendall(b"\x05\x01\x00")
    greeting = _recv_exact(sock, 2)
    if greeting != b"\x05\x00":
        sock.close()
        raise OnionError("Tor's SOCKS port refused a no-authentication greeting")
    host = dest_host.encode("idna")
    if len(host) > 255:
        sock.close()
        raise OnionError("the Tor check hostname is too long")
    request = b"\x05\x01\x00\x03" + bytes([len(host)]) + host + dest_port.to_bytes(2, "big")
    sock.sendall(request)
    head = _recv_exact(sock, 4)
    if head[1] != 0:
        sock.close()
        raise OnionError("Tor's SOCKS port could not open the Tor check connection")
    _discard_socks_address(sock, head[3])
    return sock


def tls_http_get(sock: socket.socket, host: str, path: str, timeout: float) -> str:
    context = ssl.create_default_context()
    wrapped = context.wrap_socket(sock, server_hostname=host)
    wrapped.settimeout(timeout)
    request = (
        f"GET {path} HTTP/1.1\r\n"
        f"Host: {host}\r\n"
        "User-Agent: OnionFruitux\r\n"
        "Accept: application/json\r\n"
        "Connection: close\r\n"
        "\r\n"
    )
    wrapped.sendall(request.encode("ascii"))
    chunks: list[bytes] = []
    while True:
        try:
            piece = wrapped.recv(4096)
        except socket.timeout as exc:
            raise OnionError("The Tor check timed out") from exc
        if not piece:
            break
        chunks.append(piece)
    raw = b"".join(chunks)
    return _http_body(raw)


def verify_direct(timeout: float = 3) -> DirectReport:
    """Confirm the machine can reach the internet without a proxy.

    An address counts as reachable when a TCP connection to port 443 works or
    ping gets a reply. Both public addresses must be reachable, and a name
    must resolve.
    """
    lines: list[str] = []
    ok = True
    for host in DIRECT_HOSTS:
        tcp_ok = tcp_connect(host, 443, timeout)
        ping_ok = ping(host, timeout)
        if tcp_ok or ping_ok:
            parts = []
            if tcp_ok:
                parts.append("TCP connection succeeded")
            if ping_ok:
                parts.append("ping succeeded")
            lines.append(f"{host}: " + ", ".join(parts))
        else:
            ok = False
            lines.append(f"{host}: no TCP connection and no ping reply")
    dns_ok, detail = resolve_dns()
    lines.append(detail)
    if not dns_ok:
        ok = False
    return DirectReport(ok=ok, lines=lines)


def tcp_connect(host: str, port: int, timeout: float) -> bool:
    try:
        sock = socket.create_connection((host, port), timeout)
    except OSError:
        return False
    sock.close()
    return True


def ping(host: str, timeout: float) -> bool:
    if not shutil.which("ping"):
        return False
    wait = max(1, int(timeout))
    try:
        result = subprocess.run(
            ["ping", "-c", "1", "-W", str(wait), host],
            capture_output=True,
            text=True,
            timeout=timeout + 2,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0


def resolve_dns() -> tuple[bool, str]:
    errors: list[str] = []
    for name in DNS_NAMES:
        try:
            socket.getaddrinfo(name, 443, type=socket.SOCK_STREAM)
        except OSError as exc:
            errors.append(f"{name} ({exc})")
            continue
        return True, f"DNS: resolved {name}"
    detail = "; ".join(errors) if errors else "no names were tried"
    return False, f"DNS: could not resolve a name ({detail})"


def resolv_conf_hint() -> str:
    """Read-only description of /etc/resolv.conf. Does not change the file."""
    try:
        text = open("/etc/resolv.conf", encoding="utf-8", errors="replace").read()
    except OSError:
        return ""
    servers = [
        line.strip()
        for line in text.splitlines()
        if line.strip().startswith("nameserver")
    ]
    if not servers:
        return "/etc/resolv.conf has no nameserver line."
    return "/etc/resolv.conf: " + "; ".join(servers)


def leftover_firewall_hint() -> str:
    """Read-only. Says so when an older OnionFruitux table is still installed."""
    if not shutil.which("nft"):
        return ""
    try:
        result = subprocess.run(
            ["nft", "list", "table", "inet", "onionfruitux"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    if result.returncode != 0:
        return ""
    return (
        "The old onionfruitux firewall table is still installed. "
        "onionfruitux panic-off deletes that table only and does not change VPN connections."
    )


def _discard_socks_address(sock: socket.socket, atyp: int) -> None:
    if atyp == 1:
        _recv_exact(sock, 6)
    elif atyp == 4:
        _recv_exact(sock, 18)
    elif atyp == 3:
        length = _recv_exact(sock, 1)[0]
        _recv_exact(sock, length + 2)
    else:
        sock.close()
        raise OnionError("Tor's SOCKS reply could not be read")


def _recv_exact(sock: socket.socket, count: int) -> bytes:
    chunks = []
    remaining = count
    while remaining:
        piece = sock.recv(remaining)
        if not piece:
            raise OnionError("Tor's SOCKS port closed the connection")
        chunks.append(piece)
        remaining -= len(piece)
    return b"".join(chunks)


def _http_body(raw: bytes) -> str:
    text = raw.decode("utf-8", errors="replace")
    if "\r\n\r\n" not in text:
        raise OnionError("The Tor check did not return an HTTP response")
    header, body = text.split("\r\n\r\n", 1)
    status = header.splitlines()[0] if header else ""
    if " 200 " not in f" {status} ":
        raise OnionError(f"The Tor check failed ({status or 'empty response'})")
    return body.strip()
