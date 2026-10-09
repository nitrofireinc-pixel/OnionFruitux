#!/bin/sh
# Install one package inside a throwaway root and check the command and manual.
# Does not run `onionfruitux connect` and does not apply firewall rules.
set -eu
trap 'echo "smoke failed at line $LINENO" >&2; exit 1' ERR

pkg=${1:?package path}
export PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"

if [ "$(id -u)" -ne 0 ]; then
  echo "smoke.sh must run as root" >&2
  exit 1
fi

mkdir -p /usr/sbin /etc/systemd/system
printf '%s\n' '#!/bin/sh' 'exit 101' > /usr/sbin/policy-rc.d
chmod 755 /usr/sbin/policy-rc.d

# Official container images omit documentation. A desktop install does not.
# Turn that off so this test can see the manual page inside the package.
rm -f /etc/dpkg/dpkg.cfg.d/excludes
if [ -f /etc/dnf/dnf.conf ]; then
  sed -i 's/^tsflags=nodocs/# tsflags=nodocs/' /etc/dnf/dnf.conf
fi
if [ -f /etc/pacman.conf ]; then
  sed -i '\|^NoExtract *= *usr/share/man/|s/^/# /' /etc/pacman.conf
fi
mkdir -p /etc/zypp /etc/rpm
if [ -d /etc/zypp ]; then
  printf '%s\n' 'rpm.install.excludedocs = 0' > /etc/zypp/zypp.conf
fi
printf '%s\n' '%_excludedocs 0' > /etc/rpm/macros
# Keep OnionFruitux from being started. Do not mask tor.service: a masked
# unit makes some distro maintainer scripts fail their preset step.
ln -sfn /dev/null /etc/systemd/system/onionfruitux.service

case "$pkg" in
  *.deb)
    apt-get update
    DEBIAN_FRONTEND=noninteractive apt-get install -y "$pkg"
    arch=$(dpkg-query -W -f '${Architecture}' onionfruitux)
    [ "$arch" = "all" ]
    for dep in tor nftables python3 python3-pyqt6 pkexec polkitd xdg-utils obfs4proxy snowflake-client; do
      dpkg -s "$dep" >/dev/null
    done
    ;;
  *.rpm)
    if command -v dnf >/dev/null 2>&1; then
      if ! dnf install -y "$pkg"; then
        dnf --nogpgcheck install -y "$pkg"
      fi
      command -v obfs4proxy >/dev/null
    elif command -v zypper >/dev/null 2>&1; then
      zypper --non-interactive install --allow-unsigned-rpm "$pkg"
      # Some container images tell zypper to skip documentation. The rpm
      # itself contains the manual; rpm applies it when docs are included.
      if [ ! -f /usr/share/man/man1/onionfruitux.1.gz ]; then
        echo "zypper skipped documentation; installing the rpm payload with rpm" >&2
        rpm -Uvh --replacepkgs "$pkg"
      fi
      command -v obfs4proxy >/dev/null
    else
      echo "no rpm installer" >&2
      exit 1
    fi
    arch=$(rpm -q --qf '%{ARCH}' onionfruitux)
    [ "$arch" = "noarch" ]
    command -v tor >/dev/null
    command -v nft >/dev/null
    command -v python3 >/dev/null
    command -v pkexec >/dev/null
    python3 -c 'import PyQt6'
    ;;
  *.pkg.tar.zst)
    pacman -Sy --needed --noconfirm python tor nftables python-pyqt6 polkit xdg-utils
    pacman -U --noconfirm "$pkg"
    ;;
  *)
    echo "unknown package: $pkg" >&2
    exit 1
    ;;
esac

need_man=0
if ! command -v man >/dev/null 2>&1 || grep -q 'been minimized' /usr/bin/man 2>/dev/null; then
  need_man=1
fi
if [ "$need_man" -eq 1 ]; then
  if command -v apt-get >/dev/null 2>&1; then
    DEBIAN_FRONTEND=noninteractive apt-get install -y man-db
  elif command -v dnf >/dev/null 2>&1; then
    dnf install -y man-db
  elif command -v zypper >/dev/null 2>&1; then
    zypper --non-interactive install man
  elif command -v pacman >/dev/null 2>&1; then
    pacman -S --needed --noconfirm man-db
  fi
fi
if ! command -v col >/dev/null 2>&1; then
  if command -v apt-get >/dev/null 2>&1; then
    DEBIAN_FRONTEND=noninteractive apt-get install -y bsdextrautils
  elif command -v dnf >/dev/null 2>&1; then
    dnf install -y /usr/bin/col
  elif command -v zypper >/dev/null 2>&1; then
    zypper --non-interactive install bsdextrautils || zypper --non-interactive install util-linux
  fi
fi
man_bin=man
if [ -x /usr/bin/man.REAL ]; then
  man_bin=/usr/bin/man.REAL
fi

test -x /usr/bin/onionfruitux
echo "manual: $(ls -l /usr/share/man/man1/onionfruitux.1.gz 2>&1)"
test -f /usr/share/man/man1/onionfruitux.1.gz
test -f /usr/share/applications/onionfruitux.desktop
test -f /usr/share/icons/hicolor/scalable/apps/onionfruitux.svg
test -f /usr/share/icons/hicolor/48x48/apps/onionfruitux.png
test -f /usr/share/icons/hicolor/256x256/apps/onionfruitux.png
grep -q '^Icon=onionfruitux$' /usr/share/applications/onionfruitux.desktop
grep -q '^StartupWMClass=onionfruitux$' /usr/share/applications/onionfruitux.desktop
test -f /usr/share/polkit-1/actions/com.nitrofire.onionfruitux.policy
test -f /usr/lib/systemd/system/onionfruitux.service
id onionfruitux >/dev/null

plan=$(onionfruitux plan)
printf '%s\n' "$plan" | grep -F 'This command changes nothing' >/dev/null
printf '%s\n' "$plan" | grep -F 'table inet onionfruitux' >/dev/null
onionfruitux status | grep -F 'OnionFruitux is off' >/dev/null
onionfruitux doctor

# man may emit overstrike bold (o^Ho). Strip that before matching.
MANPAGER=cat PAGER=cat "$man_bin" -P cat onionfruitux | sed 's/.\x08//g' | grep -F 'onionfruitux' >/dev/null

if [ -f /var/lib/onionfruitux/state.json ]; then
  echo "state file appeared without turning the switch on" >&2
  exit 1
fi
if command -v nft >/dev/null 2>&1; then
  if tables=$(nft list tables 2>/dev/null); then
    if printf '%s\n' "$tables" | grep -q onionfruitux; then
      echo "nftables table onionfruitux was installed" >&2
      exit 1
    fi
  fi
fi
if ps -eo comm 2>/dev/null | grep -x tor >/dev/null; then
  echo "tor is running" >&2
  exit 1
fi

echo SMOKE_OK
