"""Packaging metadata stays aligned with the app version and distro names."""

import pathlib
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]


class PackageMetadataTests(unittest.TestCase):
    def test_version_matches_app(self):
        init = (ROOT / "onionfruitux" / "__init__.py").read_text(encoding="utf-8")
        self.assertIn('__version__ = "1.0.0"', init)
        spec = (ROOT / "packaging" / "onionfruitux.spec").read_text(encoding="utf-8")
        pkgbuild = (ROOT / "packaging" / "PKGBUILD").read_text(encoding="utf-8")
        self.assertIn("Version: 1.0.0", spec)
        self.assertIn("pkgver=1.0.0", pkgbuild)

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
        workflow = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
        self.assertIn("softprops/action-gh-release", workflow)
        self.assertIn('tags:', workflow)
