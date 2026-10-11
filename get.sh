#!/bin/sh
# Install OnionFruitux from the latest GitHub release.
#   curl -fsSL https://raw.githubusercontent.com/nitrofireinc-pixel/OnionFruitux/main/get.sh | sudo bash
set -eu

REPO=nitrofireinc-pixel/OnionFruitux

die() {
  echo "onionfruitux: $*" >&2
  exit 1
}

if [ "$(id -u)" -ne 0 ]; then
  die "run as root: curl -fsSL https://raw.githubusercontent.com/${REPO}/main/get.sh | sudo bash"
fi

if [ -r /etc/os-release ]; then
  # shellcheck disable=SC1091
  . /etc/os-release
fi

detect_pm() {
  blob=" ${ID:-} ${ID_LIKE:-} "
  case "$blob" in
    *arch*|*manjaro*)
      echo pacman
      return
      ;;
  esac
  case "$blob" in
    *opensuse*|*suse*|*sles*)
      echo zypper
      return
      ;;
  esac
  case "$blob" in
    *fedora*|*rhel*|*centos*)
      echo dnf
      return
      ;;
  esac
  case "$blob" in
    *debian*|*ubuntu*|*linuxmint*|*pop*)
      echo apt
      return
      ;;
  esac
  if command -v apt-get >/dev/null 2>&1; then
    echo apt
    return
  fi
  if command -v dnf >/dev/null 2>&1; then
    echo dnf
    return
  fi
  if command -v pacman >/dev/null 2>&1; then
    echo pacman
    return
  fi
  if command -v zypper >/dev/null 2>&1; then
    echo zypper
    return
  fi
  echo unknown
}

fetch() {
  url=$1
  dest=$2
  if command -v curl >/dev/null 2>&1; then
    curl -fsSL "$url" -o "$dest"
  elif command -v wget >/dev/null 2>&1; then
    wget -q -O "$dest" "$url"
  else
    die "curl or wget is required to download the package"
  fi
}

PM=$(detect_pm)
if [ "$PM" = "unknown" ]; then
  die "could not find apt, dnf, pacman, or zypper"
fi

case "$PM" in
  apt) suffix='_all\.deb' ;;
  dnf|zypper) suffix='\.noarch\.rpm' ;;
  pacman) suffix='\.pkg\.tar\.zst' ;;
esac

tmpdir=$(mktemp -d)
trap 'rm -rf "$tmpdir"' EXIT

if [ -n "${ONIONFRUITUX_PACKAGE:-}" ]; then
  pkg=$ONIONFRUITUX_PACKAGE
  [ -f "$pkg" ] || die "package not found: $pkg"
else
  fetch "https://api.github.com/repos/${REPO}/releases/latest" "$tmpdir/release.json"
  url=$(grep -o "https://github.com/${REPO}/releases/download/[^\"]*${suffix}" "$tmpdir/release.json" | head -n 1 || true)
  if [ -z "$url" ]; then
    die "the latest GitHub release has no matching package. See https://github.com/${REPO}/releases/latest"
  fi
  pkg="$tmpdir/$(basename "$url")"
  fetch "$url" "$pkg"
fi

case "$PM" in
  apt)
    apt-get update
    DEBIAN_FRONTEND=noninteractive apt-get install -y "$pkg"
    ;;
  dnf)
    if ! dnf install -y "$pkg"; then
      dnf --nogpgcheck install -y "$pkg"
    fi
    ;;
  zypper)
    zypper --non-interactive install --allow-unsigned-rpm "$pkg"
    ;;
  pacman)
    pacman -Sy --needed --noconfirm python tor nftables python-pyqt6 polkit xdg-utils
    pacman -U --noconfirm "$pkg"
    ;;
esac

echo "Installed OnionFruitux."
echo "Manual: man onionfruitux"
echo "Window: onionfruitux"
echo "Tray:   onionfruitux tray"
