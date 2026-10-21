#!/usr/bin/env bash
# Check the fake k8s nodes: dynamic BGP peers per leaf, the default route
# (ECMP over both leafs) on every node, how leaf1 reaches each pod CIDR and the
# anycast VIP, and a full mesh of pod-to-pod pings, IPv4 and IPv6.
C=clab-3-stage-evpn-vxlan-nvd-25
NODES=${NODES:-"1 2 3 4 5 6"}
LEAFS=${LEAFS:-"1 2 3 4 5 6"}
fail=0

echo "== dynamic peers in ipvrf-k8s (expect 4 per leaf: 2 nodes x v4/v6)"
for l in $LEAFS; do
  up=$(docker exec $C-leaf$l sr_cli "show network-instance ipvrf-k8s protocols bgp neighbor" 2>/dev/null \
       | grep -c "| established ")
  echo "leaf$l: $up established"
  [ "$up" = 4 ] || fail=1
done

echo "== default route on the nodes (expect 2 next hops per family)"
for n in $NODES; do
  v4=$(docker exec $C-k8s$n ip route show vrf k8s default | grep -c nexthop)
  v6=$(docker exec $C-k8s$n ip -6 route show vrf k8s default | grep -c nexthop)
  echo "k8s$n: v4 $v4, v6 $v6"
  [ "$v4" = 2 ] && [ "$v6" = 2 ] || fail=1
done

echo "== leaf1 next hops per pod CIDR / VIP"
for p in $(for n in $NODES; do echo 10.244.$n.0/24; done) 10.200.0.100/32; do
  nh=$(docker exec $C-leaf1 sr_cli "show network-instance ipvrf-k8s route-table ipv4-unicast prefix $p detail" 2>/dev/null \
       | grep -oE "^ *[0-9.]+ \((direct|indirect)\)" | awk '{print $1}' | sort -u | tr '\n' ' ')
  echo "$p: ${nh:-MISSING}"
  [ -n "$nh" ] || fail=1
done

echo "== pod-to-pod pings (source pod gateway of each node)"
for a in $NODES; do
  line="k8s$a ->"
  for b in $NODES; do
    [ "$a" = "$b" ] && continue
    docker exec $C-k8s$a ip vrf exec k8s ping -c2 -i0.2 -W1 -I 10.244.$a.1 10.244.$b.1 >/dev/null 2>&1 && r4=ok || { r4=FAIL; fail=1; }
    docker exec $C-k8s$a ip vrf exec k8s ping -6 -c2 -i0.2 -W1 -I 3fff:244:0:$a::1 3fff:244:0:$b::1 >/dev/null 2>&1 && r6=ok || { r6=FAIL; fail=1; }
    line+=" k8s$b:$r4/$r6"
  done
  echo "$line"
done

echo "== anycast VIP 10.200.0.100 / 3fff:200::100 from every leaf"
for l in $LEAFS; do
  docker exec $C-leaf$l ip netns exec srbase-ipvrf-k8s ping -c2 -i0.2 -W1 -I 10.30.$l.1 10.200.0.100 >/dev/null 2>&1 && r4=ok || { r4=FAIL; fail=1; }
  docker exec $C-leaf$l ip netns exec srbase-ipvrf-k8s ping -6 -c2 -i0.2 -W1 -I 3fff:10:30:$l::1 3fff:200::100 >/dev/null 2>&1 && r6=ok || { r6=FAIL; fail=1; }
  echo "leaf$l: $r4/$r6"
done

[ $fail = 0 ] && echo "PASS" || { echo "FAIL"; exit 1; }
