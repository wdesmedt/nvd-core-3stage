#!/usr/bin/env python3
"""Generate the fake k8s worker nodes (FRR) and the leaf ipvrf-k8s config.

Six nodes, two per rack (leaf pair), eth1 to the odd and eth2 to the even
leaf of the rack. The node BGP config holds no leaf address in either design:

* racks 1-2 (leaf1-leaf4, k8s1-k8s4): BGP unnumbered. The node ports are
  routed IPv6 link-local subinterfaces in ipvrf-k8s with interface-based
  dynamic neighbors; the nodes peer with "neighbor ethX interface" (FRR-K8s /
  MetalLB `interface` style). Every node has the same frr.conf.
* rack 3 (leaf5-leaf6, k8s5-k8s6): numbered leaf-local subnets with prefix
  based dynamic neighbors; the nodes find their peer like Cilium's
  autoDiscovery mode DefaultGateway: one session per address family, to the
  gateway of the lowest-metric default route that is up (autodiscover.sh).

A node advertises what is on its own dummies (pod CIDR, node IP, anycast VIP)
through redistribute connected, so its frr.conf carries no node data either.

Writes k8s/leaf/<leaf>.cfg and frr/k8s<n>/; the topology nodes and links are
kept by hand in the clab file.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CLUSTER_AS = 65100
FABRIC_AS = 64512
NODES = range(1, 7)
LEAFS = range(1, 7)
UNNUMBERED_LEAFS = (1, 2, 3, 4)
UNNUMBERED_NODES = (1, 2, 3, 4)
PORTS = ("ethernet-1/10", "ethernet-1/11")  # odd node, even node


def bgp_group(g, fam_lines):
    """Settings shared by every node-facing group."""
    return [
        f"set {g} import-policy [ k8s-import ]",
        f"set {g} export-policy [ k8s-export ]",
        f"set {g} failure-detection enable-bfd true",
        f"set {g} failure-detection fast-failover true",
        # without its own policy no default is sent (25.10.5), even when
        # k8s-export accepts 0/0
        f"set {g} send-default-route export-policy k8s-default",
        *fam_lines,
    ]


def unnumbered_leaf(bgp):
    lines = []
    for p in PORTS:
        s = f"/ interface {p} subinterface 0"
        lines += [
            f"set / interface {p} admin-state enable",
            f"set {s} admin-state enable",
            f"set {s} ipv6 admin-state enable",
            f"set {s} ipv6 router-advertisement router-role admin-state enable",
            f"set {s} ipv6 router-advertisement router-role max-advertisement-interval 10",
            f"set {s} ipv6 router-advertisement router-role min-advertisement-interval 4",
            f"set / bfd subinterface {p}.0 admin-state enable",
            f"set / bfd subinterface {p}.0 desired-minimum-transmit-interval 300000",
            f"set / bfd subinterface {p}.0 required-minimum-receive 300000",
            f"set / bfd subinterface {p}.0 detection-multiplier 3",
            f"set / network-instance ipvrf-k8s interface {p}.0",
            f"set {bgp} dynamic-neighbors interface {p}.0 peer-group k8s",
            f"set {bgp} dynamic-neighbors interface {p}.0 allowed-peer-as [ {CLUSTER_AS} ]",
        ]
    g = f"{bgp} group k8s"
    lines += [
        # IPv4 routes with IPv6 link-local next hops (RFC 8950)
        "set / network-instance ipvrf-k8s ip-forwarding receive-ipv4-check false",
        *bgp_group(g, [
            f"set {g} send-default-route ipv4-unicast true",
            f"set {g} send-default-route ipv6-unicast true",
            f"set {g} afi-safi ipv4-unicast admin-state enable",
            f"set {g} afi-safi ipv4-unicast ipv4-unicast advertise-ipv6-next-hops true",
            f"set {g} afi-safi ipv4-unicast ipv4-unicast receive-ipv6-next-hops true",
            f"set {g} afi-safi ipv4-unicast ipv4-unicast prefix-limit-received max-received-routes 64",
            f"set {g} afi-safi ipv6-unicast admin-state enable",
            f"set {g} afi-safi ipv6-unicast ipv6-unicast prefix-limit-received max-received-routes 64",
        ]),
    ]
    return lines


def numbered_leaf(leaf, bgp):
    lines = []
    for p in PORTS:
        lines += [
            f"set / interface {p} admin-state enable",
            f"set / interface {p} subinterface 0 type bridged",
            f"set / interface {p} subinterface 0 admin-state enable",
        ]
    lines += [
        "set / interface irb0 subinterface 300 admin-state enable",
        "set / interface irb0 subinterface 300 ipv4 admin-state enable",
        f"set / interface irb0 subinterface 300 ipv4 address 10.30.{leaf}.1/24",
        "set / interface irb0 subinterface 300 ipv4 arp timeout 300",
        "set / interface irb0 subinterface 300 ipv6 admin-state enable",
        f"set / interface irb0 subinterface 300 ipv6 address 3fff:10:30:{leaf}::1/64",
        "set / interface irb0 subinterface 300 ipv6 neighbor-discovery stale-time 300",
        "set / bfd subinterface irb0.300 admin-state enable",
        "set / bfd subinterface irb0.300 desired-minimum-transmit-interval 300000",
        "set / bfd subinterface irb0.300 required-minimum-receive 300000",
        "set / bfd subinterface irb0.300 detection-multiplier 3",
        # leaf-local node subnet: a MAC-VRF without EVPN, only the IRB is routed
        "set / network-instance macvrf-k8s type mac-vrf",
        *[f"set / network-instance macvrf-k8s interface {p}.0" for p in PORTS],
        "set / network-instance macvrf-k8s interface irb0.300",
        "set / network-instance ipvrf-k8s interface irb0.300",
    ]
    for af, other, prefix in (("v4", "ipv6", f"10.30.{leaf}.0/24"),
                              ("v6", "ipv4", f"3fff:10:30:{leaf}::/64")):
        fam = "ipv4" if af == "v4" else "ipv6"
        g = f"{bgp} group k8s-{af}"
        lines += bgp_group(g, [
            f"set {g} send-default-route {fam}-unicast true",
            f"set {g} afi-safi {fam}-unicast admin-state enable",
            f"set {g} afi-safi {fam}-unicast {fam}-unicast prefix-limit-received max-received-routes 64",
            f"set {g} afi-safi {other}-unicast admin-state disable",
        ])
        lines += [
            f"set {bgp} dynamic-neighbors accept match {prefix} peer-group k8s-{af}",
            f"set {bgp} dynamic-neighbors accept match {prefix} allowed-peer-as [ {CLUSTER_AS} ]",
        ]
    return lines


def leaf_cfg(leaf):
    rid = f"192.0.2.{10 + leaf}"
    ni = "/ network-instance ipvrf-k8s"
    bgp = f"{ni} protocols bgp"
    lines = [
        "set / tunnel-interface vxlan0 vxlan-interface 3 type routed",
        "set / tunnel-interface vxlan0 vxlan-interface 3 ingress vni 3",
        f"set {ni} type ip-vrf",
        f"set {ni} vxlan-interface vxlan0.3",
        f"set {ni} protocols bgp-evpn bgp-instance 1 vxlan-interface vxlan0.3",
        f"set {ni} protocols bgp-evpn bgp-instance 1 evi 3",
        f"set {ni} protocols bgp-evpn bgp-instance 1 ecmp 128",
        f"set {ni} protocols bgp-vpn bgp-instance 1 route-target export-rt target:1:3",
        f"set {ni} protocols bgp-vpn bgp-instance 1 route-target import-rt target:1:3",
        f"set {bgp} autonomous-system {FABRIC_AS}",
        f"set {bgp} router-id {rid}",
        f"set {bgp} afi-safi ipv4-unicast admin-state enable",
        f"set {bgp} afi-safi ipv4-unicast multipath ebgp maximum-paths 16",
        f"set {bgp} afi-safi ipv6-unicast admin-state enable",
        f"set {bgp} afi-safi ipv6-unicast multipath ebgp maximum-paths 16",
    ]
    lines += unnumbered_leaf(bgp) if leaf in UNNUMBERED_LEAFS else numbered_leaf(leaf, bgp)
    rp = "/ routing-policy"
    sets = {
        "k8s-pods-v4": ("10.244.0.0/16", "24..26"),
        "k8s-nodes-v4": ("10.31.0.0/24", "32..32"),
        "k8s-vips-v4": ("10.200.0.0/16", "32..32"),
        "k8s-pods-v6": ("3fff:244::/32", "64..64"),
        "k8s-nodes-v6": ("3fff:10:31::/64", "128..128"),
        "k8s-vips-v6": ("3fff:200::/32", "128..128"),
    }
    for name, (pfx, rng) in sets.items():
        lines.append(f"set {rp} prefix-set {name} prefix {pfx} mask-length-range {rng}")
    lines.append(f"set {rp} policy k8s-import default-action policy-result reject")
    for i, name in enumerate(sets, 1):
        lines += [
            f"set {rp} policy k8s-import statement {i}0 match prefix prefix-set {name}",
            f"set {rp} policy k8s-import statement {i}0 action policy-result accept",
        ]
    # nodes only get the default route (send-default-route)
    lines.append(f"set {rp} policy k8s-export default-action policy-result reject")
    lines.append(f"set {rp} policy k8s-default default-action policy-result accept")
    return "\n".join(lines) + "\n"


FRR_COMMON = """frr defaults traditional
!
vrf k8s
exit-vrf
!
route-map OWN_ROUTES_ONLY permit 10
 match as-path EMPTY
