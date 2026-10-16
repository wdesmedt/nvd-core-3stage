#!/usr/bin/env bash
# Move the VM to another hypervisor. vm1 carries two tenants, each with eBGP to the anchor
# leafs: VLAN 104 (10.1.4.16, 6.6.6.1, ipvrf-1, vES) and VLAN 204 in vrf2 (10.2.4.16,
# 6.6.6.2, ipvrf-2, RT5 GW-IP).
#
#   move-vm.sh <host1|host3|host6> [topo.clab.yml]
#
# Re-points vm1's link in the topology and runs a declarative `containerlab deploy`.
# containerlab doesn't re-run exec on link changes, so the script then re-plugs both
# ends: vm1 eth1 back into br0 (IPs and MAC live on br0.104/br0.204 and survive), and
# <hypervisor>:ethN.104/.204 into br104/br204, which bridge to bond0.104/.204. Both VLANs
# stay tagged from the VM to the leafs. Finally the VM announces itself, as after a live
# migration: a gratuitous ARP and an unsolicited NA (ndptool, from Alpine's libndp;
# installed on first use since the FRR image doesn't ship it) per VLAN.
set -euo pipefail

declare -A PORT=([host1]=eth5 [host3]=eth3 [host6]=eth3)
DST=${1:?usage: move-vm.sh <host1|host3|host6> [topo]}
TOPO=${2:-$(dirname "$(readlink -f "$0")")/../3-stage-evpn-vxlan-nvd-25.clab.yml}
[[ -n ${PORT[$DST]:-} ]] || { echo "unknown hypervisor $DST" >&2; exit 1; }
IF=${PORT[$DST]}
LAB=clab-$(awk '/^name:/{print $2; exit}' "$TOPO")

sed -i -E "s|\"vm1:eth1\", \"host[0-9]+:eth[0-9]+\"|\"vm1:eth1\", \"$DST:$IF\"|" "$TOPO"
grep -q "\"vm1:eth1\", \"$DST:$IF\"" "$TOPO"

echo "$(date +%T.%3N) deploy: vm1:eth1 -> $DST:$IF"
containerlab deploy -t "$TOPO" 2>&1 | grep -E "INFO (Applying|Apply)|ERRO|WARN" || true

echo "$(date +%T.%3N) plug $DST:$IF.104 into br104 and $IF.204 into br204"
docker exec "$LAB-$DST" sh -c "
  ip link set $IF mtu 9000
  ip link set $IF up
  for v in 104 204; do
    ip link show $IF.\$v >/dev/null 2>&1 || ip link add link $IF name $IF.\$v type vlan id \$v
    ip link set $IF.\$v mtu 9000
    ip link set $IF.\$v master br\$v
    ip link set $IF.\$v up
  done"

echo "$(date +%T.%3N) plug vm1:eth1 into br0, send GARP and unsolicited NA on VLAN 104 and 204"
docker exec "$LAB-vm1" sh -c "
  ip link set eth1 mtu 9000
  ip link set eth1 master br0
  ip link set eth1 up
  command -v ndptool >/dev/null || apk add -q --no-cache libndp >/dev/null 2>&1
  for i in 1 2 3; do
    arping -U -c 1 -I br0.104 10.1.4.16 >/dev/null 2>&1
    ndptool -t na -U -i br0.104 -T 3fff:10:1:4::16 send >/dev/null 2>&1
    arping -U -c 1 -I br0.204 10.2.4.16 >/dev/null 2>&1
    ndptool -t na -U -i br0.204 -T 3fff:10:2:4::16 send >/dev/null 2>&1
    sleep 0.5
  done; true"
echo "$(date +%T.%3N) done"
