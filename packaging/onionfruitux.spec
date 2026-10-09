Name: onionfruitux
Version: 1.0.1
Release: 1
Summary: Send this computer's internet through Tor
License: GPL-3.0-or-later
URL: https://github.com/nitrofireinc-pixel/OnionFruitux
BuildArch: noarch
AutoReqProv: no

# rpmbuild otherwise marks the manual as documentation, and some installers
# skip documentation files. The manual is part of the application.
%global __docdir_path %{_datadir}/doc

# Names that exist on both Fedora and openSUSE. python3 and python3-PyQt6
# are package names on Fedora and Provides on openSUSE (python313, python313-PyQt6).
# obfs4 ships /usr/bin/obfs4proxy. pkexec is a file in Fedora's polkit package
# and its own package on openSUSE. snowflake-client is not in either distro's
# default repositories.
Requires: python3
Requires: tor
Requires: nftables
Requires: polkit
Requires: xdg-utils
Requires: python3-PyQt6
Requires: obfs4
Requires: /usr/bin/pkexec
Requires: /usr/sbin/useradd

%global debug_package %{nil}

%description
OnionFruitux is a window, a tray icon, and a command line that route a
Linux computer through Tor. Turning the switch off removes only the
onionfruitux firewall table and the Tor process this app started.

%prep

%build

%install
rm -rf %{buildroot}
mkdir -p %{buildroot}
cp -a %{_sourcedir}/rootfs/. %{buildroot}/

%post
/usr/share/onionfruitux/setup-account.sh

%preun
if [ "$1" = 0 ] && [ -x /usr/share/onionfruitux/stop.sh ]; then
  /usr/share/onionfruitux/stop.sh || true
fi

%postun
if [ "$1" = 0 ]; then
  if [ -d /run/systemd/system ] && command -v systemctl >/dev/null 2>&1; then
    systemctl daemon-reload >/dev/null 2>&1 || true
  fi
fi

%files
%license /usr/share/licenses/onionfruitux/LICENSE
/usr/bin/onionfruitux
/usr/lib/onionfruitux
/usr/share/onionfruitux
/usr/share/doc/onionfruitux
/usr/share/man/man1/onionfruitux.1.gz
/usr/share/applications/onionfruitux.desktop
/usr/share/icons/hicolor/scalable/apps/onionfruitux.svg
/usr/share/polkit-1/actions/com.nitrofire.onionfruitux.policy
/usr/lib/systemd/system/onionfruitux.service

%changelog
* Fri Oct 09 2026 Nitrofire Computing <nitrofireinc@gmail.com> - 1.0.1-1
- Keep the window responsive while connecting, and roll the firewall back on failure.
- Add onionfruitux panic-off.

* Fri Oct 09 2026 Nitrofire Computing <nitrofireinc@gmail.com> - 1.0.0-1
- Package the OnionFruitux window, tray, and command line.
