#!/bin/sh
# Install OnionFruitux, including its manual page.
set -eu

ROOT=$(CDPATH= cd -- "$(dirname "$0")" && pwd)

if [ "$(id -u)" -ne 0 ]; then
  echo "onionfruitux: run sudo ./install.sh" >&2
  exit 1
fi

die() {
  echo "onionfruitux: $*" >&2
  exit 1
}

detect_pm() {
  if [ -r /etc/os-release ]; then
    # shellcheck disable=SC1091
    . /etc/os-release
  fi
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

pkg_available() {
  pkg=$1
  case "$PM" in
    apt)
      apt-cache show "$pkg" >/dev/null 2>&1
      ;;
    dnf)
      dnf -q info "$pkg" >/dev/null 2>&1
      ;;
    pacman)
      pacman -Si "$pkg" >/dev/null 2>&1
      ;;
    zypper)
      zypper --non-interactive search --exact-match "$pkg" 2>/dev/null | grep -q "$pkg"
      ;;
    *)
      return 1
      ;;
  esac
}

pkg_install() {
  case "$PM" in
    apt)
      DEBIAN_FRONTEND=noninteractive apt-get install -y "$@"
      ;;
    dnf)
      dnf install -y "$@"
      ;;
    pacman)
      pacman -S --needed --noconfirm "$@"
      ;;
    zypper)
      zypper --non-interactive install --no-recommends "$@"
      ;;
    *)
      die "no supported package manager"
      ;;
  esac
}

install_required() {
  missing=""
  for pkg in "$@"; do
    if ! pkg_available "$pkg"; then
      missing="$missing $pkg"
    fi
  done
  if [ -n "$missing" ]; then
    die "required packages were not found:$missing"
  fi
  pkg_install "$@"
}

install_optional() {
  for pkg in "$@"; do
    if pkg_available "$pkg"; then
      echo "Installing optional transport $pkg"
      pkg_install "$pkg" || echo "onionfruitux: warning: could not install $pkg" >&2
    else
      echo "Optional package $pkg is not in this distribution's repositories; skipping"
    fi
  done
}

first_available() {
  for pkg in "$@"; do
    if pkg_available "$pkg"; then
      echo "$pkg"
      return 0
    fi
  done
  return 1
}

install_packages() {
  case "$PM" in
    apt)
      apt-get update
      # Debian and Ubuntu ship the daemon as polkitd, not polkit. pkexec is the
      # password prompt. Older releases call the same package policykit-1.
      install_required tor nftables python3 pkexec xdg-utils
      if polkit_pkg=$(first_available polkitd policykit-1 polkit); then
        pkg_install "$polkit_pkg"
      fi
      qt=$(first_available python3-pyqt6 python3-pyside6) || die "python3-pyqt6 (or python3-pyside6) was not found"
      pkg_install "$qt"
      install_optional lyrebird obfs4proxy snowflake-client
      ;;
    dnf)
      install_required tor nftables python3 polkit xdg-utils
      qt=$(first_available python3-pyqt6 python3-PyQt6 python3-pyside6 python3-qt6) || die "PyQt6 was not found"
      pkg_install "$qt"
      install_optional lyrebird obfs4 obfs4proxy snowflake-client
      ;;
    pacman)
      pacman -Sy --noconfirm
      install_required tor nftables python polkit xdg-utils
      qt=$(first_available python-pyqt6 python-pyside6) || die "python-pyqt6 was not found"
      pkg_install "$qt"
      install_optional lyrebird obfs4proxy snowflake-client
      ;;
    zypper)
      zypper --non-interactive refresh
      install_required tor nftables python3 polkit xdg-utils
      qt=$(first_available python3-qt6 python3-PyQt6 python3-pyside6 python3-pyqt6) || die "PyQt6 was not found"
      pkg_install "$qt"
      install_optional lyrebird obfs4proxy snowflake-client
      ;;
    *)
      die "this distribution is not one of Debian, Fedora, Arch, or openSUSE"
      ;;
  esac
}

