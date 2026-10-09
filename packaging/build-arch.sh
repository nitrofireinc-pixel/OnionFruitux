#!/bin/sh
# Build an Arch package with makepkg. Run this on Arch, or:
#   docker run --rm -v "$PWD":/src -w /src archlinux:base bash packaging/build-arch.sh
set -eu

ROOT=$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)

if ! command -v pacman >/dev/null 2>&1; then
  echo "onionfruitux: build-arch.sh runs on Arch Linux (pacman was not found)" >&2
  exit 1
fi

pacman -Sy --noconfirm base-devel gzip

VERSION=$(sed -n 's/^__version__ = "\(.*\)"/\1/p' "$ROOT/onionfruitux/__init__.py")
WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT
mkdir -p "$WORK/src"
cp -a "$ROOT/." "$WORK/src/"
rm -rf "$WORK/src/.git" "$WORK/src/dist"
sed -i "s/^pkgver=.*/pkgver=${VERSION}/" "$WORK/src/packaging/PKGBUILD"

if ! id -u builder >/dev/null 2>&1; then
  useradd -m builder
fi
chown -R builder:builder "$WORK"

su -s /bin/bash builder -c "cd '$WORK/src/packaging' && makepkg -f --noconfirm --nodeps"

mkdir -p "$ROOT/dist"
cp "$WORK/src/packaging/${pkgname:-onionfruitux}-${VERSION}-"*.pkg.tar.zst "$ROOT/dist/"
ls -1 "$ROOT/dist/onionfruitux-${VERSION}-"*.pkg.tar.zst
