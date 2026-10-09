"""Command line behavior that must not touch the network."""

from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from io import StringIO
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from onionfruitux import cli
from onionfruitux.config import Config, NetworkSettings, load_config
from onionfruitux.errors import OnionError
from onionfruitux.paths import CHECK_URL


class CliTests(unittest.TestCase):
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

    def test_route_add_list_use(self):
        out = StringIO()
        with redirect_stdout(out):
            self.assertEqual(
                cli.main(["route", "add", "US exit", "--entry", "DE", "--exit", "us"]),
                0,
            )
            self.assertEqual(
                cli.main(["route", "add", "Snowflake", "--bridge", "snowflake"]),
                0,
            )
            self.assertEqual(cli.main(["route", "use", "US exit"]), 0)
            self.assertEqual(cli.main(["route", "list"]), 0)
        text = out.getvalue()
        self.assertIn("* US exit", text)
        self.assertIn("entry de", text)
        self.assertIn("exit us", text)
        self.assertIn("Snowflake", text)
        self.assertIn("bridge snowflake", text)
        cfg = load_config()
        self.assertEqual(cfg.active_route, "US exit")

    def test_plan_prints_rules_and_does_not_connect(self):
        cli.main(["route", "add", "US exit", "--entry", "de", "--exit", "us"])
        out = StringIO()
        with patch("onionfruitux.system.connect", side_effect=AssertionError("connect")), \
             patch("onionfruitux.system.disconnect", side_effect=AssertionError("disconnect")), \
             patch("onionfruitux.system.apply_ruleset", side_effect=AssertionError("apply")), \
             redirect_stdout(out):
            code = cli.main(["plan"])
        self.assertEqual(code, 0)
        text = out.getvalue()
        self.assertIn("### torrc", text)
        self.assertIn("### nftables", text)
        self.assertIn("table inet onionfruitux", text)
        self.assertIn("EntryNodes {de}", text)
        self.assertIn("changes nothing", text)

    def test_check_page_opens_after_the_firewall_change(self):
        order = []
        cfg = Config(network=NetworkSettings(open_check_page=True))
        with patch.object(cli, "load_config", return_value=cfg), \
             patch.object(cli, "validate_for_connect", return_value=[]), \
             patch.object(cli, "escalate", side_effect=lambda *a, **k: order.append("firewall") or 0), \
             patch.object(cli, "open_check_page", side_effect=lambda: order.append("page")), \
             patch.object(cli.os, "geteuid", return_value=1000), \
             patch.object(cli.system_mod, "connect", side_effect=AssertionError("connect")):
            cli.connect_command()
        self.assertEqual(order, ["firewall", "page"])

    def test_check_page_can_be_turned_off(self):
        cfg = Config(network=NetworkSettings(open_check_page=False))
        with patch.object(cli, "load_config", return_value=cfg), \
             patch.object(cli, "validate_for_connect", return_value=[]), \
             patch.object(cli, "escalate", return_value=0), \
             patch.object(cli, "open_check_page") as page, \
             patch.object(cli.os, "geteuid", return_value=1000):
            cli.connect_command()
            cli.disconnect_command()
        page.assert_not_called()

    def test_disconnect_opens_the_page_after_the_table_is_removed(self):
        order = []
        cfg = Config(network=NetworkSettings(open_check_page=True))
        with patch.object(cli, "load_config", return_value=cfg), \
             patch.object(cli, "escalate", side_effect=lambda *a, **k: order.append("firewall") or 0), \
             patch.object(cli, "open_check_page", side_effect=lambda: order.append("page")), \
             patch.object(cli.os, "geteuid", return_value=1000), \
             patch.object(cli.system_mod, "disconnect", side_effect=AssertionError("disconnect")):
            cli.disconnect_command()
        self.assertEqual(order, ["firewall", "page"])

    def test_privileged_child_does_not_open_the_page_itself(self):
        cfg = Config(network=NetworkSettings(open_check_page=True))
        with patch.object(cli, "_config_for", return_value=cfg), \
             patch.object(cli, "validate_for_connect", return_value=[]), \
             patch.object(cli, "open_check_page") as page, \
             patch.object(cli.os, "geteuid", return_value=0), \
             patch.object(cli.system_mod, "connect") as connect:
            cli.connect_command(privileged=True)
        connect.assert_called_once()
        page.assert_not_called()

    def test_boot_enable_does_not_start_the_service(self):
        cfg = Config()
        with patch.object(cli, "load_config", return_value=cfg), \
             patch.object(cli.os, "geteuid", return_value=0), \
             patch.object(cli.system_mod, "enable_boot") as enable, \
             patch.object(cli.system_mod, "connect", side_effect=AssertionError("connect")):
            cli.main(["boot", "enable"])
        enable.assert_called_once()
        commands = Path("onionfruitux/system.py").read_text(encoding="utf-8")
        self.assertIn('["systemctl", *args]', commands)
        self.assertNotIn("enable --now", commands)
        self.assertNotIn('"start"', commands)

    def test_unknown_country_is_rejected(self):
        err = StringIO()
        with redirect_stderr(err):
            code = cli.main(["route", "add", "nope", "--entry", "zz"])
        self.assertEqual(code, 1)
        self.assertIn("unknown country", err.getvalue())

    def test_check_url(self):
        self.assertEqual(CHECK_URL, "https://check.torproject.org/")

    def test_status_when_off(self):
        out = StringIO()
        with redirect_stdout(out):
            self.assertEqual(cli.main(["status"]), 0)
        self.assertIn("OnionFruitux is off", out.getvalue())

    def test_new_circuit_when_off(self):
        with patch.object(cli.system_mod, "read_status", return_value={"running": False}):
            with self.assertRaises(OnionError):
                cli.new_circuit_command()

    def test_module_plan(self):
        env = os.environ.copy()
        env["XDG_CONFIG_HOME"] = self.tmp.name
        result = subprocess.run(
            ["python3", "-m", "onionfruitux", "plan"],
            capture_output=True,
            text=True,
            cwd=str(Path(__file__).resolve().parents[1]),
            env=env,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("table inet onionfruitux", result.stdout)
        self.assertIn("TransPort", result.stdout)