exit
!
route-map NODE_PREFIXES permit 10
 match interface pods
exit
route-map NODE_PREFIXES permit 20
 match interface node
exit
route-map NODE_PREFIXES permit 30
 match interface vip
exit
!
bgp as-path access-list EMPTY seq 5 permit ^$
!
"""

# racks 1-2: identical on every node, peers are the interfaces
FRR_UNNUMBERED = FRR_COMMON + f"""interface eth1
 ipv6 nd ra-interval 5
 no ipv6 nd suppress-ra
exit
!
interface eth2
 ipv6 nd ra-interval 5
 no ipv6 nd suppress-ra
exit
!
router bgp {CLUSTER_AS} vrf k8s
 no bgp ebgp-requires-policy
 no bgp default ipv4-unicast
 neighbor fabric peer-group
 neighbor fabric remote-as external
 neighbor fabric bfd
 neighbor fabric timers 3 9
 neighbor fabric timers connect 5
 neighbor eth1 interface peer-group fabric
 neighbor eth2 interface peer-group fabric
 !
 address-family ipv4 unicast
  redistribute connected route-map NODE_PREFIXES
  neighbor fabric activate
  neighbor fabric route-map OWN_ROUTES_ONLY out
  maximum-paths 8
 exit-address-family
 !
 address-family ipv6 unicast
  redistribute connected route-map NODE_PREFIXES
  neighbor fabric activate
  neighbor fabric route-map OWN_ROUTES_ONLY out
  maximum-paths 8
 exit-address-family
