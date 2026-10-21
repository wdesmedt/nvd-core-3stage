## Validated Features
* Jumbo IP/L2 MTU
* IPv4 packet forwarding over native IPv6-only LLA transport
* Single-session (multi-AF) eBGP between spines and leafs
  * Dynamic neighbors using IPv6 NDP/RA
  * Unique leaf ASNs, identical spine ASN
  * IPv4 Unicast AFI/SAFI – advertising system0/VTEP IPs on leafs using IPv6 next hops
  * L2VPN EVPN AFI/SAFI – signaling overlay
* Underlay/overlay BGP routing policies
  * AS path sets, route policies, prefix sets
* Underlay/overlay ECMP sets
  * BGP maximum paths, allow-multiple-as
  * Overlay – MAC/IP aliasing
* BGP fast failover techniques (BFD, rapid route withdrawal, rapid update, etc.)
* IPv4 + IPv6 Symmetric IRB
  * IPv4 DHCP Relay + Option 82
  * IPv6 SLAAC
  * Host routing (/32 or /128) via EVPN Type-2 MAC/IP routes + ARP/ND Snooping
  * IRB prefix routing via EVPN Type 5 routes\
  * Anycast GW (IPv4 + IPv6)
* MAC-VRFs with Layer 2 extension over EVPN
  * Advertisement of ARP/ND extended community
  * Conditional advertisement of ARP/ND route only if corresponding local MAC table entry exists
* IP-VRFs with Layer 3 extension over EVPN
  * EVPN-IFL Type 5 GW-IP advertisement and resolution/ECMP
* Bridged and routed VXLAN interfaces
* Shared services (route leaking via inter-instance policies)
* All-active/single-active Ethernet segments
  * Default (mod-based), preference-based DF election
    * Preference based only – AC-DF/non-revertive capabilities
  * Manual DF designation
  * Standby signaling for single-active (LACP/power-off)
  * EVPN-IFL host (Type 2 MAC/IP) ESI aliasing routes
  * Virtual Ethernet Segments (vES) for IP aliasing
* Layer 2 untagged/Layer 2 tagged/Layer 3 downlink configurations
* Single-homed/multi-homed port configurations
  * Multihoming via LACP/static LAG/active-backup (no LAG)
  * Ethernet reload-delay for all-active configurations
  * LACP fallback mode for LACP LAGs
* PE-CE IPv4/IPv6 unicast eBGP peerings
  * Replace peer AS (AS override)
  * Route redistribution from/to EVPN-IFL (Type 5)
  * Peering to anchor leafs and to directly connected leafs
  * BGP dynamic neighbors for Kubernetes worker nodes (ipvrf-k8s)
* Node isolation handling via Event Handler

## VM Mobility (vm1)
The PE-CE BGP workloads that used to live in vrf1 and vrf2 on host6 are modelled as one "VM", `vm1` (FRR), that moves between hypervisor hosts while keeping its IPs, MAC, VLAN tags and BGP configuration. It carries two tenants that use different EVPN designs, so both are tested under identical conditions:

| | Tenant 1 | Tenant 2 |
|---|---|---|
| VLAN / MAC-VRF | 104 / macvrf-104 | 204 / macvrf-204 |
| IP-VRF | ipvrf-1 (default VRF in vm1) | ipvrf-2 (`vrf2` in vm1) |
| VM address / loopbacks | 10.1.4.16, 3fff:10:1:4::16 / 6.6.6.1, 2001:db8::6:1 | 10.2.4.16, 3fff:10:2:4::16 / 6.6.6.2, 2001:db8::6:2 |
| How other leafs resolve the RT5 | IP-aliasing virtual ES (ESI) on every leaf | RT5 with GW-IP = VM address, no vES |

