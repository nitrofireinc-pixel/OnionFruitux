"""Connect waits for Tor, then installs the firewall. Nothing here routes traffic."""

from __future__ import annotations

import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from onionfruitux import system
from onionfruitux.config import Config
from onionfruitux.errors import OnionError


def _proc():
    return type("Proc", (), {"pid": 42})()


class ConnectOrderTests(unittest.TestCase):
    def _common(self):
        return (
            patch.object(system.os, "geteuid", return_value=0),
            patch.object(system, "_arm_parent_death"),
            patch.object(system, "_prepare_runtime"),
            patch.object(system, "_install_torrc"),
            patch.object(system, "stop_tor"),
            patch.object(system, "_reset_log"),
            patch.object(system, "_spawn_tor", return_value=_proc()),
            patch.object(system, "_rollback_network"),
        )

    def test_firewall_is_applied_only_after_bootstrap(self):
        order = []

        def bootstrap(timeout, progress=None, abort=None):
            order.append("bootstrap")
            if progress is not None:
                progress("Tor bootstrap 100%…")
            return 100

        def apply(rules):
            order.append("firewall")

        patches = self._common()
        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], \
             patches[6], patches[7], \
             patch.object(system, "_wait_port", return_value=True), \
             patch.object(system, "_wait_bootstrap", side_effect=bootstrap), \
             patch.object(system, "apply_ruleset", side_effect=apply), \
             patch.object(system, "_write_state") as state:
            messages = []
            system.connect(Config(), progress=messages.append)
        self.assertEqual(order, ["bootstrap", "firewall"])
        self.assertIn("Tor bootstrap 100%…", messages)
        self.assertIn("Applying the firewall…", messages)
        self.assertEqual(messages[-1], "Connected.")
        state.assert_called_with(True, 42, "")

    def test_bootstrap_timeout_does_not_install_the_firewall(self):
        patches = self._common()
        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], \
             patches[6], patches[7] as rollback, \
             patch.object(system, "_wait_port", return_value=True), \
             patch.object(system, "_wait_bootstrap", return_value=40), \
             patch.object(system, "apply_ruleset") as apply:
            with self.assertRaises(OnionError) as caught:
                system.connect(Config())
        apply.assert_not_called()
        rollback.assert_called_once()
        self.assertIn("time limit", str(caught.exception))
        self.assertIn("firewall was not changed", str(caught.exception))

    def test_stale_heartbeat_rolls_back_before_the_firewall(self):
        def wait_port(port, timeout, abort=None):
            if abort is not None:
                abort()
            return True

        with tempfile.TemporaryDirectory() as tmp:
            heartbeat = Path(tmp) / "heartbeat"
            heartbeat.write_text(f"{time.time() - 60}\n", encoding="utf-8")
            patches = self._common()
            with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], \
                 patches[6], patches[7] as rollback, \
                 patch.object(system, "_wait_port", side_effect=wait_port), \
                 patch.object(system, "apply_ruleset") as apply:
                with self.assertRaises(OnionError) as caught:
                    system.connect(Config(), heartbeat_file=str(heartbeat))
        apply.assert_not_called()
        rollback.assert_called_once()
        self.assertIn("closed", str(caught.exception))

    def test_log_is_cleared_before_tor_starts(self):
        order = []
        patches = self._common()
        with patches[0], patches[1], patches[2], patches[3], patches[4], \
             patch.object(system, "_reset_log", side_effect=lambda: order.append("reset")), \
             patch.object(system, "_spawn_tor", side_effect=lambda: order.append("spawn") or _proc()), \
             patches[7], \
             patch.object(system, "_wait_port", return_value=False):
            with self.assertRaises(OnionError):
                system.connect(Config())
        self.assertEqual(order, ["reset", "spawn"])

    def test_nft_timeout_is_a_clean_error(self):
        proc = type("Nft", (), {})()
        proc.pid = 7
        calls = {"n": 0}

        def communicate(rules=None, timeout=None):
            calls["n"] += 1
            if calls["n"] == 1:
                raise subprocess.TimeoutExpired(cmd="nft", timeout=15)
            return ("", "")

        proc.communicate = communicate
        proc.kill = lambda: None
        with patch.object(system, "delete_table"), \
             patch.object(system.subprocess, "Popen", return_value=proc):
            with self.assertRaises(OnionError) as caught:
                system.apply_ruleset("table inet onionfruitux {\n}\n")
        self.assertIn("nftables did not finish", str(caught.exception))

    def test_panic_off_removes_the_table_and_tor(self):
        with patch.object(system.os, "geteuid", return_value=0), \
             patch.object(system, "delete_table") as delete, \
             patch.object(system, "stop_tor") as stop, \
             patch.object(system, "_write_state") as state, \
             patch.object(system, "_flush_dns_cache") as flush:
            system.panic_off()
            system.panic_off()
        self.assertEqual(delete.call_count, 2)
        self.assertEqual(stop.call_count, 2)
        state.assert_called_with(False, None, "")
        self.assertEqual(flush.call_count, 2)

    def test_rollback_deletes_the_table(self):
        with patch.object(system, "_stop_helper") as helper, \
             patch.object(system, "delete_table") as delete, \
             patch.object(system, "stop_tor") as stop, \
             patch.object(system, "_write_state") as state:
            system._rollback_network()
        helper.assert_called_once()
        delete.assert_called_once()
        stop.assert_called_once()
        state.assert_called_once_with(False, None, "")

    def test_systemd_parent_is_not_treated_as_a_dead_parent(self):
        text = Path("onionfruitux/system.py").read_text(encoding="utf-8")
        start = text.index("def _arm_parent_death")
        body = text[start:text.index("def _rollback_signal")]
        self.assertNotIn("getppid", body)

    def test_bootstrap_percent_uses_the_latest_line(self):
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / "notice.log"
            log.write_text(
                "Bootstrapped 10%: \nBootstrapped 100%: Done\n",
                encoding="utf-8",
            )
            with patch.object(system, "LOG_PATH", log):
                self.assertEqual(system._bootstrap_percent(), 100)