exit
!
"""

# rack 3: peer groups only, autodiscover.sh adds the neighbor. Received
# routes are not installed, as with Cilium; the node forwards on its kernel
# default routes.
FRR_DEFAULT_GW = FRR_COMMON + f"""route-map DENY_ALL deny 10
exit
!
router bgp {CLUSTER_AS} vrf k8s
 no bgp ebgp-requires-policy
 no bgp default ipv4-unicast
 neighbor fabric-v4 peer-group
 neighbor fabric-v4 remote-as external
 neighbor fabric-v4 bfd
 neighbor fabric-v4 timers 3 9
 neighbor fabric-v4 timers connect 5
 neighbor fabric-v6 peer-group
 neighbor fabric-v6 remote-as external
 neighbor fabric-v6 bfd
 neighbor fabric-v6 timers 3 9
 neighbor fabric-v6 timers connect 5
 !
 address-family ipv4 unicast
  redistribute connected route-map NODE_PREFIXES
  neighbor fabric-v4 activate
  neighbor fabric-v4 route-map DENY_ALL in
  neighbor fabric-v4 route-map OWN_ROUTES_ONLY out
 exit-address-family
 !
 address-family ipv6 unicast
  redistribute connected route-map NODE_PREFIXES
  neighbor fabric-v6 activate
  neighbor fabric-v6 route-map DENY_ALL in
  neighbor fabric-v6 route-map OWN_ROUTES_ONLY out
 exit-address-family
exit
!
"""

AUTODISCOVER = f"""#!/bin/sh
# Mimics Cilium BGP autoDiscovery mode DefaultGateway: one session per address
# family, to the gateway of the lowest-metric kernel default route in VRF k8s
# that is not linkdown. Re-checked every 2 s; the peer moves when that route
# changes (e.g. the link to the primary leaf goes down).
VRF=k8s
AS={CLUSTER_AS}

gateway() {{
    ip "$1" route show vrf "$VRF" default | grep -v -e linkdown -e 'proto bgp' |
        awk '{{m = 0; for (i = 1; i < NF; i++) {{ if ($i == "via") g = $(i + 1); if ($i == "metric") m = $(i + 1) }}
              print m, g}}' | sort -n | awk 'NR == 1 {{ print $2 }}'
}}

move_peer() {{  # family old new
    fam=$1 old=$2 new=$3
    set -- -c "configure terminal" -c "router bgp $AS vrf $VRF"
    [ -n "$old" ] && set -- "$@" -c "no neighbor $old"
    [ -n "$new" ] && set -- "$@" -c "neighbor $new peer-group fabric-v$fam"
    vtysh "$@" >/dev/null 2>&1 || return 1
    echo "$(date +%T) v$fam peer ${{old:-none}} -> ${{new:-none}}"
}}

cur4= cur6=
while :; do
    new4=$(gateway -4)
    if [ "$new4" != "$cur4" ] && move_peer 4 "$cur4" "$new4"; then cur4=$new4; fi
    new6=$(gateway -6)
    if [ "$new6" != "$cur6" ] && move_peer 6 "$cur6" "$new6"; then cur6=$new6; fi
    sleep 2
done
"""


def main():
    (ROOT / "k8s/leaf").mkdir(parents=True, exist_ok=True)
    for leaf in LEAFS:
        (ROOT / f"k8s/leaf/leaf{leaf}.cfg").write_text(leaf_cfg(leaf))
    daemons = (ROOT / "frr/vm1/daemons").read_text()
    for n in NODES:
        d = ROOT / f"frr/k8s{n}"
        d.mkdir(parents=True, exist_ok=True)
        (d / "daemons").write_text(daemons)
        if n in UNNUMBERED_NODES:
            (d / "frr.conf").write_text(FRR_UNNUMBERED)
            (d / "autodiscover.sh").unlink(missing_ok=True)
        else:
            (d / "frr.conf").write_text(FRR_DEFAULT_GW)
            (d / "autodiscover.sh").write_text(AUTODISCOVER)
            (d / "autodiscover.sh").chmod(0o755)


if __name__ == "__main__":
    main()
