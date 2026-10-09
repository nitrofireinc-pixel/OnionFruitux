"""nftables rules for the single onionfruitux table.

DNS is redirected before the local-network exceptions. A lookup that would
have gone to a home router, or to a resolver on localhost, still enters Tor.
The rules are text only until a privileged connect applies them.

Redirected packets are accepted with `ct status dnat`. The filter hook runs
in the same output pass, before the kernel moves the packet onto loopback, so
`oifname "lo"` does not match them yet. Without the dnat accept, the final
reject drops the connection and the computer has no web even though Tor is up.
"""

from __future__ import annotations

from onionfruitux.config import NetworkSettings
from onionfruitux.paths import DNS_PORT, TABLE_NAME, TOR_USER, TRANS_PORT, VIRTUAL_IPV4, VIRTUAL_IPV6

_DHCP = "udp dport { 67, 68, 546, 547 }"
# 10.192.0.0/10 is Tor's virtual range, so it is not part of this set.
_LAN_V4 = (
    "ip daddr { 10.0.0.0/9, 10.128.0.0/10, 172.16.0.0/12, "
    "192.168.0.0/16, 169.254.0.0/16 }"
)
_LAN_V6_LINK = "ip6 daddr fe80::/10"
_LAN_V6_ULA = f"ip6 daddr fc00::/7 ip6 daddr != {VIRTUAL_IPV6}"


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
    ]
    if settings.lan_direct:
        nat.append(f"        ip daddr {VIRTUAL_IPV4} meta l4proto tcp redirect to :{TRANS_PORT}")
        nat.append(f"        ip6 daddr {VIRTUAL_IPV6} meta l4proto tcp redirect to :{TRANS_PORT}")
    nat.append('        oifname "lo" return')
    if settings.lan_direct:
        nat.append(f"        {_LAN_V4} return")
        nat.append(f"        {_LAN_V6_LINK} return")
        nat.append(f"        {_LAN_V6_ULA} return")
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
            "        ct status dnat accept",
            f"        {_DHCP} accept",
            "        udp sport 68 accept",
            "        icmpv6 type { nd-router-solicit, nd-router-advert, nd-neighbor-solicit, nd-neighbor-advert, nd-redirect } accept",
            '        oifname "lo" accept',
        ]
    )
    if settings.lan_direct:
        nat.append(f"        {_LAN_V4} accept")
        nat.append(f"        {_LAN_V6_LINK} accept")
        nat.append(f"        {_LAN_V6_ULA} accept")
    nat.append("        meta l4proto udp reject")
    if settings.reject_non_tor:
        nat.append("        reject")
    nat.extend(["    }", "}"])
    return "\n".join(nat) + "\n"
