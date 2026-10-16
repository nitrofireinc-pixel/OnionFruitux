"""A saved DNS snapshot can be put back. Nothing here points DNS at Tor."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from onionfruitux import dns


class DnsRestoreTests(unittest.TestCase):
    def test_resolved_snapshot_is_restored_without_touching_a_vpn(self):
        calls = []

        def run(argv, timeout=5):
            calls.append(list(argv))
            if argv[0] in {"nmcli", "tailscale", "networkctl", "nft", "iptables"}:
                raise AssertionError(argv)
            return type("Result", (), {"returncode": 0, "stdout": "", "stderr": ""})()

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            save = root / "dns.json"
            payload = {
                "mode": "resolved",
                "links": ["enp42s0", "tailscale0"],
                "snapshot": {
                    "links": {
                        "enp42s0": {
                            "dns": ["192.168.1.1"],
                            "domains": ["lan"],
                            "default_route": "yes",
                        },
                        "tailscale0": {
                            "dns": ["100.100.100.100"],
                            "domains": ["tail.ts.net"],
                            "default_route": "no",
                        },
                    },
                    "dropins": {},
                },
            }
            save.write_text(json.dumps(payload), encoding="utf-8")
            with patch.object(dns, "DNS_SAVE_PATH", save), \
                 patch.object(dns.shutil, "which", return_value="/usr/bin/resolvectl"), \
                 patch.object(dns, "_run", side_effect=run):
                dns.restore_saved_dns()
            self.assertFalse(save.exists())
        self.assertIn(["resolvectl", "dns", "enp42s0", "192.168.1.1"], calls)
        self.assertIn(["resolvectl", "domain", "enp42s0", "lan"], calls)
        self.assertIn(["resolvectl", "default-route", "enp42s0", "yes"], calls)
        self.assertIn(["resolvectl", "dns", "tailscale0", "100.100.100.100"], calls)
        self.assertIn(["resolvectl", "default-route", "tailscale0", "no"], calls)
        self.assertFalse(any(argv[0] in {"nmcli", "tailscale", "networkctl"} for argv in calls))
        self.assertFalse(any(len(argv) > 1 and argv[1] == "revert" for argv in calls))

    def test_plain_resolv_conf_backup_is_restored(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            resolv = root / "resolv.conf"
            original = "nameserver 192.168.1.1\n"
            resolv.write_text("nameserver 127.0.0.1\n", encoding="utf-8")
            backup = root / "resolv.conf.saved"
            backup.write_text(original, encoding="utf-8")
            save = root / "dns.json"
            save.write_text(json.dumps({"mode": "file", "links": []}), encoding="utf-8")
            with patch.object(dns, "_RESOLV", resolv), \
                 patch.object(dns, "RESOLV_BACKUP_PATH", backup), \
                 patch.object(dns, "DNS_SAVE_PATH", save):
                dns.restore_saved_dns()
            self.assertEqual(resolv.read_text(encoding="utf-8"), original)
            self.assertFalse(backup.exists())
            self.assertFalse(save.exists())

    def test_missing_snapshot_does_not_touch_resolv_conf(self):
        with tempfile.TemporaryDirectory() as tmp:
            resolv = Path(tmp) / "resolv.conf"
            original = "nameserver 1.1.1.1\n"
            resolv.write_text(original, encoding="utf-8")
            missing = Path(tmp) / "dns.json"
            with patch.object(dns, "_RESOLV", resolv), \
                 patch.object(dns, "DNS_SAVE_PATH", missing), \
                 patch.object(dns, "_run", side_effect=AssertionError("ran a command")):
                dns.restore_saved_dns()
            self.assertEqual(resolv.read_text(encoding="utf-8"), original)

    def test_module_does_not_point_resolv_conf_at_tor(self):
        text = Path(dns.__file__).read_text(encoding="utf-8")
        self.assertNotIn("engage_dns", text)
        self.assertNotIn("nameserver 127.0.0.1", text)
        self.assertIn("refusing to run", text)
