"""Torrc text. Nothing here is installed or applied to the network."""

from __future__ import annotations

import subprocess
import unittest
from pathlib import Path

from onionfruitux.config import Config, Route
from onionfruitux.torrc import render_torrc


def _route(**kwargs) -> Config:
    route = Route(name="test", **kwargs).cleaned()
    return Config(routes=[route], active_route=route.name)


class TorrcTests(unittest.TestCase):
    def test_countries_and_strict(self):
        cfg = _route(entry="de", exit="us", strict=True)
        text = render_torrc(cfg)
        self.assertIn("EntryNodes {de}", text)
        self.assertIn("ExitNodes {us}", text)
        self.assertIn("StrictNodes 1", text)
        self.assertNotIn("UseBridges", text)
        self.assertNotIn("TransPort", text)
        self.assertNotIn("DNSPort", text)
        self.assertNotIn("VirtualAddrNetwork", text)
        self.assertNotIn("AutomapHostsOnResolve", text)
        self.assertIn("SocksPort 127.0.0.1:9155", text)
        self.assertIn("ControlPort 127.0.0.1:9156", text)
        self.assertNotIn(":9040", text)
        self.assertNotIn(":5353", text)
        self.assertNotIn(":9050", text)
        self.assertNotIn(":9051", text)
        self.assertIn("User onionfruitux", text)

    def test_bridge_replaces_entry_country(self):
        cfg = _route(entry="de", exit="us", bridge="snowflake")
        text = render_torrc(cfg)
        self.assertIn("UseBridges 1", text)
        self.assertNotIn("EntryNodes", text)
        self.assertIn("ExitNodes {us}", text)
        self.assertIn("Bridge snowflake ", text)
        self.assertIn("ClientTransportPlugin snowflake exec ", text)

    def test_custom_bridge_lines_win(self):
        cfg = _route(bridge="obfs4")
        cfg.bridge_lines["obfs4"] = "obfs4 192.0.2.10:443 FINGERPRINT cert=abc iat-mode=0\n"
        text = render_torrc(cfg)
        self.assertIn("Bridge obfs4 192.0.2.10:443 FINGERPRINT", text)
        self.assertNotIn("37.218.245.14", text)

    def test_plain_bridge_has_no_plugin(self):
        cfg = _route(bridge="plain")
        cfg.bridge_lines["plain"] = "Bridge 192.0.2.10:443 FINGERPRINT\n"
        text = render_torrc(cfg)
        self.assertIn("Bridge 192.0.2.10:443 FINGERPRINT", text)
        self.assertNotIn("ClientTransportPlugin", text)

    def test_meek_uses_published_meek_lite_line(self):
        cfg = _route(bridge="meek")
        text = render_torrc(cfg)
        self.assertIn("ClientTransportPlugin meek_lite exec ", text)
        self.assertIn("Bridge meek_lite ", text)

    def test_relay_ports(self):
        cfg = _route(exit="nl")
        cfg.network.relay_ports = "80, 443"
        text = render_torrc(cfg)
        self.assertIn("FascistFirewall 1", text)
        self.assertIn("ReachableAddresses *:80,*:443", text)

    def test_socks_only_without_a_route(self):
        text = render_torrc(Config())
        self.assertIn("SocksPort 127.0.0.1:9155", text)
        self.assertNotIn("TransPort", text)
        self.assertNotIn("DNSPort", text)
        self.assertNotIn("EntryNodes", text)
        self.assertNotIn("UseBridges", text)


class ManPageTests(unittest.TestCase):
    def test_manual_renders(self):
        manual = Path("share/man/onionfruitux.1")
        self.assertTrue(manual.is_file())
        text = manual.read_text(encoding="utf-8")
        for phrase in (
            "onionfruitux connect",
            "new\\-circuit",
            "boot enable",
            "check.torproject.org",
            "/usr/share/man/man1/onionfruitux.1.gz",
            "pt_config.json",
        ):
            self.assertIn(phrase, text)
        rendered = subprocess.run(
            ["groff", "-man", "-Tutf8", "-z", str(manual)],
            capture_output=True,
            text=True,
        )
        self.assertEqual(rendered.returncode, 0, rendered.stderr)
        self.assertNotIn("warning:", rendered.stderr.lower())

    def test_installer_ships_the_manual(self):
        install = Path("install.sh").read_text(encoding="utf-8")
        uninstall = Path("uninstall.sh").read_text(encoding="utf-8")
        self.assertIn("install_man", install)
        self.assertIn("/usr/share/man/man1/onionfruitux.1", install)
        self.assertIn("mandb", install)
        self.assertIn("/usr/share/man/man1/onionfruitux.1.gz", uninstall)
        self.assertIn("/usr/share/man/man1/onionfruitux.1", uninstall)
        for manager in ("apt-get", "dnf", "pacman", "zypper"):
            self.assertIn(manager, install)
        apt_block = install.split("install_packages()", 1)[1].split("apt)", 1)[1].split("dnf)", 1)[0]
        self.assertNotIn("pkexec polkit", apt_block)
        self.assertIn("polkitd", apt_block)
        self.assertIn("policykit-1", apt_block)
        self.assertNotIn("systemctl enable", install)
        self.assertNotIn("systemctl start", install)
        unit = Path("share/onionfruitux.service").read_text(encoding="utf-8")
        from onionfruitux.system import UNIT_TEXT

        self.assertEqual(unit.strip(), UNIT_TEXT.strip())
        self.assertIn("ExecStart=/usr/bin/onionfruitux --system connect", unit)
        self.assertNotIn("systemctl start", unit)
