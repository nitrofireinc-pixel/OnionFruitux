#!/bin/sh
# Copy OnionFruitux into a package root at $1. Does not install dependencies.
set -eu

if [ "$#" -ne 1 ] || [ -z "$1" ]; then
  echo "usage: stage.sh DEST" >&2
  exit 1
fi

ROOT=$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)
DEST=$1

if [ -d "$DEST" ]; then
  find "$DEST" -mindepth 1 -maxdepth 1 -exec rm -rf {} +
else
  mkdir -p "$DEST"
fi

mkdir -p \
  "$DEST/usr/bin" \
  "$DEST/usr/lib/onionfruitux" \
  "$DEST/usr/share/onionfruitux" \
  "$DEST/usr/share/man/man1" \
  "$DEST/usr/share/applications" \
  "$DEST/usr/share/icons/hicolor/scalable/apps" \
  "$DEST/usr/share/icons/hicolor/16x16/apps" \
  "$DEST/usr/share/icons/hicolor/32x32/apps" \
  "$DEST/usr/share/icons/hicolor/48x48/apps" \
  "$DEST/usr/share/icons/hicolor/64x64/apps" \
  "$DEST/usr/share/icons/hicolor/128x128/apps" \
  "$DEST/usr/share/icons/hicolor/256x256/apps" \
  "$DEST/usr/share/polkit-1/actions" \
  "$DEST/usr/lib/systemd/system" \
  "$DEST/usr/share/doc/onionfruitux" \
  "$DEST/usr/share/licenses/onionfruitux"

cp -a "$ROOT/onionfruitux" "$DEST/usr/lib/onionfruitux/onionfruitux"
find "$DEST/usr/lib/onionfruitux" -type d -name __pycache__ -exec rm -rf {} +
find "$DEST/usr/lib/onionfruitux" -type f -name '*.pyc' -delete

cat > "$DEST/usr/bin/onionfruitux" <<'EOF'
#!/bin/sh
export PYTHONPATH="/usr/lib/onionfruitux${PYTHONPATH:+:$PYTHONPATH}"
exec -a onionfruitux python3 -m onionfruitux "$@"
EOF
chmod 755 "$DEST/usr/bin/onionfruitux"

gzip -c -n -9 "$ROOT/share/man/onionfruitux.1" > "$DEST/usr/share/man/man1/onionfruitux.1.gz"
chmod 644 "$DEST/usr/share/man/man1/onionfruitux.1.gz"

install -m 644 "$ROOT/share/onionfruitux.desktop" "$DEST/usr/share/applications/onionfruitux.desktop"
install -m 644 "$ROOT/share/onionfruitux.svg" "$DEST/usr/share/icons/hicolor/scalable/apps/onionfruitux.svg"
for size in 16 32 48 64 128 256; do
  install -m 644 "$ROOT/share/icons/hicolor/${size}x${size}/apps/onionfruitux.png" \
    "$DEST/usr/share/icons/hicolor/${size}x${size}/apps/onionfruitux.png"
done
install -m 644 "$ROOT/share/com.nitrofire.onionfruitux.policy" \
  "$DEST/usr/share/polkit-1/actions/com.nitrofire.onionfruitux.policy"
install -m 644 "$ROOT/share/onionfruitux.service" "$DEST/usr/lib/systemd/system/onionfruitux.service"
install -m 755 "$ROOT/packaging/hooks/setup-account.sh" "$DEST/usr/share/onionfruitux/setup-account.sh"
install -m 755 "$ROOT/packaging/hooks/stop.sh" "$DEST/usr/share/onionfruitux/stop.sh"
install -m 644 "$ROOT/LICENSE" "$DEST/usr/share/doc/onionfruitux/copyright"
install -m 644 "$ROOT/LICENSE" "$DEST/usr/share/licenses/onionfruitux/LICENSE"
