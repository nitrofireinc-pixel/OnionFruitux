#!/bin/sh
# Build a noarch RPM for Fedora and openSUSE.
set -eu

ROOT=$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)
if ! command -v rpmbuild >/dev/null 2>&1; then
  echo "onionfruitux: rpmbuild is required (Debian/Ubuntu: apt install rpm)" >&2
  exit 1
fi

VERSION=$(sed -n 's/^__version__ = "\(.*\)"/\1/p' "$ROOT/onionfruitux/__init__.py")
STAGE=$(mktemp -d)
trap 'rm -rf "$STAGE"' EXIT

sh "$ROOT/packaging/stage.sh" "$STAGE/rootfs"
sed "s/^Version:.*/Version: ${VERSION}/" "$ROOT/packaging/onionfruitux.spec" > "$STAGE/onionfruitux.spec"

mkdir -p "$ROOT/dist"
rpmbuild -bb \
  --define "_sourcedir $STAGE" \
  --define "_topdir $STAGE/top" \
  --define "_rpmdir $STAGE/top/RPMS" \
  "$STAGE/onionfruitux.spec"

OUT="$ROOT/dist/onionfruitux-${VERSION}-1.noarch.rpm"
cp "$STAGE/top/RPMS/noarch/onionfruitux-${VERSION}-1.noarch.rpm" "$OUT"
echo "$OUT"