```
 vm1 (FRR container)                       host6 (hypervisor)                       fabric
┌───────────────────────────────┐         ┌──────────────────────────────┐
│ dummy11 6.6.6.1  (default)    │         │  br104           br204       │
│ dummy12 6.6.6.2  (vrf2)       │         │   ├ eth3.104 ◄┐   ├ eth3.204◄┐│
│                               │         │   └ bond0.104 │   └ bond0.204││   eth1 ─► leaf5 lag1
│ br0.104 10.1.4.16  (default)  │         │        └──────┼─ bond0 (LACP)┼┼─► eth2 ─► leaf6 lag1
│ br0.204 10.2.4.16  (vrf2)     │         │               │              ││   (ES HOST6, all-active)
│ br0  MAC 02:00:00:01:04:16    │         │               │              ││
│   ├─ eth1 ────────────────────┼─ veth ──┼─► eth3 ───────┴──────────────┘│
│   └─ br0-keep (dummy)         │         │                              │
│ bgpd AS 65001:                │         └──────────────────────────────┘
│  default → 10.1.4.201/.202,   │
│            3fff:10:1:4::201/2 │
│  vrf2    → 10.2.4.201/.202,   │
│            3fff:10:2:4::201/2 │
└───────────────────────────────┘
```

* vm1 (the VM)
  * Addresses on `br0.104` and `br0.204`, VLAN subinterfaces of bridge `br0`: the VM sends both VLANs tagged; `br0.204` and `dummy12` are in Linux VRF `vrf2`
  * Fixed MAC `02:00:00:01:04:16` on `br0`, so the MAC follows the VM across hypervisors
  * `eth1` (the veth to the hypervisor) is a `br0` port; a move only re-plugs `eth1`, the addresses and BGP stay untouched
  * `br0-keep` dummy port keeps `br0` carrier up while `eth1` is unplugged, so a move does not reset the BGP sessions (as with a real vMotion)
  * Two eBGP AS 65001 instances (default VRF and `vrf2`) to the anchor leafs leaf1/leaf2 IRB addresses, advertising only their own loopbacks (`OWN_ROUTES_ONLY`); config in `frr/vm1/frr.conf`
  * IPv6 sessions use `neighbor leafs-v6 update-source <VM IPv6>`. Without it the kernel picks the VM's SLAAC address (from the leafs' router advertisements) as source, the leafs reject vm1's connects, and IPv6 sessions only come up when the leaf connects itself: ~30 s after a reset instead of ~3 s
* Hypervisors host1 (leaf1-4), host3 (leaf3/4) and host6 (leaf5/6)
  * `bond0.104` and `bond0.204` are ports of Linux bridges `br104` and `br204` (no IP, no VRF)
  * The VM port (`eth3` on host6/host3, `eth5` on host1) gets `.104` and `.204` subinterfaces in those bridges: both VLANs are kept end to end
  * host6 no longer has its own vrf1/vrf2 or BGP; host6's FRR config is empty apart from the route-map
