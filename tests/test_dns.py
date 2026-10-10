"""DNS changes point at Tor and restore the previous resolver."""

from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from onionfruitux import dns


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

            def run(argv):
                calls.append(argv)
                return subprocess.CompletedProcess(argv, 0, "", "")

            with patch.object(dns, "_RESOLV", resolv), \
                 patch.object(dns, "DNS_SAVE_PATH", save), \
                 patch.object(dns, "STATE_DIR", root), \
                 patch.object(dns.shutil, "which", return_value="/usr/bin/resolvectl"), \
                 patch.object(dns, "_run", side_effect=run):
                dns.engage_dns(["enp42s0", "tailscale0"])
                self.assertEqual(stub.read_text(encoding="utf-8"), "nameserver 127.0.0.53\n")
                self.assertTrue(resolv.is_symlink())
                self.assertEqual(
                    calls[:4],
                    [
                        ["resolvectl", "dns", "enp42s0", "127.0.0.1:9153"],
                        ["resolvectl", "domain", "enp42s0", "~."],
                        ["resolvectl", "dns", "tailscale0", "127.0.0.1:9153"],
                        ["resolvectl", "domain", "tailscale0", "~."],
                    ],
                )
                calls.clear()
                dns.restore_dns()
            self.assertFalse(save.exists())
            self.assertEqual(
                calls[:2],
                [
                    ["resolvectl", "revert", "enp42s0"],
                    ["resolvectl", "revert", "tailscale0"],
                ],
            )

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

    def test_restore_is_safe_when_nothing_was_saved(self):
        with tempfile.TemporaryDirectory() as tmp:
            save = Path(tmp) / "missing.json"
            with patch.object(dns, "DNS_SAVE_PATH", save):
                dns.restore_dns()
            self.assertFalse(save.exists())
