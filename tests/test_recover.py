"""Startup recover reattaches a healthy leftover Tor or clears a broken one."""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from onionfruitux.config import Config, NetworkSettings, load_config, save_config
from onionfruitux.recover import (
    ATTACHED_NOTE,
    CLEARED_NOTE,
    RecoverResult,
    SessionReport,
    inspect_session,
    recover_session,
    socks_is_healthy,
)


def _free_port() -> int:
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


def _serve_socks(port: int, stop: threading.Event):
    server = socket.socket()
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind(("127.0.0.1", port))
    server.listen(4)
    server.settimeout(0.2)
    try:
        while not stop.is_set():
            try:
                conn, _addr = server.accept()
            except socket.timeout:
                continue
            try:
                conn.recv(2)
                conn.sendall(b"\x05\x00")
            finally:
                conn.close()
    finally:
        server.close()


def _serve_plain(port: int, stop: threading.Event):
    server = socket.socket()
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind(("127.0.0.1", port))
    server.listen(4)
    server.settimeout(0.2)
    try:
        while not stop.is_set():
            try:
                conn, _addr = server.accept()
            except socket.timeout:
                continue
            conn.close()
    finally:
        server.close()


def _fake_tor():
    return subprocess.Popen(
        [
            sys.executable,
            "-c",
            "import os, sys, time; os.execv(sys.executable, "
            "['tor', '-c', 'import time; time.sleep(30)', '-f', "
            "'/var/lib/onionfruitux/torrc'])",
        ]
    )


class SocksHealthTests(unittest.TestCase):
    def test_socks_greeting_is_required(self):
        port = _free_port()
        stop = threading.Event()
        thread = threading.Thread(target=_serve_socks, args=(port, stop), daemon=True)
        thread.start()
        time.sleep(0.05)
        try:
            self.assertTrue(socks_is_healthy(port))
        finally:
            stop.set()
            thread.join(timeout=2)

    def test_a_plain_listener_is_not_healthy_socks(self):
        port = _free_port()
        stop = threading.Event()
        thread = threading.Thread(target=_serve_plain, args=(port, stop), daemon=True)
        thread.start()
        time.sleep(0.05)
        try:
            self.assertFalse(socks_is_healthy(port))
        finally:
            stop.set()
            thread.join(timeout=2)

    def test_a_closed_port_is_not_healthy(self):
        self.assertFalse(socks_is_healthy(_free_port(), timeout=0.2))


class RecoverSessionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.state = Path(self.tmp.name) / "state"
        self.state.mkdir()
        self._old_state = os.environ.get("XDG_STATE_HOME")
        os.environ["XDG_STATE_HOME"] = str(self.state)

    def tearDown(self):
        if self._old_state is None:
            os.environ.pop("XDG_STATE_HOME", None)
        else:
            os.environ["XDG_STATE_HOME"] = self._old_state
        self.tmp.cleanup()

    def test_idle_when_nothing_is_left_behind(self):
        report = SessionReport()
        self.assertFalse(report.our_tor_healthy())
        self.assertFalse(report.needs_cleanup())
        with patch("onionfruitux.recover.inspect_session", return_value=report):
            result = recover_session()
        self.assertEqual(result.action, "idle")
        self.assertFalse(result.on)
        self.assertEqual(result.note, "")

    def test_healthy_orphan_is_reattached_and_proxy_is_restored(self):
        applied = {}
        report = SessionReport(tor_pids=[4242], socks_healthy=True)

        def attach(*_args, **_kwargs):
            applied["called"] = True

        with patch("onionfruitux.recover.inspect_session", return_value=report), \
             patch("onionfruitux.recover._reattach_proxy", side_effect=attach), \
             patch("onionfruitux.recover._cleanup_privileged") as privileged, \
             patch("onionfruitux.recover._cleanup_user_proxy") as user:
            result = recover_session()
        self.assertTrue(result.attached)
        self.assertTrue(result.on)
        self.assertEqual(result.note, ATTACHED_NOTE)
        self.assertTrue(applied.get("called"))
        privileged.assert_not_called()
        user.assert_not_called()

    def test_unhealthy_orphan_is_cleared(self):
        first = SessionReport(tor_pids=[99], socks_healthy=False, stale_pidfile=True)
        empty = SessionReport()
        reports = [first, empty]

        def inspect():
            return reports.pop(0) if reports else empty

        with patch("onionfruitux.recover.inspect_session", side_effect=inspect), \
             patch("onionfruitux.recover._cleanup_privileged") as privileged, \
             patch("onionfruitux.recover._cleanup_user_proxy") as user, \
             patch("onionfruitux.recover._is_root", return_value=True):
            result = recover_session(privileged=True)
        self.assertEqual(result.action, "cleared")
        self.assertFalse(result.on)
        self.assertEqual(result.note, CLEARED_NOTE)
        privileged.assert_called_once()
        user.assert_called_once()

    def test_unhealthy_orphan_asks_the_desktop_to_escalate(self):
        first = SessionReport(tor_pids=[99], socks_healthy=False)
        empty = SessionReport()
        reports = [first, empty]
        escalated = {}

        def inspect():
            return reports.pop(0) if reports else empty

        def escalate(args, error_file=None):
            escalated["args"] = args
            return 0

        with patch("onionfruitux.recover.inspect_session", side_effect=inspect), \
             patch("onionfruitux.recover._cleanup_user_proxy"), \
             patch("onionfruitux.recover._is_root", return_value=False):
            result = recover_session(escalate=escalate)
        self.assertEqual(result.action, "cleared")
        self.assertEqual(escalated.get("args"), ["recover"])

    def test_reattach_writes_a_fallback_snapshot_when_the_original_is_gone(self):
        from onionfruitux import recover as recover_mod
        from onionfruitux.proxy import snapshot_path

        self.assertFalse(snapshot_path().exists())
        with patch.object(recover_mod, "proxy_is_ours", return_value=True), \
             patch.object(recover_mod, "write_fallback_snapshot") as fallback, \
             patch.object(recover_mod, "apply_proxy") as apply, \
             patch.object(recover_mod, "capture_snapshot") as capture:
            recover_mod._reattach_proxy(["localhost"])
        fallback.assert_called_once()
        apply.assert_not_called()
        capture.assert_not_called()

    def test_reattach_captures_and_applies_when_the_proxy_was_already_restored(self):
        from onionfruitux import recover as recover_mod

        with patch.object(recover_mod, "proxy_is_ours", return_value=False), \
             patch.object(recover_mod, "write_fallback_snapshot") as fallback, \
             patch.object(recover_mod, "apply_proxy") as apply, \
             patch.object(recover_mod, "capture_snapshot") as capture:
            recover_mod._reattach_proxy(["localhost"])
        capture.assert_called_once()
        apply.assert_called_once()
        fallback.assert_not_called()


