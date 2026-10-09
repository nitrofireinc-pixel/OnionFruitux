"""nftables rules for the single onionfruitux table.

DNS is redirected before the local-network exceptions. A lookup that would
have gone to a home router, or to a resolver on localhost, still enters Tor.
The rules are text only until a privileged connect applies them.
"""

from __future__ import annotations

from onionfruitux.config import NetworkSettings
from onionfruitux.paths import DNS_PORT, TABLE_NAME, TOR_USER, TRANS_PORT

_DHCP = "udp dport { 67, 68, 546, 547 }"
_LAN_V4 = "ip daddr { 10.0.0.0/8, 172.16.0.0/12, 192.168.0.0/16, 169.254.0.0/16 }"
_LAN_V6 = "ip6 daddr { fe80::/10, fc00::/7 }"


def render_firewall(
    network: NetworkSettings | None = None,
    tor_user: str = TOR_USER,
) -> str:
    settings = network if network is not None else NetworkSettings()
    nat = [
        f"table inet {TABLE_NAME} {{",
        "    chain nat_output {",
        "        type nat hook output priority dstnat; policy accept;",
        f"        meta skuid {tor_user} return",
        f"        {_DHCP} return",
        "        udp sport 68 return",
        f"        udp dport 53 redirect to :{DNS_PORT}",
        f"        tcp dport 53 redirect to :{DNS_PORT}",
        "        meta pkttype { broadcast, multicast } return",
        '        oifname "lo" return',
    ]
    if settings.lan_direct:
        nat.append(f"        {_LAN_V4} return")
        nat.append(f"        {_LAN_V6} return")
    nat.extend(
        [
            "        ip daddr 224.0.0.0/4 return",
            "        ip6 daddr ff00::/8 return",
            f"        meta l4proto tcp redirect to :{TRANS_PORT}",
            "    }",
            "",
            "    chain filter_output {",
            "        type filter hook output priority filter; policy accept;",
            f"        meta skuid {tor_user} accept",
            "        ct state established,related accept",
            f"        {_DHCP} accept",
            "        udp sport 68 accept",
            "        icmpv6 type { nd-router-solicit, nd-router-advert, nd-neighbor-solicit, nd-neighbor-advert, nd-redirect } accept",
            '        oifname "lo" accept',
        ]
    )
    if settings.lan_direct:
        nat.append(f"        {_LAN_V4} accept")
        nat.append(f"        {_LAN_V6} accept")
    nat.append("        meta l4proto udp reject")
    if settings.reject_non_tor:
        nat.append("        reject")
    nat.extend(["    }", "}"])
    return "\n".join(nat) + "\n"
