#!/bin/sh
# Turn OnionFruitux off if a package removal finds it running.
# This never starts Tor and never installs firewall rules.
if [ -d /run/systemd/system ] && command -v systemctl >/dev/null 2>&1; then
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
  if [ -n "${pid:-}" ]; then
    kill "$pid" >/dev/null 2>&1 || true
  fi
  rm -f /run/onionfruitux.pid
fi
exit 0
