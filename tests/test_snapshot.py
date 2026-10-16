"""Snapshot and restore of the system proxy. Nothing here changes the real session."""

from __future__ import annotations

import os
import socket
import subprocess
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from onionfruitux import health, proxy
from onionfruitux.errors import DirectUnreachable, OnionError
from onionfruitux.health import DirectReport


def _result(code: int, stdout: str = "", stderr: str = ""):
    return subprocess.CompletedProcess(args=[], returncode=code, stdout=stdout, stderr=stderr)


class SnapshotTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.config_home = root / "config"
        self.state_home = root / "state"
        self.home = root / "home"
        self.config_home.mkdir()
        self.home.mkdir()
        self._env = patch.dict(
            os.environ,
            {
                "HOME": str(self.home),
                "XDG_CONFIG_HOME": str(self.config_home),
                "XDG_STATE_HOME": str(self.state_home),
                "XDG_CURRENT_DESKTOP": "KDE",
                "XDG_SESSION_DESKTOP": "plasma",
            },
        )
        self._env.start()
        self.gnome = {
            "org.gnome.system.proxy mode": "'none'",
            "org.gnome.system.proxy autoconfig-url": "''",
            "org.gnome.system.proxy ignore-hosts": "['localhost', '127.0.0.0/8']",
            "org.gnome.system.proxy use-same-proxy": "true",
            "org.gnome.system.proxy.http enabled": "false",
            "org.gnome.system.proxy.http host": "'proxy.example'",
            "org.gnome.system.proxy.http port": "8080",
            "org.gnome.system.proxy.http use-authentication": "false",
            "org.gnome.system.proxy.http authentication-user": "''",
            "org.gnome.system.proxy.http authentication-password": "''",
            "org.gnome.system.proxy.https host": "'proxy.example'",
            "org.gnome.system.proxy.https port": "8080",
            "org.gnome.system.proxy.ftp host": "''",
            "org.gnome.system.proxy.ftp port": "0",
            "org.gnome.system.proxy.socks host": "''",
            "org.gnome.system.proxy.socks port": "0",
        }
        self.session = {"OTHER": "keep"}
        self.calls: list[list[str]] = []
        kde = self.config_home / "kioslaverc"
        kde.write_text(
            "[Proxy Settings]\nProxyType=0\nhttpProxy=http://proxy.example:8080\n"
            "NoProxyFor=localhost\n\n[Other]\nKeep=yes\n",
            encoding="utf-8",
        )
        self._which = patch.object(proxy.shutil, "which", side_effect=self._which)
        self._run = patch.object(proxy, "_run", side_effect=self._fake_run)
        self._which.start()
        self._run.start()

    def tearDown(self):
        self._run.stop()
        self._which.stop()
        self._env.stop()
        self.tmp.cleanup()

    def _which(self, name: str):
        if name in {"gsettings", "systemctl", "kwriteconfig6", "dbus-send"}:
            return "/usr/bin/" + name
        return None

    def _fake_run(self, argv, timeout=8):
        self.calls.append(list(argv))
        if argv[0] == "gsettings" and argv[1] == "get":
            key = f"{argv[2]} {argv[3]}"
            if key not in self.gnome:
                return _result(1, stderr="missing")
            return _result(0, self.gnome[key] + "\n")
        if argv[0] == "gsettings" and argv[1] == "set":
            self.gnome[f"{argv[2]} {argv[3]}"] = argv[4]
            return _result(0)
        if argv[:3] == ["systemctl", "--user", "show-environment"]:
            text = "\n".join(f"{key}={value}" for key, value in self.session.items())
            return _result(0, text + "\n")
        if argv[:3] == ["systemctl", "--user", "set-environment"]:
            for item in argv[3:]:
                key, value = item.split("=", 1)
                self.session[key] = value
            return _result(0)
        if argv[:3] == ["systemctl", "--user", "unset-environment"]:
            for key in argv[3:]:
                self.session.pop(key, None)
            return _result(0)
        if argv[0] in {"kwriteconfig6", "dbus-send"}:
            return _result(0)
        return _result(1, stderr="unexpected")

    def test_apply_and_restore_puts_every_proxy_setting_back(self):
        before_gnome = dict(self.gnome)
        before_session = dict(self.session)
        with patch.object(proxy, "verify_tor", return_value={"IsTor": True, "IP": "192.0.2.1"}):
            proxy.enable_session(["localhost", "127.0.0.0/8", "printer.local"])
        self.assertTrue(proxy.snapshot_path().is_file())
        self.assertEqual(self.gnome["org.gnome.system.proxy mode"], "'manual'")
        self.assertEqual(self.gnome["org.gnome.system.proxy.socks host"], "'127.0.0.1'")
        self.assertEqual(self.gnome["org.gnome.system.proxy.socks port"], "9155")
        self.assertIn("printer.local", self.gnome["org.gnome.system.proxy ignore-hosts"])
        self.assertEqual(self.gnome["org.gnome.system.proxy use-same-proxy"], "false")
        self.assertEqual(self.session["ALL_PROXY"], "socks5h://127.0.0.1:9155")
        self.assertIn("printer.local", self.session["NO_PROXY"])
        self.assertNotIn("http_proxy", self.session)
        self.assertNotIn("HTTP_PROXY", self.session)
        kde = (self.config_home / "kioslaverc").read_text(encoding="utf-8")
        self.assertIn("ProxyType=1", kde)
        self.assertIn("socksProxy=socks://127.0.0.1:9155", kde)
        self.assertIn("httpProxy=http://proxy.example:8080", kde)
        self.assertIn("[Other]", kde)
        self.assertIn("Keep=yes", kde)
        env_file = proxy.environment_file()
        self.assertIn("socks5h://127.0.0.1:9155", env_file.read_text(encoding="utf-8"))
        commands = [call[0] for call in self.calls]
        for forbidden in ("nft", "iptables", "nmcli", "resolvectl", "tailscale", "protonvpn"):
            self.assertNotIn(forbidden, commands)

        with patch.object(proxy, "verify_direct", return_value=DirectReport(True, ["ok"])):
            proxy.disable_session()
        self.assertFalse(proxy.snapshot_path().exists())
        self.assertEqual(self.gnome, before_gnome)
        self.assertEqual(self.session, before_session)
        restored = (self.config_home / "kioslaverc").read_text(encoding="utf-8")
        self.assertIn("[Other]", restored)
        self.assertIn("Keep=yes", restored)
        group = proxy._read_group(restored, "Proxy Settings")
        self.assertEqual(group["ProxyType"], "0")
        self.assertEqual(group["httpProxy"], "http://proxy.example:8080")
        self.assertEqual(group["NoProxyFor"], "localhost")
        self.assertNotIn("socksProxy", group)
        self.assertFalse(env_file.exists())

    def test_failed_direct_check_restores_twice_and_keeps_the_snapshot(self):
        with patch.object(proxy, "verify_tor", return_value={"IsTor": True}):
            proxy.enable_session(["localhost"])
        seen = {"n": 0}
        original = proxy.restore_snapshot

        def wrapped(*, delete: bool):
            seen["n"] += 1
            return original(delete=delete)

        report = DirectReport(False, ["1.1.1.1: no TCP connection and no ping reply", "DNS: failed"])
        with patch.object(proxy, "restore_snapshot", side_effect=wrapped), \
             patch.object(proxy, "verify_direct", return_value=report), \
             patch.object(proxy, "resolv_conf_hint", return_value="/etc/resolv.conf: nameserver 192.168.1.1"), \
             patch.object(proxy, "leftover_firewall_hint", return_value=""):
            with self.assertRaises(DirectUnreachable) as caught:
                proxy.disable_session()
        self.assertEqual(seen["n"], 2)
        self.assertTrue(proxy.snapshot_path().exists())
        message = str(caught.exception)
        self.assertIn("1.1.1.1", message)
        self.assertIn("GNOME proxy mode", message)
        self.assertIn("nameserver 192.168.1.1", message)
        self.assertIn("Reset network settings to defaults", message)

    def test_enable_failure_rolls_the_snapshot_back(self):
        with patch.object(proxy, "verify_tor", side_effect=OnionError("not Tor")):
            with self.assertRaises(OnionError):
                proxy.enable_session(["localhost"])
        self.assertFalse(proxy.snapshot_path().exists())
        self.assertEqual(self.gnome["org.gnome.system.proxy mode"], "'none'")
        self.assertEqual(self.gnome["org.gnome.system.proxy.http host"], "'proxy.example'")
        self.assertEqual(self.session, {"OTHER": "keep"})
        self.assertFalse(proxy.environment_file().exists())

    def test_reset_sets_proxy_mode_to_none(self):
        with patch.object(proxy, "verify_tor", return_value={"IsTor": True}):
            proxy.enable_session(["localhost"])
        proxy.reset_proxy_defaults()
        self.assertEqual(self.gnome["org.gnome.system.proxy mode"], "'none'")
        self.assertEqual(self.gnome["org.gnome.system.proxy.socks port"], "0")
        self.assertFalse(proxy.snapshot_path().exists())
        self.assertFalse(proxy.environment_file().exists())
        self.assertNotIn("ALL_PROXY", self.session)
        kde = (self.config_home / "kioslaverc").read_text(encoding="utf-8")
        self.assertIn("ProxyType=0", kde)
        self.assertIn("[Other]", kde)

    def test_quit_without_a_snapshot_does_nothing(self):
        with patch.object(proxy, "verify_direct", side_effect=AssertionError("checked")):
            proxy.quit_restore()
        self.assertFalse(proxy.snapshot_path().exists())


