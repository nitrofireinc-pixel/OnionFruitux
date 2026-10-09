"""Torrc, firewall text, and nft -c. Nothing here is installed."""

from __future__ import annotations

import getpass
import subprocess
import unittest
from pathlib import Path

from onionfruitux.config import Config, NetworkSettings, Route
from onionfruitux.firewall import render_firewall
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
        self.assertIn("TransPort 127.0.0.1:9040", text)
        self.assertIn("DNSPort 127.0.0.1:5353", text)
        self.assertIn("AutomapHostsOnResolve 1", text)
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

    def test_onion_mapping_without_a_route(self):
        text = render_torrc(Config())
        self.assertIn("VirtualAddrNetworkIPv4 10.192.0.0/10", text)
        self.assertNotIn("EntryNodes", text)
        self.assertNotIn("UseBridges", text)


class FirewallTests(unittest.TestCase):
    def test_dns_is_before_the_local_network(self):
        text = render_firewall(NetworkSettings())
        self.assertLess(text.index("udp dport 53 redirect"), text.index("192.168.0.0/16"))
        self.assertLess(text.index("tcp dport 53 redirect"), text.index('oifname "lo" return'))
        self.assertIn("table inet onionfruitux {", text)
        self.assertIn("meta skuid onionfruitux return", text)
        self.assertIn("udp dport { 67, 68, 546, 547 } return", text)
        self.assertIn("meta l4proto tcp redirect to :9040", text)
        self.assertIn("meta l4proto udp reject", text)
        self.assertTrue(text.strip().endswith("}"))

    def test_other_udp_is_blocked_even_when_the_reject_check_is_off(self):
        text = render_firewall(NetworkSettings(reject_non_tor=False))
        self.assertIn("meta l4proto udp reject", text)
        self.assertNotIn("\n        reject\n", text)

    def test_reject_check_and_lan_through_tor(self):
        text = render_firewall(NetworkSettings(lan_direct=False, reject_non_tor=True))
        self.assertNotIn("192.168.0.0/16", text)
        self.assertIn("udp dport 53 redirect", text)
        self.assertIn("\n        reject\n", text)
        self.assertIn("udp dport { 67, 68, 546, 547 } accept", text)

    def test_only_one_table(self):
        text = render_firewall(NetworkSettings())
        self.assertEqual(text.count("table "), 1)
        self.assertNotIn("flush ruleset", text)

    def test_nft_check_does_not_install_the_table(self):
        nft = subprocess.run(["nft", "--version"], capture_output=True, text=True)
        if nft.returncode != 0:
            self.skipTest("nft is not installed")
        before = subprocess.run(
            ["sudo", "-n", "nft", "list", "tables"],
            capture_output=True,
            text=True,
        )
        user = getpass.getuser()
        variants = [
            NetworkSettings(),
            NetworkSettings(lan_direct=False),
            NetworkSettings(reject_non_tor=False),
            NetworkSettings(lan_direct=False, reject_non_tor=False),
        ]
        for network in variants:
            rules = render_firewall(network, tor_user=user)
            result = subprocess.run(
                ["sudo", "-n", "nft", "-c", "-f", "-"],
                input=rules,
                capture_output=True,
                text=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
        after = subprocess.run(
            ["sudo", "-n", "nft", "list", "tables"],
            capture_output=True,
            text=True,
        )
        self.assertEqual(before.stdout, after.stdout)
        self.assertNotIn("onionfruitux", after.stdout)


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
        self.assertNotIn("systemctl enable", install)
        self.assertNotIn("systemctl start", install)
        unit = Path("share/onionfruitux.service").read_text(encoding="utf-8")
        from onionfruitux.system import UNIT_TEXT

        self.assertEqual(unit.strip(), UNIT_TEXT.strip())
        self.assertIn("ExecStart=/usr/bin/onionfruitux --system connect", unit)
        self.assertNotIn("systemctl start", unit)
