"""DNS changes point at Tor and restore the previous resolver."""

from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from onionfruitux import dns


class _Resolver:
    """Stand-in for systemd-resolved. Tor's DNS port is not running here."""

    def __init__(self):
        self.links = {
            "enp42s0": {
                "dns": ["192.168.1.1"],
                "domains": ["lan"],
                "default_route": "yes",
            },
            "tailscale0": {
                "dns": ["100.100.100.100"],
                "domains": ["tail.ts.net.", "~tail.ts.net."],
                "default_route": "no",
            },
        }
        self.calls = []

    def run(self, argv, timeout=5):
        self.calls.append(argv)
        if not argv:
            return subprocess.CompletedProcess(argv, 0, "", "")
        if argv[0] == "resolvectl":
            return self._resolvectl(argv)
        if argv[:4] == ["nmcli", "-t", "-f", "DEVICE,STATE"]:
            return subprocess.CompletedProcess(
                argv,
                0,
                "enp42s0:connected\ntailscale0:unmanaged\ndocker0:unmanaged\n",
                "",
            )
        if argv[:3] == ["nmcli", "device", "reapply"]:
            device = argv[3]
            if device in self.links:
                # NetworkManager publishes the connection profile again.
                self.links[device] = {
                    "dns": ["192.168.1.1"] if device == "enp42s0" else ["100.100.100.100"],
                    "domains": ["lan"] if device == "enp42s0" else ["tail.ts.net.", "~tail.ts.net."],
                    "default_route": "yes" if device == "enp42s0" else "no",
                }
            return subprocess.CompletedProcess(argv, 0, "", "")
        if argv[:3] == ["tailscale", "debug", "prefs"]:
            return subprocess.CompletedProcess(argv, 0, '{"CorpDNS": true}\n', "")
        return subprocess.CompletedProcess(argv, 0, "", "")

    def _resolvectl(self, argv):
        if argv[1:] == ["flush-caches"]:
            return subprocess.CompletedProcess(argv, 0, "", "")
        kind, link = argv[1], argv[2]
        info = self.links.setdefault(
            link, {"dns": [], "domains": [], "default_route": "yes"}
        )
        if len(argv) == 3:
            if kind == "dns":
                value = " ".join(info["dns"])
            elif kind == "domain":
                value = " ".join(info["domains"])
            else:
                value = info["default_route"]
            return subprocess.CompletedProcess(argv, 0, f"Link 2 ({link}): {value}\n", "")
        values = argv[3:]
        if kind == "dns":
            info["dns"] = [] if values == [""] else values
        elif kind == "domain":
            info["domains"] = [] if values == [""] else values
        elif kind == "default-route":
            info["default_route"] = values[0]
        return subprocess.CompletedProcess(argv, 0, "", "")

    def resolves(self, name: str) -> str | None:
        """Return the DNS server that would answer name, or None when lookup fails.

        A routing domain beats the link that is the DNS default route, which
        is how systemd-resolved chooses.
        """
        fallback = None
        for info in self.links.values():
            servers = [server for server in info["dns"] if not server.startswith("127.0.0.1")]
            if not servers:
                continue
            domains = info["domains"]
            routed = any(
                name == domain[1:].rstrip(".") or name.endswith("." + domain[1:].rstrip("."))
                for domain in domains
                if domain.startswith("~") and domain != "~."
            )
            if routed:
                return servers[0]
            if fallback is None and (info["default_route"] == "yes" or "~." in domains):
                fallback = servers[0]
        return fallback


