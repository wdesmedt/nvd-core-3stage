#!/usr/bin/env python3
"""Make the tenant2 workload (10.2.4.16, 6.6.6.2, vrf2 BGP) part of the movable VM.

vm1 gets VLAN 204 in its own vrf2; host6 hands 10.2.4.16 over to vm1, and host1/host3/
host6 bridge VLAN 204 (br204) like VLAN 104. Run after patch-topo.py.

Usage: patch-topo-tenant2.py <topo.clab.yml>   (idempotent)
"""
import re
import sys

path = sys.argv[1]
lines = open(path).read().split("\n")

HOST6_DROP = [
    "ip link add vrf2 type vrf table 121",
    "ip link set vrf2 up",
    "ip link set bond0.204 master vrf2",
    "ip addr add 10.2.4.16/24 dev bond0.204",
    "ip -6 addr add 3fff:10:2:4::16/64 dev bond0.204",
    "ip link add dummy12 type dummy",
    "ip link set dummy12 master vrf2",
    "ip link set dummy12 up",
    "ip link set dummy12 mtu 9000",
    "ip addr add 6.6.6.2/32 dev dummy12",
    "ip -6 addr add 2001:db8::6:2/128 dev dummy12",
]
BR204 = [
    "ip link add br204 type bridge",
    "ip link set br204 mtu 9000",
    "ip link set bond0.204 master br204",
    "ip link set br204 up",
]
VLAN204 = [
    "ip link add link bond0 name bond0.204 type vlan id 204",
    "ip link set bond0.204 mtu 9000",
    "ip link set bond0.204 up",
]
VM1_T2 = [
    "ip link add vrf2 type vrf table 121",
    "ip link set vrf2 up",
    "ip link add link br0 name br0.204 type vlan id 204",
    "ip link set br0.204 mtu 9000",
    "ip link set br0.204 master vrf2",
    "ip link set br0.204 up",
    "ip addr add 10.2.4.16/24 dev br0.204",
    "ip -6 addr add 3fff:10:2:4::16/64 dev br0.204",
    "ip link add dummy12 type dummy",
    "ip link set dummy12 master vrf2",
    "ip link set dummy12 mtu 9000",
    "ip link set dummy12 up",
    "ip addr add 6.6.6.2/32 dev dummy12",
    "ip -6 addr add 2001:db8::6:2/128 dev dummy12",
]

if any("br0.204" in l for l in lines):
    sys.exit(f"{path}: already patched")


def node_span(name):
    start = lines.index(f"    {name}:")
    end = start + 1
    while end < len(lines) and not re.match(r"    \S", lines[end]) and lines[end].strip() != "links:":
        end += 1
    return start, end


def cmd(l):
    return l.strip()[2:].strip().strip('"') if l.strip().startswith("- ") else None


def insert_after(node, needle, cmds):
    s, e = node_span(node)
    at = next(i for i in range(s, e) if cmd(lines[i]) == needle)
    lines[at + 1:at + 1] = [f"        - {c}" for c in cmds]


def insert_before_lldp(node, cmds):
    s, e = node_span(node)
    execs = [i for i in range(s, e) if cmd(lines[i])]
    at = next((i for i in execs if "lldpd" in lines[i] or "apk add" in lines[i]), execs[-1] + 1)
    lines[at:at] = [f"        - {c}" for c in cmds]


# host6: 10.2.4.16 and vrf2 move to vm1, bond0.204 goes into br204
s, e = node_span("host6")
for i in range(e - 1, s, -1):
    if cmd(lines[i]) in HOST6_DROP:
        del lines[i]
insert_after("host6", "ip link set bond0.204 mtu 9000", BR204)

# host1/host3: VLAN 204 on the bond, bridged
for host in ("host1", "host3"):
    insert_before_lldp(host, VLAN204 + BR204)

# vm1: second vNIC VLAN in its own vrf2
insert_after("vm1", "ip link set eth1 up", VM1_T2)

open(path, "w").write("\n".join(lines))
print(f"{path}: patched")
