#!/bin/sh
# Build a Debian/Ubuntu package. Architecture is all because the app is Python.
set -eu

ROOT=$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)
VERSION=$(sed -n 's/^__version__ = "\(.*\)"/\1/p' "$ROOT/onionfruitux/__init__.py")
REV=1
STAGE=$(mktemp -d)
trap 'rm -rf "$STAGE"' EXIT

sh "$ROOT/packaging/stage.sh" "$STAGE/root"
mkdir -p "$STAGE/root/DEBIAN"

SIZE=$(du -sk "$STAGE/root" | awk '{print $1}')
cat > "$STAGE/root/DEBIAN/control" <<EOF
Package: onionfruitux
Version: ${VERSION}-${REV}
Architecture: all
Maintainer: Nitrofire Computing <nitrofireinc@gmail.com>
Section: net
Priority: optional
Homepage: https://github.com/nitrofireinc-pixel/OnionFruitux
Installed-Size: ${SIZE}
Depends: python3, tor, nftables, python3-pyqt6, pkexec, polkitd | policykit-1, xdg-utils, obfs4proxy, snowflake-client
Description: Send this computer's internet through Tor
 OnionFruitux is a window, a tray icon, and a command line that route
 a Linux computer through Tor. Turning the switch off removes only the
 onionfruitux firewall table and the Tor process this app started.
EOF

cat > "$STAGE/root/DEBIAN/postinst" <<'EOF'
#!/bin/sh
set -e
case "$1" in
  configure)
    /usr/share/onionfruitux/setup-account.sh
    ;;
esac
exit 0
EOF

cat > "$STAGE/root/DEBIAN/prerm" <<'EOF'
#!/bin/sh
set -e
case "$1" in
  remove|deconfigure)
    if [ -x /usr/share/onionfruitux/stop.sh ]; then
      /usr/share/onionfruitux/stop.sh || true
    fi
    ;;
esac
exit 0
EOF

cat > "$STAGE/root/DEBIAN/postrm" <<'EOF'
#!/bin/sh
set -e
case "$1" in
  purge)
    if id -u onionfruitux >/dev/null 2>&1; then
      userdel onionfruitux >/dev/null 2>&1 || true
    fi
    rm -rf /var/lib/onionfruitux /etc/onionfruitux
    ;;
esac
if [ "$1" = "remove" ] || [ "$1" = "purge" ]; then
  if [ -d /run/systemd/system ] && command -v systemctl >/dev/null 2>&1; then
    systemctl daemon-reload >/dev/null 2>&1 || true
  fi
  if command -v mandb >/dev/null 2>&1; then
    mandb -q >/dev/null 2>&1 || true
  fi
fi
exit 0
EOF

chmod 755 "$STAGE/root/DEBIAN/postinst" "$STAGE/root/DEBIAN/prerm" "$STAGE/root/DEBIAN/postrm"

mkdir -p "$ROOT/dist"
OUT="$ROOT/dist/onionfruitux_${VERSION}-${REV}_all.deb"
dpkg-deb --root-owner-group --build "$STAGE/root" "$OUT" >/dev/null
echo "$OUT"
