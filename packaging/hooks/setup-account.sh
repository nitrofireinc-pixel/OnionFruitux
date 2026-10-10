#!/bin/sh
# Create the Tor system user and data directories. Does not start Tor.
# Do not disable tor.service. The tor package may start Ubuntu's own Tor on
# port 9050. OnionFruitux binds different ports and leaves that service alone.
set -eu

if ! id -u onionfruitux >/dev/null 2>&1; then
  if ! useradd --system --home-dir /var/lib/onionfruitux --shell /usr/sbin/nologin onionfruitux 2>/dev/null; then
    useradd --system --home-dir /var/lib/onionfruitux --shell /bin/false onionfruitux
  fi
fi

mkdir -p /var/lib/onionfruitux/tor /etc/onionfruitux
chown root:root /var/lib/onionfruitux /etc/onionfruitux
chmod 755 /var/lib/onionfruitux /etc/onionfruitux
chown onionfruitux:onionfruitux /var/lib/onionfruitux/tor
chmod 700 /var/lib/onionfruitux/tor

if [ -d /run/systemd/system ] && command -v systemctl >/dev/null 2>&1; then
  systemctl daemon-reload >/dev/null 2>&1 || true
fi
if command -v mandb >/dev/null 2>&1; then
  mandb -q >/dev/null 2>&1 || true
fi
if command -v update-desktop-database >/dev/null 2>&1; then
  update-desktop-database /usr/share/applications >/dev/null 2>&1 || true
fi
