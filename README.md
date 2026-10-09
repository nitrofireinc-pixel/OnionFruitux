
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
- Turning the switch off deletes only the `onionfruitux` table and stops the Tor process OnionFruitux started.

The window also has **New circuit**, which asks Tor for a fresh path. Routes remember an entry country, an exit country, and a bridge choice.

When the switch turns on, OnionFruitux opens [https://check.torproject.org/](https://check.torproject.org/) in the default browser. That page says the browser is using Tor. When the switch turns off, it opens the same page again, and the page says the browser is not using Tor. The check runs after the firewall change, so the page matches the switch. Turn this off under Settings, Network, with "Open the Tor check page when the switch changes".

## Install

Debian, Ubuntu, Linux Mint, Pop!_OS, Fedora, Arch, Manjaro, and openSUSE:

```bash
git clone https://github.com/nitrofireinc-pixel/OnionFruitux.git
cd OnionFruitux
sudo ./install.sh
```

The installer uses apt, dnf, pacman, or zypper. It installs Tor, nftables, a bridge transport when the distro has one (lyrebird, obfs4proxy, or snowflake-client), and the Qt libraries used by the window. It also creates a system user named `onionfruitux` that Tor runs as.

Then pin **OnionFruitux** to the dock or panel from the app menu, or run:

```bash
onionfruitux          # window
onionfruitux tray     # panel or dock icon, with the same switch
```

The first time the switch is turned on, the desktop asks for an admin password. That password lets OnionFruitux change the firewall. It is remembered for a little while.

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
onionfruitux doctor
onionfruitux plan          # print the torrc and firewall rules, change nothing
```

`onionfruitux boot enable` saves the current settings and turns routing on at startup. `onionfruitux boot disable` turns that off.

## Check the machine

```bash
onionfruitux doctor
```

Country selection uses Tor's GeoIP database (`/usr/share/tor/geoip` on most distros), which the `tor` package installs.

## Uninstall

```bash
sudo ./uninstall.sh
```

That removes the app, the firewall table, and OnionFruitux's Tor process. The `tor` and `nftables` packages stay installed. `sudo ./uninstall.sh --purge` also removes the `onionfruitux` user and `/var/lib/onionfruitux`.

## Development

```bash
python3 -m unittest discover -s tests -v
```

The tests build the torrc and the nftables rules and, when `nft` is installed, check the rules with `nft -c` so nothing is applied. They do not change the computer's network.

Published bridge lines are refreshed from the Tor Project file recorded in `onionfruitux/data/PT_CONFIG_SOURCE.txt`.

## License

GNU General Public License v3 or later. See [LICENSE](LICENSE).
```
