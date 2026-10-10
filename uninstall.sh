#!/bin/sh
# Remove OnionFruitux. Tor and nftables packages are left installed.
set -eu

PURGE=0
if [ "${1:-}" = "--purge" ]; then
  PURGE=1
elif [ -n "${1:-}" ]; then
  echo "onionfruitux: unknown option $1 (expected --purge)" >&2
  exit 1
fi

if [ "$(id -u)" -ne 0 ]; then
  echo "onionfruitux: run sudo ./uninstall.sh" >&2
  exit 1
fi

if command -v systemctl >/dev/null 2>&1; then
  systemctl disable --now onionfruitux.service >/dev/null 2>&1 || true
fi

if [ -x /usr/bin/onionfruitux ]; then
  /usr/bin/onionfruitux --system disconnect >/dev/null 2>&1 || true
fi

if command -v nft >/dev/null 2>&1; then
  nft delete table inet onionfruitux >/dev/null 2>&1 || true
fi

if [ -f /run/onionfruitux.pid ]; then
  pid=$(cat /run/onionfruitux.pid 2>/dev/null || true)
  if [ -n "$pid" ]; then
    kill "$pid" >/dev/null 2>&1 || true
  fi
  rm -f /run/onionfruitux.pid
fi

rm -f /usr/bin/onionfruitux
rm -rf /usr/lib/onionfruitux
rm -f /usr/share/applications/onionfruitux.desktop
rm -f /usr/share/icons/hicolor/scalable/apps/onionfruitux.svg
for size in 16 32 48 64 128 256; do
  rm -f "/usr/share/icons/hicolor/${size}x${size}/apps/onionfruitux.png"
done
rm -f /usr/share/polkit-1/actions/com.nitrofire.onionfruitux.policy
rm -f /usr/lib/systemd/system/onionfruitux.service
rm -f /lib/systemd/system/onionfruitux.service
rm -f /etc/systemd/system/onionfruitux.service
rm -f /usr/share/man/man1/onionfruitux.1
rm -f /usr/share/man/man1/onionfruitux.1.gz
rm -f /etc/onionfruitux/boot.json
rmdir /etc/onionfruitux 2>/dev/null || true

if command -v mandb >/dev/null 2>&1; then
  mandb -q >/dev/null 2>&1 || true
fi
if command -v systemctl >/dev/null 2>&1; then
  systemctl daemon-reload >/dev/null 2>&1 || true
fi
if command -v update-desktop-database >/dev/null 2>&1; then
  update-desktop-database /usr/share/applications >/dev/null 2>&1 || true
fi
if command -v gtk-update-icon-cache >/dev/null 2>&1; then
  gtk-update-icon-cache -f /usr/share/icons/hicolor >/dev/null 2>&1 || true
fi

if [ "$PURGE" -eq 1 ]; then
  if id -u onionfruitux >/dev/null 2>&1; then
    userdel onionfruitux >/dev/null 2>&1 || true
  fi
  rm -rf /var/lib/onionfruitux
  echo "Purged OnionFruitux, the onionfruitux user, and /var/lib/onionfruitux."
else
  echo "Removed OnionFruitux. The tor and nftables packages are still installed."
fi