class GuardTests(unittest.TestCase):
    def test_real_runner_refuses_firewall_dns_and_vpn_commands(self):
        for command in (
            "nft",
            "iptables",
            "nmcli",
            "resolvectl",
            "tailscale",
            "protonvpn",
            "pvpn-killswitch",
        ):
            with self.assertRaises(OnionError) as caught:
                proxy._run([command, "list"])
            self.assertIn(command, str(caught.exception))


class HealthTests(unittest.TestCase):
    def test_tor_check_requires_the_api_flag(self):
        self.assertTrue(health.interpret_tor_check('{"IsTor": true, "IP": "192.0.2.10"}')["IsTor"])
        with self.assertRaises(OnionError):
            health.interpret_tor_check('{"IsTor": false, "IP": "192.0.2.10"}')
        with self.assertRaises(OnionError):
            health.interpret_tor_check("not json")

    def test_socks5_handshake_sends_the_hostname_to_the_proxy(self):
        ready = threading.Event()
        seen = {}

        def serve():
            listener = socket.socket()
            listener.bind(("127.0.0.1", 0))
            listener.listen(1)
            seen["port"] = listener.getsockname()[1]
            ready.set()
            conn, _addr = listener.accept()
            greeting = conn.recv(3)
            seen["greeting"] = greeting
            conn.sendall(b"\x05\x00")
            request = conn.recv(64)
            seen["request"] = request
            conn.sendall(b"\x05\x00\x00\x01\x00\x00\x00\x00\x00\x00")
            conn.close()
            listener.close()

        thread = threading.Thread(target=serve)
        thread.start()
        self.assertTrue(ready.wait(2))
        sock = health.socks5_connect("127.0.0.1", seen["port"], "check.torproject.org", 443, 2)
        sock.close()
        thread.join(2)
        self.assertEqual(seen["greeting"], b"\x05\x01\x00")
        host = b"check.torproject.org"
        self.assertIn(b"\x05\x01\x00\x03" + bytes([len(host)]) + host, seen["request"])

    def test_direct_check_accepts_tcp_when_ping_is_blocked(self):
        with patch.object(health, "tcp_connect", return_value=True), \
             patch.object(health, "ping", return_value=False), \
             patch.object(health, "resolve_dns", return_value=(True, "DNS: resolved one.one.one.one")):
            report = health.verify_direct()
        self.assertTrue(report.ok)
        self.assertIn("1.1.1.1: TCP connection succeeded", report.text())
        self.assertIn("8.8.8.8: TCP connection succeeded", report.text())

    def test_direct_check_fails_when_both_paths_fail(self):
        with patch.object(health, "tcp_connect", return_value=False), \
             patch.object(health, "ping", return_value=False), \
             patch.object(health, "resolve_dns", return_value=(False, "DNS: could not resolve a name")):
            report = health.verify_direct()
        self.assertFalse(report.ok)
        self.assertIn("8.8.8.8: no TCP connection and no ping reply", report.text())