class OrphanSimulationTests(unittest.TestCase):
    """Exercise a live leftover process and a live port, the way a dirty close looks."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.state = Path(self.tmp.name) / "state"
        self.state.mkdir()
        self._old_state = os.environ.get("XDG_STATE_HOME")
        os.environ["XDG_STATE_HOME"] = str(self.state)
        self.proc = None
        self.stop = threading.Event()
        self.thread = None

    def tearDown(self):
        self.stop.set()
        if self.thread is not None:
            self.thread.join(timeout=2)
        if self.proc is not None and self.proc.poll() is None:
            self.proc.kill()
            self.proc.wait(timeout=3)
        if self._old_state is None:
            os.environ.pop("XDG_STATE_HOME", None)
        else:
            os.environ["XDG_STATE_HOME"] = self._old_state
        self.tmp.cleanup()

    def _wait_for_tor_comm(self, proc) -> None:
        deadline = time.time() + 2
        while time.time() < deadline:
            try:
                cmdline = Path(f"/proc/{proc.pid}/cmdline").read_bytes()
            except OSError:
                cmdline = b""
            if cmdline.startswith(b"tor\x00"):
                return
            time.sleep(0.05)
        self.fail(f"fake tor did not rewrite its command line: {cmdline!r}")

    def test_healthy_orphaned_tor_is_reattached_on_the_real_socks_port(self):
        from onionfruitux.paths import SOCKS_PORT
        from onionfruitux import system

        sock = socket.socket()
        try:
            sock.bind(("127.0.0.1", SOCKS_PORT))
        except OSError:
            self.skipTest(f"port {SOCKS_PORT} is already in use")
        sock.close()

        self.proc = _fake_tor()
        self._wait_for_tor_comm(self.proc)
        self.assertIn(self.proc.pid, system.list_our_tor())

        self.thread = threading.Thread(
            target=_serve_socks, args=(SOCKS_PORT, self.stop), daemon=True
        )
        self.thread.start()
        time.sleep(0.05)
        self.assertTrue(socks_is_healthy(SOCKS_PORT))

        attached = {}

        def fake_attach(_hosts=None):
            attached["yes"] = True

        with patch("onionfruitux.recover._reattach_proxy", side_effect=fake_attach), \
             patch("onionfruitux.recover._nft_table_present", return_value=False), \
             patch("onionfruitux.recover.proxy_is_ours", return_value=False), \
             patch("onionfruitux.listeners.our_port_conflicts", return_value=[]):
            result = recover_session()
        self.assertTrue(result.attached, result)
        self.assertTrue(attached.get("yes"))
        # The leftover process is still running; we reattached instead of killing it.
        self.assertIsNone(self.proc.poll())

    def test_port_in_use_by_our_dead_session_is_cleared_before_connect(self):
        """A leftover holder of 9155 is stopped so connect does not see 'port in use'."""
        from onionfruitux.paths import SOCKS_PORT
        from onionfruitux import system

        sock = socket.socket()
        try:
            sock.bind(("127.0.0.1", SOCKS_PORT))
        except OSError:
            self.skipTest(f"port {SOCKS_PORT} is already in use")
        sock.close()

        self.proc = _fake_tor()
        self._wait_for_tor_comm(self.proc)
        # No SOCKS greeting: unhealthy leftover. recover should try to clear it.
        first = inspect_session()
        self.assertIn(self.proc.pid, first.tor_pids)
        self.assertFalse(first.socks_healthy)

        def cleanup():
            system.stop_tor()

        with patch("onionfruitux.recover._cleanup_privileged", side_effect=cleanup), \
             patch("onionfruitux.recover._cleanup_user_proxy"), \
             patch("onionfruitux.recover._is_root", return_value=True), \
             patch("onionfruitux.recover._nft_table_present", return_value=False), \
             patch("onionfruitux.recover.proxy_is_ours", return_value=False), \
             patch("onionfruitux.listeners.our_port_conflicts", return_value=[]):
            result = recover_session(privileged=True)
        self.assertEqual(result.action, "cleared")
        self.assertIsNotNone(self.proc.wait(timeout=3))
        self.assertFalse(system.list_our_tor())


class ConnectRecoverTests(unittest.TestCase):
    def test_connect_returns_early_when_a_session_is_reattached(self):
        from onionfruitux.cli import connect_command

        recovered = RecoverResult("attached", True, ATTACHED_NOTE)
        with patch("onionfruitux.cli._config_for", return_value=Config()), \
             patch("onionfruitux.cli._needs_admin", return_value=True), \
             patch("onionfruitux.recover.recover_session", return_value=recovered), \
             patch("onionfruitux.cli.validate_for_connect") as validate, \
             patch("onionfruitux.cli.escalate") as escalate, \
             patch("onionfruitux.cli._wants_check_page", return_value=False):
            connect_command(open_page=False)
        validate.assert_not_called()
        escalate.assert_not_called()


class CloseToTraySettingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self._old = os.environ.get("XDG_CONFIG_HOME")
        os.environ["XDG_CONFIG_HOME"] = self.tmp.name

    def tearDown(self):
        if self._old is None:
            os.environ.pop("XDG_CONFIG_HOME", None)
        else:
            os.environ["XDG_CONFIG_HOME"] = self._old
        self.tmp.cleanup()

    def test_close_to_tray_defaults_on_and_can_be_turned_off(self):
        cfg = load_config()
        self.assertTrue(cfg.network.close_to_tray)
        cfg.network = NetworkSettings(
            open_check_page=True,
            close_to_tray=False,
        ).cleaned()
        save_config(cfg)
        loaded = load_config()
        self.assertFalse(loaded.network.close_to_tray)
