"""Port choice and the rule that nothing connects to TransPort or DNSPort."""

from __future__ import annotations

import os
import socket
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from onionfruitux import system
from onionfruitux.doctor import _port_checks, doctor_status, format_report
from onionfruitux.listeners import describe_listeners, parse_proc_net
from onionfruitux.paths import CONTROL_PORT, DNS_PORT, SOCKS_PORT, TRANS_PORT
from onionfruitux.preflight import validate_for_connect
from onionfruitux.config import Config


_TCP = """  sl  local_address rem_address   st tx_queue rx_queue tr tm->when retrnsmt   uid  timeout inode
   0: 0100007F:23B4 00000000:0000 0A 00000000:00000000 00:00000000 00000000  1000        0 12345 1 0000000000000000 100 0 0 10 0
   1: 0100007F:0050 0100007F:23B4 01 00000000:00000000 00:00000000 00000000  1000        0 99999 1 0000000000000000 100 0 0 10 0
"""


class PortParseTests(unittest.TestCase):
    def test_listen_row_is_kept_and_an_established_row_is_not(self):
        self.assertEqual(parse_proc_net(_TCP, 9140, tcp=True), [("12345", 1000)])

    def test_constants_avoid_system_tor_and_mdns(self):
        self.assertEqual((TRANS_PORT, DNS_PORT, SOCKS_PORT, CONTROL_PORT), (9140, 9153, 9155, 9156))
        self.assertNotIn(TRANS_PORT, (9040, 9050, 9150))
        self.assertNotIn(DNS_PORT, (53, 5353))
        self.assertNotIn(SOCKS_PORT, (9050, 9150))
        self.assertNotIn(CONTROL_PORT, (9051, 9151))

    def test_a_live_socket_names_this_process(self):
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.bind(("127.0.0.1", 0))
        sock.listen(1)
        port = sock.getsockname()[1]
        try:
            found = describe_listeners(port, ignore_our_tor=False)
        finally:
            sock.close()
        self.assertTrue(any(str(os.getpid()) in item for item in found), found)


class ProbeTests(unittest.TestCase):
    def test_listener_wait_never_opens_a_socket(self):
        def boom(address, timeout=None):
            raise AssertionError(f"opened a socket to {address}")

        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / "notice.log"
            log.write_text(
                "Opened Transparent pf/netfilter listener on 127.0.0.1:9140\n",
                encoding="utf-8",
            )
            with patch.object(system, "LOG_PATH", log), \
                 patch.object(system.socket, "create_connection", side_effect=boom):
                self.assertTrue(system._wait_for_listeners(1))

    def test_bootstrap_uses_the_control_port_only(self):
        seen = []

        class FakeFile:
            def __init__(self, lines):
                self.lines = list(lines)

            def write(self, data):
                return len(data)

            def readline(self):
                if not self.lines:
                    return b""
                return self.lines.pop(0)

        class FakeSock:
            def __init__(self):
                self.file = FakeFile(
                    [
                        b"250 OK\r\n",
                        b"250-status/bootstrap-phase=NOTICE BOOTSTRAP "
                        b"PROGRESS=100 TAG=done "
                        b'SUMMARY="Done"\r\n',
                        b"250 OK\r\n",
                    ]
                )

            def makefile(self, mode="rwb", buffering=0):
                return self.file

            def close(self):
                pass

        def connect(address, timeout=None):
            seen.append(address[1])
            if address[1] in (TRANS_PORT, DNS_PORT):
                raise AssertionError(f"probed {address[1]}")
            return FakeSock()

        with tempfile.TemporaryDirectory() as tmp:
            cookie = Path(tmp) / "cookie"
            cookie.write_bytes(b"abc")
            log = Path(tmp) / "notice.log"
            log.write_text("", encoding="utf-8")
            messages = []
            with patch.object(system, "COOKIE_PATH", cookie), \
                 patch.object(system, "LOG_PATH", log), \
                 patch.object(system.socket, "create_connection", side_effect=connect):
                self.assertEqual(
                    system._wait_bootstrap(2, progress=messages.append),
                    100,
                )
        self.assertEqual(seen, [CONTROL_PORT])
        self.assertEqual(messages, ["Tor bootstrap 100% (Done)…"])

    def test_source_never_connects_to_trans_or_dns_port(self):
        text = Path("onionfruitux/system.py").read_text(encoding="utf-8")
        self.assertNotIn("def _wait_port", text)
        for line in text.splitlines():
            if "create_connection" not in line:
                continue
            self.assertNotIn("TRANS_PORT", line)
            self.assertNotIn("DNS_PORT", line)
            self.assertIn("CONTROL_PORT", line)


class ConflictTests(unittest.TestCase):
    def test_preflight_reports_the_process_holding_the_port(self):
        message = (
            "OnionFruitux's SOCKS port 9155 is already in use by "
            "tor (pid 9) running as debian-tor. The firewall was not changed."
        )
        with patch("onionfruitux.preflight.our_port_conflicts", return_value=[message]):
            errors = validate_for_connect(Config())
        self.assertIn(message, errors)

    def test_doctor_warns_about_system_tor_without_failing(self):
        with patch("onionfruitux.doctor.our_port_conflicts", return_value=[]), \
             patch(
                 "onionfruitux.doctor.distro_tor_listeners",
                 return_value=["tor (pid 3) running as debian-tor"],
             ):
            checks = _port_checks()
        self.assertEqual(doctor_status(checks), 0)
        text = format_report(checks)
        self.assertIn("9050", text)
        self.assertIn("does not stop", text)
        self.assertIn("debian-tor", text)

    def test_doctor_fails_when_our_port_is_taken(self):
        with patch(
            "onionfruitux.doctor.our_port_conflicts",
            return_value=["OnionFruitux's DNS port 9153 is already in use by avahi-daemon (pid 4)"],
        ), patch("onionfruitux.doctor.distro_tor_listeners", return_value=[]):
            checks = _port_checks()
        self.assertEqual(doctor_status(checks), 1)
        self.assertIn("9153", format_report(checks))
        self.assertIn("avahi-daemon", format_report(checks))
