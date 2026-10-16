#!/usr/bin/env bash
# Where is the VM, and can every leaf and a client per leaf pair reach its loopbacks?
#   [TENANT=1|2] check.sh     (tenant1: ipvrf-1 / vES, tenant2: ipvrf-2 / GW-IP)
LAB=${LAB:-clab-3-stage-evpn-vxlan-nvd-25}
. "$(dirname "$(readlink -f "$0")")/tenant.sh"

echo "== vm1 BGP ($NI)"
docker exec $LAB-vm1 vtysh -c "show bgp $FRR_VRF summary" 2>/dev/null | awk '/^[0-9a-f]+[.:]/{print "  "$1, $9, $10}'

route_type() {  # <leaf> <af> <prefix>: route type of the active route, or -
  docker exec $LAB-$1 sr_cli -d "info flat from state /network-instance $NI route-table $2-unicast" 2>/dev/null |
    grep -E "route $3 .* active true" | grep -oE "route-type [a-z-]+" | head -1 | cut -d' ' -f2
}
if [ -n "$VES" ]; then
  printf "\n== per leaf: VM MAC, vES DF candidates, VM loopbacks in %s\n" $NI
else
  printf "\n== per leaf: VM MAC, how the VM IP (GW-IP) resolves, VM loopbacks in %s\n" $NI
fi
for l in leaf1 leaf2 leaf3 leaf4 leaf5 leaf6; do
  mac=$(docker exec $LAB-$l sr_cli -d "info flat from state /network-instance $MACVRF bridge-table mac-table mac $VM_MAC destination" 2>/dev/null |
        sed -E "s/.* destination \"?([^\"]*)\"?\$/\1/; s/vxlan-interface:vxlan0.$VLAN //; s/ /,/g")
  # state paths, not show reports: 26.x dropped 'show ... route-table ipv4-unicast'
  r4=$(docker exec $LAB-$l sr_cli -d "info flat from state /network-instance $NI route-table ipv4-unicast" 2>/dev/null | grep -cE "route $PFX4/32 .* active true")
  r6=$(docker exec $LAB-$l sr_cli -d "info flat from state /network-instance $NI route-table ipv6-unicast" 2>/dev/null | grep -cE "route $PFX6/128 .* active true")
  if [ -n "$VES" ]; then
    set -- $VES
    c4=$(docker exec $LAB-$l sr_cli -d "show system network-instance ethernet-segments $1 detail" 2>/dev/null | awk -F': ' '/Candidates :/{print $2}')
    c6=$(docker exec $LAB-$l sr_cli -d "show system network-instance ethernet-segments $2 detail" 2>/dev/null | awk -F': ' '/Candidates :/{print $2}')
    printf "  %-5s mac=%-34s v4-cand=[%s] v6-cand=[%s] %s=%s %s=%s\n" $l "${mac:--}" "$c4" "$c6" $PFX4 $r4 $PFX6 $r6
  else
    g4=$(route_type $l ipv4 $VMIP4/32); g6=$(route_type $l ipv6 $VMIP6/128)
    printf "  %-5s mac=%-34s gw4=%-18s gw6=%-18s %s=%s %s=%s\n" $l "${mac:--}" "${g4:--}" "${g6:--}" $PFX4 $r4 $PFX6 $r6
  fi
done

printf "\n== ping %s (%s)\n" $PFX4 $VRF
for c in $CLIENTS; do
  printf "  %-5s %s\n" $c "$(docker exec $LAB-$c ip vrf exec $VRF ping -c3 -i0.2 -W1 $PFX4 2>&1 | grep -o '[0-9]*% packet loss')"
done
