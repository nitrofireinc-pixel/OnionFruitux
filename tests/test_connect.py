"""Connect waits for Tor, then installs the firewall. Nothing here routes traffic."""

from __future__ import annotations

import subprocess
import sys
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
             patch.object(system, "_wait_for_listeners", return_value=True), \
             patch.object(system, "_wait_bootstrap", side_effect=bootstrap), \
             patch.object(system, "apply_ruleset", side_effect=apply), \
             patch.object(system, "engage_dns", side_effect=lambda: order.append("dns")), \
             patch.object(system, "_write_state") as state:
            messages = []
            system.connect(Config(), progress=messages.append)
        self.assertEqual(order, ["bootstrap", "firewall", "dns"])
        self.assertIn("Tor bootstrap 100%…", messages)
        self.assertIn("Applying the firewall…", messages)
        self.assertEqual(messages[-1], "Connected.")
        state.assert_called_with(True, 42, "")

    def test_bootstrap_timeout_does_not_install_the_firewall(self):
        patches = self._common()
        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], \
             patches[6], patches[7] as rollback, \
             patch.object(system, "_wait_for_listeners", return_value=True), \
             patch.object(system, "_wait_bootstrap", return_value=40), \
             patch.object(system, "apply_ruleset") as apply:
            with self.assertRaises(OnionError) as caught:
                system.connect(Config())
        apply.assert_not_called()
        rollback.assert_called_once()
        self.assertIn("time limit", str(caught.exception))
        self.assertIn("firewall was not changed", str(caught.exception))

    def test_stale_heartbeat_rolls_back_before_the_firewall(self):
        def wait_listeners(timeout, abort=None):
            if abort is not None:
                abort()
            return True

        with tempfile.TemporaryDirectory() as tmp:
            heartbeat = Path(tmp) / "heartbeat"
            heartbeat.write_text(f"{time.time() - 60}\n", encoding="utf-8")
            patches = self._common()
            with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], \
                 patches[6], patches[7] as rollback, \
                 patch.object(system, "_wait_for_listeners", side_effect=wait_listeners), \
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
             patch.object(system, "_wait_for_listeners", return_value=False):
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

    def test_busy_port_is_reported_before_tor_starts(self):
        patches = self._common()
        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], \
             patch.object(
                 system,
                 "our_port_conflicts",
                 return_value=[
                     "OnionFruitux's SOCKS port 9155 is already in use by "
                     "tor (pid 9) running as debian-tor. The firewall was not changed."
                 ],
             ), \
             patch.object(system, "_spawn_tor") as spawn:
            with self.assertRaises(OnionError) as caught:
                system.connect(Config())
        spawn.assert_not_called()
        self.assertIn("9155", str(caught.exception))
        self.assertIn("debian-tor", str(caught.exception))

    def test_leftover_onionfruitux_tor_is_not_called_a_permission_error(self):
        patches = self._common()
        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], \
             patch.object(system, "_our_tor_pids", return_value=[4242]), \
             patch.object(system, "_alive", return_value=True), \
             patch.object(system, "_is_our_tor", return_value=True), \
             patch.object(system, "_spawn_tor") as spawn:
            with self.assertRaises(OnionError) as caught:
                system.connect(Config())
        spawn.assert_not_called()
        message = str(caught.exception)
        self.assertIn("previous Tor is still running", message)
        self.assertIn("4242", message)
        self.assertNotIn("permission", message)

    def test_stop_tor_clears_a_leftover_instance(self):
        proc = subprocess.Popen(
            [
                sys.executable,
                "-c",
                "import os, sys, time; os.execv(sys.executable, "
                "['tor', '-c', 'import time; time.sleep(30)', '-f', "
                "'/var/lib/onionfruitux/torrc'])",
            ]
        )
        try:
            deadline = time.time() + 2
            cmdline = b""
            while time.time() < deadline:
                try:
                    cmdline = Path(f"/proc/{proc.pid}/cmdline").read_bytes()
                except OSError:
                    cmdline = b""
                if cmdline.startswith(b"tor\x00"):
                    break
                time.sleep(0.05)
            self.assertTrue(cmdline.startswith(b"tor\x00"), cmdline)
            with tempfile.TemporaryDirectory() as tmp, \
                 patch.object(system, "PID_PATH", Path(tmp) / "pid"):
                system.stop_tor()
            self.assertIsNotNone(proc.wait(timeout=3))
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait(timeout=3)

    def test_tor_exit_is_reported_immediately(self):
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / "notice.log"
            err = Path(tmp) / "tor.stderr"
            log.write_text(
                "Bootstrapped 0% (starting)\nTor 0.4.9.11 died: Caught signal 11\n",
                encoding="utf-8",
            )
            err.write_text("Caught signal 11\n", encoding="utf-8")
            proc = type("Proc", (), {"pid": 42, "returncode": None})()

            def poll():
                proc.returncode = -11
                return -11

            proc.poll = poll

            def wait_listeners(timeout, abort=None):
                if abort is not None:
                    abort()
                return True

            patches = self._common()
            started = time.time()
            with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], \
                 patches[7], \
                 patch.object(system, "_spawn_tor", return_value=proc), \
                 patch.object(system, "_wait_for_listeners", side_effect=wait_listeners), \
                 patch.object(system, "LOG_PATH", log), \
                 patch.object(system, "STDERR_PATH", err), \
                 patch.object(system, "apply_ruleset") as apply:
                with self.assertRaises(OnionError) as caught:
                    system.connect(Config())
        self.assertLess(time.time() - started, 3)
        text = str(caught.exception)
        self.assertIn("Caught signal 11", text)
        self.assertIn("firewall was not changed", text)
        apply.assert_not_called()

    def test_bootstrap_percent_uses_the_latest_line(self):
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / "notice.log"
            log.write_text(
                "Bootstrapped 10%: \nBootstrapped 100%: Done\n",
                encoding="utf-8",
            )
            with patch.object(system, "LOG_PATH", log):
                self.assertEqual(system._bootstrap_percent(), 100)