install_user() {
  if ! id -u onionfruitux >/dev/null 2>&1; then
    if ! useradd --system --home-dir /var/lib/onionfruitux --shell /usr/sbin/nologin onionfruitux; then
      useradd --system --home-dir /var/lib/onionfruitux --shell /bin/false onionfruitux
    fi
  fi
  mkdir -p /var/lib/onionfruitux/tor /etc/onionfruitux
  chown root:root /var/lib/onionfruitux /etc/onionfruitux
  chmod 755 /var/lib/onionfruitux /etc/onionfruitux
  chown onionfruitux:onionfruitux /var/lib/onionfruitux/tor
  chmod 700 /var/lib/onionfruitux/tor
}

install_program() {
  rm -rf /usr/lib/onionfruitux/onionfruitux
  mkdir -p /usr/lib/onionfruitux
  cp -a "$ROOT/onionfruitux" /usr/lib/onionfruitux/onionfruitux
  find /usr/lib/onionfruitux -type d -name __pycache__ -exec rm -rf {} +
  find /usr/lib/onionfruitux -type f -name '*.pyc' -delete
  cat > /usr/bin/onionfruitux <<'EOF'
#!/bin/sh
export PYTHONPATH="/usr/lib/onionfruitux${PYTHONPATH:+:$PYTHONPATH}"
exec python3 -m onionfruitux "$@"
EOF
  chmod 755 /usr/bin/onionfruitux
}

install_man() {
  install -d /usr/share/man/man1
  install -m 644 "$ROOT/share/man/onionfruitux.1" /usr/share/man/man1/onionfruitux.1
  if command -v gzip >/dev/null 2>&1; then
    gzip -f -n /usr/share/man/man1/onionfruitux.1
  fi
  if command -v mandb >/dev/null 2>&1; then
    mandb -q >/dev/null 2>&1 || true
  fi
}

install_desktop() {
  install -d /usr/share/applications /usr/share/icons/hicolor/scalable/apps
  install -m 644 "$ROOT/share/onionfruitux.desktop" /usr/share/applications/onionfruitux.desktop
  install -m 644 "$ROOT/share/onionfruitux.svg" /usr/share/icons/hicolor/scalable/apps/onionfruitux.svg
  if [ -d /usr/share/polkit-1/actions ]; then
    install -m 644 "$ROOT/share/com.nitrofire.onionfruitux.policy" \
      /usr/share/polkit-1/actions/com.nitrofire.onionfruitux.policy
  else
    echo "onionfruitux: warning: polkit actions directory is missing" >&2
  fi
  if [ -d /usr/lib/systemd/system ]; then
    unit_dir=/usr/lib/systemd/system
  elif [ -d /lib/systemd/system ]; then
    unit_dir=/lib/systemd/system
  else
    unit_dir=/etc/systemd/system
  fi
  install -m 644 "$ROOT/share/onionfruitux.service" "$unit_dir/onionfruitux.service"
  if command -v systemctl >/dev/null 2>&1; then
    systemctl daemon-reload >/dev/null 2>&1 || true
  fi
  if command -v update-desktop-database >/dev/null 2>&1; then
    update-desktop-database /usr/share/applications >/dev/null 2>&1 || true
  fi
  if command -v gtk-update-icon-cache >/dev/null 2>&1; then
    gtk-update-icon-cache -f /usr/share/icons/hicolor >/dev/null 2>&1 || true
  fi
}

PM=$(detect_pm)
if [ "$PM" = "unknown" ]; then
  die "could not find apt, dnf, pacman, or zypper"
fi

install_packages
install_user
install_program
install_man
install_desktop

echo "Installed OnionFruitux."
echo "Manual: man onionfruitux"
echo "Window: onionfruitux"
echo "Tray:   onionfruitux tray"
