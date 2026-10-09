Name: onionfruitux
Version: 1.0.4
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
if command -v gtk-update-icon-cache >/dev/null 2>&1; then
  gtk-update-icon-cache -f /usr/share/icons/hicolor >/dev/null 2>&1 || true
fi
if command -v update-desktop-database >/dev/null 2>&1; then
  update-desktop-database /usr/share/applications >/dev/null 2>&1 || true
fi

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
if command -v gtk-update-icon-cache >/dev/null 2>&1; then
  gtk-update-icon-cache -f /usr/share/icons/hicolor >/dev/null 2>&1 || true
fi
if command -v update-desktop-database >/dev/null 2>&1; then
  update-desktop-database /usr/share/applications >/dev/null 2>&1 || true
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
/usr/share/icons/hicolor/16x16/apps/onionfruitux.png
/usr/share/icons/hicolor/32x32/apps/onionfruitux.png
/usr/share/icons/hicolor/48x48/apps/onionfruitux.png
/usr/share/icons/hicolor/64x64/apps/onionfruitux.png
/usr/share/icons/hicolor/128x128/apps/onionfruitux.png
/usr/share/icons/hicolor/256x256/apps/onionfruitux.png
/usr/share/polkit-1/actions/com.nitrofire.onionfruitux.policy
/usr/lib/systemd/system/onionfruitux.service

%changelog
* Fri Oct 09 2026 Nitrofire Computing <nitrofireinc@gmail.com> - 1.0.4-1
- Use the onion icon for the open window and the tray.

* Fri Oct 09 2026 Nitrofire Computing <nitrofireinc@gmail.com> - 1.0.3-1
- Accept redirected packets so the computer can actually use Tor.
- Install the OnionFruitux icon at the sizes desktops look up.

* Fri Oct 09 2026 Nitrofire Computing <nitrofireinc@gmail.com> - 1.0.2-1
- Bind OnionFruitux's Tor to its own ports so it can run beside Ubuntu's Tor and mDNS.
- Read Tor's log instead of connecting to TransPort, which crashed Tor 0.4.9.

* Fri Oct 09 2026 Nitrofire Computing <nitrofireinc@gmail.com> - 1.0.1-1
- Keep the window responsive while connecting, and roll the firewall back on failure.
- Add onionfruitux panic-off.

* Fri Oct 09 2026 Nitrofire Computing <nitrofireinc@gmail.com> - 1.0.0-1
- Package the OnionFruitux window, tray, and command line.