class DnsTests(unittest.TestCase):
    def test_resolved_stub_is_not_replaced(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            stub = root / "stub-resolv.conf"
            stub.write_text("nameserver 127.0.0.53\n", encoding="utf-8")
            resolv = root / "resolv.conf"
            resolv.symlink_to(stub)
            save = root / "dns.json"
            calls = []

            def run(argv, timeout=5):
                calls.append(argv)
                text = ""
                if argv[:3] == ["resolvectl", "dns", "enp42s0"] and len(argv) == 3:
                    text = "Link 2 (enp42s0): 192.168.1.1\n"
                elif argv[:3] == ["resolvectl", "domain", "enp42s0"] and len(argv) == 3:
                    text = "Link 2 (enp42s0): lan\n"
                elif argv[:3] == ["resolvectl", "default-route", "enp42s0"] and len(argv) == 3:
                    text = "Link 2 (enp42s0): yes\n"
                elif argv[:3] == ["resolvectl", "dns", "tailscale0"] and len(argv) == 3:
                    text = "Link 2 (tailscale0): 100.100.100.100\n"
                elif argv[:3] == ["resolvectl", "domain", "tailscale0"] and len(argv) == 3:
                    text = "Link 2 (tailscale0): ~tail.ts.net.\n"
                elif argv[:3] == ["resolvectl", "default-route", "tailscale0"] and len(argv) == 3:
                    text = "Link 2 (tailscale0): no\n"
                return subprocess.CompletedProcess(argv, 0, text, "")

            def which(name):
                if name == "resolvectl":
                    return "/usr/bin/resolvectl"
                return None

            with patch.object(dns, "_RESOLV", resolv), \
                 patch.object(dns, "DNS_SAVE_PATH", save), \
                 patch.object(dns, "STATE_DIR", root), \
                 patch.object(dns.shutil, "which", side_effect=which), \
                 patch.object(dns, "_run", side_effect=run):
                dns.engage_dns(["enp42s0", "tailscale0"])
                self.assertEqual(stub.read_text(encoding="utf-8"), "nameserver 127.0.0.53\n")
                self.assertTrue(resolv.is_symlink())
                self.assertIn(
                    ["resolvectl", "dns", "enp42s0", "127.0.0.1:9153"],
                    calls,
                )
                self.assertIn(["resolvectl", "domain", "enp42s0", "~."], calls)
                calls.clear()
                dns.restore_dns()
            self.assertFalse(save.exists())
            self.assertNotIn(["resolvectl", "revert", "enp42s0"], calls)
            self.assertNotIn(["resolvectl", "revert", "tailscale0"], calls)
            self.assertIn(["resolvectl", "dns", "enp42s0", "192.168.1.1"], calls)
            self.assertIn(["resolvectl", "domain", "enp42s0", "lan"], calls)
            self.assertIn(["resolvectl", "default-route", "enp42s0", "yes"], calls)
            self.assertIn(["resolvectl", "dns", "tailscale0", "100.100.100.100"], calls)
            self.assertIn(["resolvectl", "default-route", "tailscale0", "no"], calls)

    def test_off_restores_dns_resolution(self):
        resolver = _Resolver()

        def which(name):
            if name in {"resolvectl", "nmcli", "tailscale", "systemctl"}:
                return "/usr/bin/" + name
            return None

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            stub = root / "stub-resolv.conf"
            stub.write_text("nameserver 127.0.0.53\n", encoding="utf-8")
            resolv = root / "resolv.conf"
            resolv.symlink_to(stub)
            save = root / "dns.json"
            dropin = root / "resolved.conf.d" / "dns.conf"
            dropin.parent.mkdir()
            dropin.write_text("[Resolve]\nDNS=192.168.1.1\n", encoding="utf-8")
            with patch.object(dns, "_RESOLV", resolv), \
                 patch.object(dns, "DNS_SAVE_PATH", save), \
                 patch.object(dns, "STATE_DIR", root), \
                 patch.object(dns, "_DROPIN_DIRS", (dropin.parent,)), \
                 patch.object(dns.shutil, "which", side_effect=which), \
                 patch.object(dns, "_run", side_effect=resolver.run):
                self.assertEqual(resolver.resolves("example.com"), "192.168.1.1")
                dns.engage_dns(["enp42s0", "tailscale0"])
                self.assertIsNone(resolver.resolves("example.com"))
                saved = json.loads(save.read_text(encoding="utf-8"))
                self.assertEqual(saved["snapshot"]["links"]["enp42s0"]["dns"], ["192.168.1.1"])
                self.assertEqual(saved["snapshot"]["links"]["enp42s0"]["default_route"], "yes")
                self.assertIn(str(dropin), saved["snapshot"]["dropins"])
                dropin.write_text("changed\n", encoding="utf-8")
                dns.restore_dns()
            self.assertEqual(dropin.read_text(encoding="utf-8"), "[Resolve]\nDNS=192.168.1.1\n")
            self.assertEqual(resolver.resolves("example.com"), "192.168.1.1")
            self.assertEqual(resolver.resolves("host.tail.ts.net"), "100.100.100.100")
            self.assertFalse(any(argv[:2] == ["resolvectl", "revert"] for argv in resolver.calls))
            self.assertIn(["nmcli", "device", "reapply", "enp42s0"], resolver.calls)
            self.assertIn(["systemctl", "reload", "systemd-resolved"], resolver.calls)
            self.assertIn(["tailscale", "set", "--accept-dns=true"], resolver.calls)

    def test_panic_off_reapplies_when_the_snapshot_was_already_removed(self):
        calls = []

        def run(argv, timeout=5):
            calls.append(argv)
            if argv[:4] == ["nmcli", "-t", "-f", "DEVICE,STATE"]:
                return subprocess.CompletedProcess(argv, 0, "enp42s0:connected\n", "")
            return subprocess.CompletedProcess(argv, 0, "", "")

        def which(name):
            if name in {"nmcli", "systemctl"}:
                return "/usr/bin/" + name
            return None

        with tempfile.TemporaryDirectory() as tmp:
            save = Path(tmp) / "missing.json"
            with patch.object(dns, "DNS_SAVE_PATH", save), \
                 patch.object(dns.shutil, "which", side_effect=which), \
                 patch.object(dns, "_run", side_effect=run):
                dns.restore_dns()
            self.assertFalse(save.exists())
            self.assertIn(["nmcli", "device", "reapply", "enp42s0"], calls)
            self.assertIn(["systemctl", "reload", "systemd-resolved"], calls)

    def test_plain_resolv_conf_is_saved_and_restored(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            resolv = root / "resolv.conf"
            original = "nameserver 192.168.1.254\n"
            resolv.write_text(original, encoding="utf-8")
            save = root / "dns.json"
            backup = root / "resolv.conf.saved"
            with patch.object(dns, "_RESOLV", resolv), \
                 patch.object(dns, "DNS_SAVE_PATH", save), \
                 patch.object(dns, "RESOLV_BACKUP_PATH", backup), \
                 patch.object(dns, "STATE_DIR", root), \
                 patch.object(dns.shutil, "which", return_value=None):
                dns.engage_dns(["enp42s0"])
                self.assertIn("nameserver 127.0.0.1", resolv.read_text(encoding="utf-8"))
                self.assertEqual(backup.read_text(encoding="utf-8"), original)
                self.assertEqual(json.loads(save.read_text(encoding="utf-8"))["mode"], "file")
                dns.restore_dns()
            self.assertEqual(resolv.read_text(encoding="utf-8"), original)
            self.assertFalse(save.exists())
            self.assertFalse(backup.exists())
