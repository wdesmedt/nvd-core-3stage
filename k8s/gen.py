#!/usr/bin/env python3
"""Generate the fake k8s worker nodes (FRR) and the leaf ipvrf-k8s config.

Six nodes, two per rack (leaf pair). Each node has eth1 to the odd leaf and
eth2 to the even leaf of its rack, on a leaf-local subnet, and peers with the
leaf IRB address on each link (its default gateways). The leafs accept any
node on that subnet through BGP dynamic neighbors, so adding a node needs no
fabric change.

Writes k8s/leaf/<leaf>.cfg and frr/k8s<n>/frr.conf; the topology nodes and
links are kept by hand in the clab file.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CLUSTER_AS = 65100
FABRIC_AS = 64512
NODES = range(1, 7)
LEAFS = range(1, 7)
PORTS = {1: "ethernet-1/10", 0: "ethernet-1/11"}  # by node number parity


def rack_leafs(n):
    r = (n + 1) // 2
    return 2 * r - 1, 2 * r


def node_ip(leaf, n, v6=False):
    return f"3fff:10:30:{leaf}::{10 + n}" if v6 else f"10.30.{leaf}.{10 + n}"


def leaf_cfg(leaf):
    rid = f"192.0.2.{10 + leaf}"
    ports = list(PORTS.values())
    ni = "/ network-instance ipvrf-k8s"
    bgp = f"{ni} protocols bgp"
    lines = []
    for p in ports:
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
        *[f"set / network-instance macvrf-k8s interface {p}.0" for p in ports],
        "set / network-instance macvrf-k8s interface irb0.300",
        "set / tunnel-interface vxlan0 vxlan-interface 3 type routed",
        "set / tunnel-interface vxlan0 vxlan-interface 3 ingress vni 3",
        f"set {ni} type ip-vrf",
        f"set {ni} interface irb0.300",
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
    for af, other, prefix in (("v4", "ipv6", f"10.30.{leaf}.0/24"),
                              ("v6", "ipv4", f"3fff:10:30:{leaf}::/64")):
        fam = "ipv4" if af == "v4" else "ipv6"
        g = f"{bgp} group k8s-{af}"
        lines += [
            f"set {g} import-policy [ k8s-import ]",
            f"set {g} export-policy [ k8s-export ]",
            f"set {g} failure-detection enable-bfd true",
            f"set {g} failure-detection fast-failover true",
            f"set {g} send-default-route {fam}-unicast true",
            # without its own policy no default is sent (25.10.5), even when
            # k8s-export accepts 0/0
            f"set {g} send-default-route export-policy k8s-default",
            f"set {g} afi-safi {fam}-unicast admin-state enable",
            f"set {g} afi-safi {fam}-unicast {fam}-unicast prefix-limit-received max-received-routes 64",
            f"set {g} afi-safi {other}-unicast admin-state disable",
            f"set {bgp} dynamic-neighbors accept match {prefix} peer-group k8s-{af}",
            f"set {bgp} dynamic-neighbors accept match {prefix} allowed-peer-as [ {CLUSTER_AS} ]",
        ]
    rp = "/ routing-policy"
    sets = {
        "k8s-pods-v4": ("10.244.0.0/16", "24..26"),
        "k8s-vips-v4": ("10.200.0.0/16", "32..32"),
        "k8s-pods-v6": ("3fff:244::/32", "64..64"),
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


def frr_conf(n):
    a, b = rack_leafs(n)
    return f"""frr defaults traditional
hostname k8s{n}
!
vrf k8s
exit-vrf
!
route-map OWN_ROUTES_ONLY permit 10
 match as-path EMPTY
exit
!
router bgp {CLUSTER_AS} vrf k8s
 bgp router-id 10.244.{n}.1
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
 neighbor 10.30.{a}.1 peer-group fabric-v4
 neighbor 10.30.{b}.1 peer-group fabric-v4
 neighbor 3fff:10:30:{a}::1 peer-group fabric-v6
 neighbor 3fff:10:30:{b}::1 peer-group fabric-v6
 !
 address-family ipv4 unicast
  network 10.244.{n}.0/24
  network 10.200.0.100/32
  neighbor fabric-v4 activate
  neighbor fabric-v4 route-map OWN_ROUTES_ONLY out
  maximum-paths 8
 exit-address-family
 !
 address-family ipv6 unicast
  network 3fff:244:0:{n}::/64
  network 3fff:200::100/128
  neighbor fabric-v6 activate
  neighbor fabric-v6 route-map OWN_ROUTES_ONLY out
  maximum-paths 8
 exit-address-family
exit
!
bgp as-path access-list EMPTY seq 5 permit ^$
!
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
        (d / "frr.conf").write_text(frr_conf(n))


if __name__ == "__main__":
    main()
