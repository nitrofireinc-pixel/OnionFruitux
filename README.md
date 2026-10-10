
# OnionFruitux

OnionFruitux is a Linux switch that sends the computer's internet traffic through the [Tor](https://www.torproject.org/) network. Open the window, turn the switch on, and new connections leave through Tor. Turn it off and the computer uses its normal network again.

It is the Linux counterpart to the kind of one-click Tor gateway people use on Windows: a dock or panel app, a command line, optional bridges, and named routes with an entry country and an exit country.

OnionFruitux is an independent project by Nitrofire Computing. It is not affiliated with or endorsed by The Tor Project or DragonFruit Network. Tor is a trademark of The Tor Project. OnionFruit is a trademark of DragonFruit Network.

## What the switch does

While the switch is on, OnionFruitux runs Tor as its own system user and adds one nftables table named `onionfruitux`.

- TCP connections are redirected into Tor.
- DNS is redirected into Tor, including lookups that would have gone to a home router.
- `.onion` names can resolve through Tor's DNS port.
- DHCP keeps working, so the computer does not lose its address.
- Other UDP is blocked, so a program cannot bypass Tor with a UDP stream.
- The local network stays direct (printers, file shares, other machines on the LAN). There is a setting to send that through Tor as well.
- Traffic that cannot go through Tor is rejected. That check is on by default.
- Turning the switch off deletes only the `onionfruitux` table, restores the previous DNS settings, and stops the Tor process OnionFruitux started.

Redirected connections are accepted in the firewall. The filter runs before Linux moves a redirected packet onto the loopback interface, so a loopback allow rule does not see them. Accepting the DNAT mark is what lets them reach Tor. Without that, Tor can be fully connected while Firefox and curl still have no working web, and `panic-off` is what brings the network back.

On Ubuntu, apps ask systemd-resolved at `127.0.0.53`. While the switch is on, OnionFruitux points resolved at `127.0.0.1:9153` and leaves the `stub-resolv.conf` symlink alone. Disconnect and `panic-off` restore the previous resolver. Port 53 is redirected as well, including lookups that would have gone to the home router.

Tailscale's WireGuard UDP is blocked while the switch is on, so the tunnel pauses and MagicDNS is not used. It comes back when the switch is turned off. TCP that would have left through Tailscale goes through Tor instead, which keeps that path from being a way around Tor. Local printers and file shares stay reachable. DNS does not.

The window also has **New circuit**, which asks Tor for a fresh path. Routes remember an entry country, an exit country, and a bridge choice.

When the switch turns on, OnionFruitux opens [https://check.torproject.org/](https://check.torproject.org/) in the default browser. That page says the browser is using Tor. When the switch turns off, it opens the same page again, and the page says the browser is not using Tor. The check runs after the firewall change, so the page matches the switch. Turn this off under Settings, Network, with "Open the Tor check page when the switch changes".

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
sudo apt install ./onionfruitux_1.0.5-1_all.deb
```

If the switch is on, turn it off and on once after upgrading.

Ubuntu's `tor` package starts its own Tor on port 9050 (`tor.service` and `tor@default.service`). OnionFruitux does not use that port, or 9051, or mDNS port 5353. It listens on 9140 (transparent proxy), 9153 (DNS), 9155 (SOCKS), and 9156 (control), and it does not stop the system Tor. `onionfruitux doctor` warns when that service is running. Stop it yourself only when you do not want it:

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

The first time the switch is turned on, the desktop asks for an admin password. That password lets OnionFruitux change the firewall. It is remembered for a little while. The window stays usable while that dialog is open and while Tor is connecting.

The switch slides on and turns **orange** while Tor is starting. A status line shows the step, including the bootstrap percent. It turns **green** only after Tor is fully connected and the firewall is in place. The tray icon uses the same colors. If connecting fails or times out, the switch slides back to off and the firewall is removed. Once the switch is green, OnionFruitux opens https://check.torproject.org in your normal browser, as you, without waiting on the browser. Turn that off under Settings, Network.

## Routes and bridges

Open **Settings**.

- **Routes** — **New route**, then pick the entry country and the exit country. Sites see the exit country. Choose **Only use the countries I picked** when a circuit that ignores those countries is not acceptable.
- **Bridges** — optional. Plain, obfs4, Snowflake, meek, WebTunnel, and Conjure. Snowflake, meek, and obfs4 can use the published default bridges shipped in `onionfruitux/data/pt_config.json` (from the Tor Browser bundle). Paste lines from [bridges.torproject.org](https://bridges.torproject.org) when a network blocks the defaults, or for plain, WebTunnel, and Conjure bridges. A bridge replaces the entry country.
- **Network** — keep the local network off Tor, block traffic that cannot use Tor, and list relay ports (for example `80, 443`) when a firewall only allows those outbound ports.

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
onionfruitux panic-off     # remove the firewall table even if the app froze
onionfruitux doctor
onionfruitux plan          # print the torrc and firewall rules, change nothing
```

`onionfruitux boot enable` saves the current settings and turns routing on at startup. `onionfruitux boot disable` turns that off.

## If the app froze or the network stopped working

The firewall changes live only in one nftables table, `inet onionfruitux`. Deleting that table restores DNS and normal routing. These commands work on the 1.0.0 package already installed, before you upgrade:

```bash
sudo nft list table inet onionfruitux
sudo nft list tables
sudo nft delete table inet onionfruitux
sudo /usr/bin/onionfruitux --system disconnect
```

`sudo nft delete table inet onionfruitux` is the command that brings the network back. If it says the table does not exist, the firewall is already gone. `--system disconnect` stops the Tor process this app started and does not open a browser. If that command cannot run, stop only a Tor that is using OnionFruitux's torrc (leave any other Tor alone):

```bash
ps -eo pid,args | grep /var/lib/onionfruitux/torrc
sudo kill PID
```

After upgrading to 1.0.1, one command does both and does not open a browser:

```bash
onionfruitux panic-off
```

It is safe to run twice. Closing the app while the switch is still orange also removes the table.

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

The tests build the torrc and the nftables rules and, when `nft` is installed, check the rules with `nft -c` so nothing is applied. They do not change the computer's network.

Published bridge lines are refreshed from the Tor Project file recorded in `onionfruitux/data/PT_CONFIG_SOURCE.txt`.

## License

GNU General Public License v3 or later. See [LICENSE](LICENSE).
```
