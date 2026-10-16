#!/usr/bin/env python3
"""Turn host6/host1/host3 into hypervisors (br104) and add the movable VM vm1.

Usage: patch-topo.py <topo.clab.yml>   (idempotent)
"""
import re
import sys

path = sys.argv[1]
lines = open(path).read().split("\n")

HOST6_DROP = [
    "ip link add vrf1 type vrf table 111",
    "ip link set vrf1 up",
    "ip link set bond0.104 master vrf1",
    "ip addr add 10.1.4.16/24 dev bond0.104",
    "ip -6 addr add 3fff:10:1:4::16/64 dev bond0.104",
    "ip link add dummy11 type dummy",
    "ip link set dummy11 master vrf1",
    "ip link set dummy11 up",
    "ip link set dummy11 mtu 9000",
    "ip addr add 6.6.6.1/32 dev dummy11",
    "ip -6 addr add 2001:db8::6:1/128 dev dummy11",
]
BR104 = [
    "ip link add br104 type bridge",
    "ip link set br104 mtu 9000",
    "ip link set bond0.104 master br104",
    "ip link set br104 up",
]
VLAN104 = [
    "ip link add link bond0 name bond0.104 type vlan id 104",
    "ip link set bond0.104 mtu 9000",
    "ip link set bond0.104 up",
]
VM1 = """    vm1:
      group: frr
      exec:
        - ip link add br0 type bridge
        - ip link set br0 address 02:00:00:01:04:16
        - ip link set br0 mtu 9000
        - ip link set br0 up
        - ip link add br0-keep type dummy
        - ip link set br0-keep master br0
        - ip link set br0-keep up
        - ip link add link br0 name br0.104 type vlan id 104
        - ip link set br0.104 mtu 9000
        - ip link set br0.104 up
        - ip addr add 10.1.4.16/24 dev br0.104
        - ip -6 addr add 3fff:10:1:4::16/64 dev br0.104
        - ip link add dummy11 type dummy
        - ip link set dummy11 mtu 9000
        - ip link set dummy11 up
        - ip addr add 6.6.6.1/32 dev dummy11
        - ip -6 addr add 2001:db8::6:1/128 dev dummy11
        - ip link set eth1 mtu 9000
        - ip link set eth1 master br0
        - ip link set eth1 up""".split("\n")
VM1_LINK = '    - endpoints: [ "vm1:eth1", "host6:eth3" ]'

if any(l.strip() == "vm1:" for l in lines):
    sys.exit(f"{path}: already patched")


def node_span(name):
    start = lines.index(f"    {name}:")
    end = start + 1
    while end < len(lines) and not re.match(r"    \S", lines[end]) and lines[end].strip() not in ("links:",):
        end += 1
    return start, end


def last_exec_line(start, end):
    return max(i for i in range(start, end) if lines[i].startswith("        - "))


# host6: vrf1 leaves for vm1, bond0.104 goes into br104
s, e = node_span("host6")
for i in range(e - 1, s, -1):
    if lines[i].strip().lstrip("- ") in HOST6_DROP:
        del lines[i]
s, e = node_span("host6")
anchor = next(i for i in range(s, e) if "ip link set bond0.104 mtu 9000" in lines[i])
lines[anchor + 1:anchor + 1] = [f"        - {c}" for c in BR104]

# host1/host3: add VLAN 104 on the bond and br104
for host in ("host1", "host3"):
    s, e = node_span(host)
    at = last_exec_line(s, e)
    if "lldpd" in lines[at]:
        at -= 1
    lines[at + 1:at + 1] = [f"        - {c}" for c in VLAN104 + BR104]

# vm1 node right after host6
s, e = node_span("host6")
while lines[e - 1].strip() == "":
    e -= 1
lines[e:e] = VM1

# vm1 link, initially behind host6
at = max(i for i, l in enumerate(lines) if '"host6:eth2"' in l)
lines.insert(at + 1, VM1_LINK)

open(path, "w").write("\n".join(lines))
print(f"{path}: patched")
