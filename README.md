
# OnionFruitux

OnionFruitux is a Linux switch that sends apps which follow the system proxy through the [Tor](https://www.torproject.org/) network. Open the window, turn the switch on, and those apps leave through Tor. Turn it off and they use the normal network again.

It is the Linux counterpart to the kind of one-click Tor gateway people use on Windows: a dock or panel app, a command line, optional bridges, and named routes with an entry country and an exit country.

OnionFruitux is an independent project by Nitrofire Computing. It is not affiliated with or endorsed by The Tor Project or DragonFruit Network. Tor is a trademark of The Tor Project. OnionFruit is a trademark of DragonFruit Network.

## What the switch does

While the switch is on, OnionFruitux runs its own Tor and sets the **system SOCKS proxy** to that Tor (port 9155).

- GNOME: `org.gnome.system.proxy` is set to manual, with the SOCKS host and port.
- KDE: `~/.config/kioslaverc` `[Proxy Settings]` is set the same way, when the session is KDE.
- New sessions can also pick up `ALL_PROXY` and `SOCKS_PROXY` (`socks5h://127.0.0.1:9155`). `http_proxy` is not set, because this port speaks SOCKS.
- Before anything is changed, the current proxy settings are saved to `~/.local/state/onionfruitux/saved-state.json`. Turning the switch off puts that file back exactly, checks that the computer can reach the internet directly, and then deletes the file.
- If the app closes, crashes, or is stopped while the switch is on, the next start offers to restore that snapshot. Quit and SIGTERM restore it as well.
- OnionFruitux does **not** install iptables or nftables rules, does **not** rewrite `/etc/resolv.conf`, and does **not** change NetworkManager, ProtonVPN, or Tailscale.

### Which apps follow it

Apps that use the system proxy send their traffic through Tor. That includes Firefox (when it is set to use the system proxy), Chrome and Chromium on GNOME, and GNOME and KDE apps that use the desktop proxy settings.

Apps that ignore the system proxy stay on the normal connection. That includes Transmission, qBittorrent, and uTorrent. OnionFruitux leaves those alone.

Hosts in **Settings → Network → Ignore hosts** also stay direct. The default list is `localhost`, `127.0.0.0/8`, and `::1`. Add a name or a network, separated by commas, when it should skip Tor.

The window also has **New circuit**, which asks Tor for a fresh path. Routes remember an entry country, an exit country, and a bridge choice.

When the switch turns on, OnionFruitux checks that Tor has finished connecting and that a request through the SOCKS proxy reaches [the Tor check API](https://check.torproject.org/api/ip). It then opens [https://check.torproject.org/](https://check.torproject.org/) in the default browser. If that check fails, the saved proxy settings are put back and Tor is stopped. When the switch turns off, it checks that `1.1.1.1` and `8.8.8.8` answer and that a name resolves. If that fails, it restores again. If it still fails, the window explains what is still set and offers **Reset network settings to defaults**, which sets the proxy mode to none. Turn the browser page off under Settings, Network, with "Open the Tor check page when the switch changes".

## Install

Download one file, or run one command. The package manager then downloads Tor, nftables, Qt, polkit, and the bridge transports that distro ships.

```bash
curl -fsSL https://raw.githubusercontent.com/nitrofireinc-pixel/OnionFruitux/main/get.sh | sudo bash
```

That detects Debian, Ubuntu, Linux Mint, Pop!_OS, Fedora, openSUSE, or Arch, downloads the package from the [latest GitHub release](https://github.com/nitrofireinc-pixel/OnionFruitux/releases/latest), and installs it with apt, dnf, zypper, or pacman.

### Debian, Ubuntu, Linux Mint, Pop!_OS

Download `onionfruitux_*_all.deb` from the latest release, then:

```bash
sudo apt install ./onionfruitux_*_all.deb
```

The package is `Architecture: all`. apt installs `tor`, `nftables`, `python3-pyqt6`, `pkexec`, `polkitd` (or `policykit-1`), `xdg-utils`, `obfs4proxy`, and `snowflake-client`.

Upgrading replaces the installed package. You do not need to remove it first:

```bash
sudo apt install ./onionfruitux_1.0.6-1_all.deb
```

If the switch is on, turn it off and on once after upgrading.

Ubuntu's `tor` package starts its own Tor on port 9050 (`tor.service` and `tor@default.service`). OnionFruitux does not use that port, or 9051, or mDNS port 5353. It listens on 9155 (SOCKS) and 9156 (control), and it does not stop the system Tor. `onionfruitux doctor` warns when that service is running. Stop it yourself only when you do not want it:

```bash
sudo systemctl disable --now tor.service tor@default.service
```

### Fedora

Download `onionfruitux-*.noarch.rpm` from the latest release, then:

```bash
sudo dnf install ./onionfruitux-*.noarch.rpm
```

dnf installs `tor`, `nftables`, `python3`, `python3-pyqt6`, `polkit` (which provides `pkexec`), `xdg-utils`, and `obfs4` (which provides `obfs4proxy`). Fedora's repositories do not ship `snowflake-client`.

### openSUSE

The same RPM:

```bash
sudo zypper install ./onionfruitux-*.noarch.rpm
```

zypper installs `tor`, `nftables`, Python 3, PyQt6, `polkit`, `pkexec`, `xdg-utils`, and `obfs4`. openSUSE's `snowflake` package is a proxy, not `snowflake-client`.

### Arch and Manjaro

Download `onionfruitux-*-any.pkg.tar.zst`, then install the dependencies and the package. `pacman -U` does not download dependencies on its own. The one-line installer above does both.

```bash
sudo pacman -Sy --needed python tor nftables python-pyqt6 polkit xdg-utils
sudo pacman -U ./onionfruitux-*-any.pkg.tar.zst
```

From a git clone you can build it instead:

```bash
cd packaging
makepkg -si
```

The package is `arch=('any')`. The official Arch repositories do not currently ship `obfs4proxy` or `snowflake-client`. Plain Tor routes still work; install those transports yourself when you need those bridges.

The packages install the command, the manual page, the desktop file, the icon, the polkit policy, and a systemd unit. The unit is not enabled. `onionfruitux boot enable` turns routing on at the next boot and does not connect now.

Then pin **OnionFruitux** to the dock or panel from the app menu, or run:

```bash
onionfruitux          # window
onionfruitux tray     # panel or dock icon, with the same switch
```

The first time the switch is turned on, the desktop asks for an admin password. That password lets OnionFruitux start its own Tor. It is remembered for a little while. The window stays usable while that dialog is open and while Tor is connecting. The proxy itself is changed as you, not as root.

The switch slides on and turns **orange** while Tor is starting. A status line shows the step, including the bootstrap percent. It turns **green** only after Tor is fully connected, the system proxy is set, and a request through that proxy is using Tor. The tray icon uses the same colors. If connecting fails or times out, the switch slides back to off, the saved proxy settings are put back, and Tor is stopped. Once the switch is green, OnionFruitux opens https://check.torproject.org in your normal browser, as you, without waiting on the browser. Turn that off under Settings, Network.

## Routes and bridges

Open **Settings**.

- **Routes** — **New route**, then pick the entry country and the exit country. Sites see the exit country. Choose **Only use the countries I picked** when a circuit that ignores those countries is not acceptable.
- **Bridges** — optional. Plain, obfs4, Snowflake, meek, WebTunnel, and Conjure. Snowflake, meek, and obfs4 can use the published default bridges shipped in `onionfruitux/data/pt_config.json` (from the Tor Browser bundle). Paste lines from [bridges.torproject.org](https://bridges.torproject.org) when a network blocks the defaults, or for plain, WebTunnel, and Conjure bridges. A bridge replaces the entry country.
- **Network** — list hosts that should ignore the proxy, list relay ports (for example `80, 443`) when a firewall only allows those outbound ports, and reset the system proxy mode to none.

From the command line:

```bash
onionfruitux route add "US exit" --entry de --exit us
onionfruitux route add "Snowflake" --bridge snowflake
onionfruitux route list
onionfruitux route use "US exit"
onionfruitux connect
onionfruitux status
onionfruitux new-circuit
onionfruitux disconnect
onionfruitux panic-off     # stop Tor and delete a leftover firewall table from an older version
onionfruitux reset-network # set the system proxy mode to none
onionfruitux doctor
onionfruitux plan          # print the torrc and proxy settings, change nothing
```

`onionfruitux boot enable` saves the current settings and turns routing on at startup. `onionfruitux boot disable` turns that off.

## If the app froze or the network stopped working

Current OnionFruitux only changes the system proxy, and it writes the previous values to `~/.local/state/onionfruitux/saved-state.json` first. Closing the app, or opening it again and choosing **Restore**, puts those values back. `onionfruitux reset-network` sets the proxy mode to none when the saved values are not enough. That command does not change DNS or VPN connections.

Older versions also installed one nftables table, `inet onionfruitux`, redirected DNS, and on disconnect asked NetworkManager to reapply every connection. That could leave the computer with no internet, including while a ProtonVPN kill switch was up. Those versions did not change ProtonVPN on purpose, but reapplying the network and blocking UDP could drop it. This version does not install that table, does not rewrite `resolv.conf`, and does not touch ProtonVPN or Tailscale.

If an older table is still installed, delete only that table:

```bash
sudo nft list table inet onionfruitux
sudo nft delete table inet onionfruitux
onionfruitux panic-off
onionfruitux reset-network
```

`panic-off` stops the Tor process this app started, deletes the `onionfruitux` table if it is still there, and restores a DNS snapshot if an older version saved one. It does not reconfigure NetworkManager or any VPN. It is safe to run twice. If Tor is still running and that command cannot stop it, stop only a Tor that is using OnionFruitux's torrc (leave any other Tor alone):

```bash
ps -eo pid,args | grep /var/lib/onionfruitux/torrc
sudo kill PID
```

## Check the machine

```bash
onionfruitux doctor
```

Country selection uses Tor's GeoIP database (`/usr/share/tor/geoip` on most distros), which the `tor` package installs.

## Uninstall

Debian, Ubuntu, Linux Mint, and Pop!_OS:

```bash
sudo apt remove onionfruitux
sudo apt purge onionfruitux
```

`apt purge` also removes the `onionfruitux` user and `/var/lib/onionfruitux`.

Fedora:

```bash
sudo dnf remove onionfruitux
```

openSUSE:

```bash
sudo zypper remove onionfruitux
```

Arch and Manjaro:

```bash
sudo pacman -R onionfruitux
```

On Fedora, openSUSE, and Arch, remove the system user and data yourself when you want a full purge:

```bash
sudo userdel onionfruitux
sudo rm -rf /var/lib/onionfruitux /etc/onionfruitux
```

The `tor` and `nftables` packages stay installed. From a git checkout, `sudo ./uninstall.sh` still removes a copy installed from source. `sudo ./uninstall.sh --purge` also removes the user and `/var/lib/onionfruitux`.

## Development

Install from a git clone with the distro package manager:

```bash
git clone https://github.com/nitrofireinc-pixel/OnionFruitux.git
cd OnionFruitux
sudo ./install.sh
```

`install.sh` uses apt, dnf, pacman, or zypper. It installs Tor, nftables, a bridge transport when the distro has one, and the Qt libraries used by the window.

```bash
python3 -m unittest discover -s tests -v
```

The tests build the torrc and exercise the proxy snapshot and restore. They do not change the computer's network.

Published bridge lines are refreshed from the Tor Project file recorded in `onionfruitux/data/PT_CONFIG_SOURCE.txt`.

## License

GNU General Public License v3 or later. See [LICENSE](LICENSE).
```
