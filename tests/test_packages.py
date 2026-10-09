"""Packaging metadata stays aligned with the app version and distro names."""

import os
import pathlib
import subprocess
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]


class PackageMetadataTests(unittest.TestCase):
    def test_version_matches_app(self):
        init = (ROOT / "onionfruitux" / "__init__.py").read_text(encoding="utf-8")
        self.assertIn('__version__ = "1.0.4"', init)
        spec = (ROOT / "packaging" / "onionfruitux.spec").read_text(encoding="utf-8")
        pkgbuild = (ROOT / "packaging" / "PKGBUILD").read_text(encoding="utf-8")
        self.assertIn("Version: 1.0.4", spec)
        self.assertIn("pkgver=1.0.4", pkgbuild)

    def test_deb_depends_and_arch(self):
        script = (ROOT / "packaging" / "build-deb.sh").read_text(encoding="utf-8")
        self.assertIn("Architecture: all", script)
        for dep in (
            "python3",
            "tor",
            "nftables",
            "python3-pyqt6",
            "pkexec",
            "polkitd | policykit-1",
            "xdg-utils",
            "obfs4proxy",
            "snowflake-client",
        ):
            self.assertIn(dep, script)

    def test_rpm_requires_and_noarch(self):
        spec = (ROOT / "packaging" / "onionfruitux.spec").read_text(encoding="utf-8")
        self.assertIn("BuildArch: noarch", spec)
        for dep in (
            "Requires: python3",
            "Requires: tor",
            "Requires: nftables",
            "Requires: polkit",
            "Requires: xdg-utils",
            "Requires: python3-PyQt6",
            "Requires: obfs4",
            "Requires: /usr/bin/pkexec",
        ):
            self.assertIn(dep, spec)

    def test_arch_any_and_get_script(self):
        pkgbuild = (ROOT / "packaging" / "PKGBUILD").read_text(encoding="utf-8")
        self.assertIn("arch=('any')", pkgbuild)
        getter = (ROOT / "get.sh").read_text(encoding="utf-8")
        self.assertIn("releases/latest", getter)
        self.assertIn("apt-get install -y", getter)
        self.assertIn("dnf install -y", getter)
        self.assertIn("zypper --non-interactive install --allow-unsigned-rpm", getter)
        self.assertIn("pacman -U", getter)
        desktop = (ROOT / "share/onionfruitux.desktop").read_text(encoding="utf-8")
        self.assertIn("Icon=onionfruitux\n", desktop)
        for size in (16, 32, 48, 64, 128, 256):
            icon = ROOT / "share/icons/hicolor" / f"{size}x{size}" / "apps" / "onionfruitux.png"
            self.assertTrue(icon.is_file(), icon)
            self.assertTrue(icon.read_bytes().startswith(b"\x89PNG"))
        stage = (ROOT / "packaging/stage.sh").read_text(encoding="utf-8")
        deb = (ROOT / "packaging/build-deb.sh").read_text(encoding="utf-8")
        self.assertIn("for size in 16 32 48 64 128 256", stage)
        self.assertIn("${size}x${size}/apps/onionfruitux.png", stage)
        self.assertIn("gtk-update-icon-cache", deb)
        self.assertIn("update-desktop-database", deb)
        workflow = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
        self.assertIn("softprops/action-gh-release", workflow)
        self.assertIn('tags:', workflow)

    def test_launcher_runs_under_dash(self):
        launcher = ROOT / "packaging" / "onionfruitux-bin"
        text = launcher.read_text(encoding="utf-8")
        self.assertNotIn("exec -a", text)
        self.assertTrue(text.startswith("#!/bin/sh\n"))
        env = os.environ.copy()
        env["PYTHONPATH"] = str(ROOT)
        result = subprocess.run(
            ["dash", str(launcher), "plan"],
            cwd=ROOT,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("This command changes nothing", result.stdout)
        self.assertIn("table inet onionfruitux", result.stdout)

    def test_process_comm_is_the_app_name(self):
        code = (
            "from onionfruitux.__main__ import _name_process\n"
            "_name_process()\n"
            "print(open('/proc/self/comm', encoding='ascii').read().strip())\n"
        )
        result = subprocess.run(
            [sys.executable, "-c", code],
            cwd=ROOT,
            env={**os.environ, "PYTHONPATH": str(ROOT)},
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "onionfruitux")