* Fabric
  * macvrf-104 / irb0.104 and macvrf-204 / irb0.204 (anycast GW .1) on all leafs, in ipvrf-1 / ipvrf-2
  * VLANs 104 and 204 on the host1 LAG (leaf1-4) and host3 LAG (leaf3/4)
  * Tenant 1: IP-aliasing virtual ESs on all leafs: `vES-host6-tenant1` (next-hop 10.1.4.16) and `vES-host6-tenant1-v6` (next-hop 3fff:10:1:4::16), all-active, evi 1
  * Tenant 2: `ip-prefix evpn-interface-less-gateway-ip advertise` and `resolve` in ipvrf-2 on all leafs, no vES
  * The startup configs in `srl-configs/` hold all of this (regenerated from the running leafs); the additions are also kept separately in `vm-mobility/leaf/<leaf>.cfg` (tenant 1) and `vm-mobility/leaf/<leaf>.tenant2.cfg` (tenant 2)
  * leaf6 has `host-route populate evpn` on irb0.104 and irb0.204 in its running config (the other leafs don't)

### Moving the VM
`vm1` is cabled with a single link in the topology (`vm1:eth1` to `<hypervisor>:<port>`). A move edits that link and runs a declarative `containerlab deploy` (containerlab 0.80+), which only changes the link; no node restarts.

```bash
vm-mobility/move-vm.sh host3 3-stage-evpn-vxlan-nvd-25.clab.yml
```

`move-vm.sh <host1|host3|host6> [topo]`:
1. rewrites the `vm1:eth1` link in the topology file
2. runs `containerlab deploy`
3. re-plugs both ends, because containerlab does not re-run `exec` on link changes: `<port>.104` / `<port>.204` into `br104` / `br204` on the hypervisor, `eth1` into `br0` on vm1
4. announces the VM on both VLANs, as a hypervisor does after a live migration: gratuitous ARP for 10.1.4.16 / 10.2.4.16 and unsolicited NA for 3fff:10:1:4::16 / 3fff:10:2:4::16 (`ndptool` from Alpine's `libndp`, installed in vm1 on first use)

### Test scripts
All scripts take `TENANT=1` (default: ipvrf-1, 6.6.6.1, vES) or `TENANT=2` (ipvrf-2, 6.6.6.2, GW-IP); the per-tenant settings live in `vm-mobility/tenant.sh`.

* `check.sh`: VM MAC location, vES DF candidates (tenant 1) or how each leaf resolves the GW-IP (tenant 2), presence of both VM loopbacks in the IP-VRF on every leaf (state paths, works on 25.10 and 26.x), and a ping from one client per leaf pair
* `leaf-impact.sh <host> [topo] [settle]`: moves the VM while every leaf pings both loopbacks from its own IP-VRF namespace (`srbase-<ipvrf>`) at 10 pps, sourced from its unique irb address; reports the longest gap and ICMP errors per leaf, and the PE-CE BGP session counters on vm1 and leaf1/leaf2 before and after
* `loop-moves.sh [moves] [settle] [topo]`: alternates moves host3 ↔ host6 with `leaf-impact.sh`; on the first move where a leaf is still failing at the end, it captures ND/ARP/MAC/route/vES/RT2 state on every leaf and vm1's neighbor cache into `vm-mobility/captures/`, times the recovery and captures again
* `test-move.sh <host> [topo] [settle]`: moves the VM while client hosts ping 6.6.6.1, reports the longest gap per client
* `patch-topo.py`, `patch-topo-tenant2.py`: the one-off topology changes that turned host1/host3/host6 into hypervisors, added vm1 and moved tenant 2 into it

Probe notes: ping with `-c`, not `-w` (with a deadline iputils ping exits on the first ICMP error); and always source leaf probes from a unique address, since the default source can be an anycast gateway address that every leaf owns.

### Results (SR Linux 25.10.5)
| | Tenant 1 · vES | Tenant 2 · GW-IP |
|---|---|---|
| BGP reset on vm1 (`clear bgp *` / `clear bgp vrf vrf2 *`) | leaf1/2 (BGP) and attached leafs back in 2.9–3.3 s; **leaf3/leaf4 never recover** (see known issue) | **all six leafs back in 3.3 s**, IPv4 and IPv6 |
| VM moves host3 ↔ host6, BGP kept up | 30 moves, 0.8–1.0 s on every leaf (one move had a single transient ICMPv6 "No route" inside that window) | 20 moves, 0.8–1.0 s on every leaf, no ICMP errors |
| PE-CE sessions during moves | never flapped | never flapped |

`vm-mobility/ves-bgp-reset-blackhole.html` is a self-contained animated deck that walks through the vES issue and this comparison.

### Known issue: vES RT5 not reinstalled after re-advertisement
A leaf that has the vES configured, is not attached (the VM is not behind it) and has no PE-CE BGP to the VM does not reinstall the VM's RT5s (carrying the vES ESI) after they are withdrawn and re-advertised: after a VM BGP session reset or after a deploy. The prefixes stay missing from ipvrf-1 on that leaf (traffic entering there is dropped) until the vES is toggled:

```
set / system network-instance protocols evpn ethernet-segments bgp-instance 1 ethernet-segment vES-host6-tenant1 admin-state disable
set / system network-instance protocols evpn ethernet-segments bgp-instance 1 ethernet-segment vES-host6-tenant1 admin-state enable
```

(same for `vES-host6-tenant1-v6`). Reproduce with `clear bgp *` on vm1 followed by `check.sh` (leaf3/leaf4 lose both loopbacks while the VM is on host6). VM moves without a BGP reset are clean. Tenant 2 (GW-IP) is not affected.
* Reproduced on 25.10.4, 25.10.5 and 26.7.2
* Not listed as a known or resolved issue in the 25.10.6, 26.7.1 or 26.7.3 release notes
* Do not use `arp/nd host-route populate evpn` on irb0.104 as a workaround: the leaf then exports the VM /32 as an RT5 and attaches to the vES, which pulls the VM's own IP towards the wrong leaf

## Kubernetes worker nodes (k8s1-k8s6)
Six FRR containers act as k8s worker nodes whose CNI (Calico, Cilium, MetalLB FRR mode) speaks BGP to the fabric. Each one advertises its pod CIDR and an anycast LoadBalancer VIP and only needs a default route back. The design keeps the fabric config independent of the number of nodes: adding a node needs no leaf change.

| | |
|---|---|
| Racks | two nodes per leaf pair: k8s1/k8s2 on leaf1/leaf2, k8s3/k8s4 on leaf3/leaf4, k8s5/k8s6 on leaf5/leaf6 |
| Cabling | `eth1` to the odd leaf, `eth2` to the even leaf (`ethernet-1/10` for odd nodes, `ethernet-1/11` for even), no LAG or ES |
| Node subnet | leaf-local, not stretched: `10.30.<leaf>.0/24` and `3fff:10:30:<leaf>::/64` on `irb0.300` (gateway `.1`/`::1`) in `macvrf-k8s`, a MAC-VRF without EVPN. Node n is `.1n`/`::1n` on both leafs |
| Pod CIDR | `10.244.<n>.0/24`, `3fff:244:0:<n>::/64` (gateway `.1`/`::1` on dummy `pods`) |
| Anycast VIP | `10.200.0.100/32`, `3fff:200::100/128` on every node (dummy `vip`) |
| Cluster AS | 65100 on all nodes; the leafs use 64512 in ipvrf-k8s |
| IP-VRF | `ipvrf-k8s`, evi 3, VNI 3, `target:1:3`, `ecmp 128` |

Node side (`frr/k8s<n>/frr.conf`, all in Linux VRF `k8s`): peers with the irb address of both its leafs (its two default gateways), BFD, keepalive 3 s / hold 9 s and `timers connect 5`. The leafs are passive (dynamic neighbors), so the node's connect-retry sets how fast a session comes back. It advertises its own routes only (`OWN_ROUTES_ONLY`), and the kernel ECMPs the default route over both leafs.

Leaf side (`k8s/leaf/<leaf>.cfg`, also appended to `srl-configs/`), the same block on every leaf apart from the subnet:
* `dynamic-neighbors accept match 10.30.<leaf>.0/24 peer-group k8s-v4 allowed-peer-as [ 65100 ]` and the IPv6 equivalent
* `import-policy k8s-import`: only pod CIDRs (`10.244.0.0/16` /24-/26, `3fff:244::/32` /64) and VIPs (`10.200.0.0/16` /32, `3fff:200::/32` /128); `prefix-limit-received` 64
* `export-policy k8s-export` rejects everything; the nodes get only `send-default-route`
* `send-default-route export-policy k8s-default` (accept): without its own policy the leafs sent no default at all (25.10.5), even with 0/0 accepted in `k8s-export`
* BFD on `irb0.300`, `failure-detection enable-bfd` and `fast-failover`; `multipath ebgp maximum-paths 16`
* No vES and no GW-IP: every leaf a node is attached to has its own session, so remote leafs ECMP over both leafs of a rack through ordinary RT5s

`k8s/gen.py` writes both sides; regenerate and re-apply after changing the addressing. Spines are untouched.

### Test script
`k8s/check.sh`: established dynamic peers per leaf, ECMP default on every node, leaf1's next hops for each pod CIDR and the VIP, a full mesh of pod-to-pod pings and VIP pings from every leaf, IPv4 and IPv6.

### Results (SR Linux 25.10.5)
* All 24 sessions (6 nodes x 2 leafs x v4/v6) established with BFD; `check.sh` passes
* Remote racks: leaf1 reaches k8s3's pod CIDR over leaf3 and leaf4 (ECMP)
* The anycast VIP is ECMPed only over the local nodes on a leaf that has some: the local eBGP route wins over the RT5s. A leaf without nodes would ECMP over the RT5s of all six leafs (not tested, no such leaf)
* Link failure k8s1 eth1 (leaf1) during a 20 pps stream from k8s6: no loss; IPv4 and IPv6 sessions back about 5-7 s after the link returns

Lab notes:
* Node exec sets `keep_addr_on_down=1` on `eth1`/`eth2`. Without it, `ip link set eth1 down` flushes the static IPv6 address, and the IPv6 session stays `Active` after the link comes back
* `accept_ra=0` on the node links, so the kernel doesn't source BGP from a SLAAC address (see the vm1 IPv6 note)
