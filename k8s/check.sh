#!/usr/bin/env bash
# Check the fake k8s nodes: dynamic BGP peers per leaf, the default route on
# every node, the peer chosen by the DefaultGateway nodes, how leaf1 reaches
# each pod CIDR and the anycast VIP, a full mesh of pod-to-pod pings and VIP
# pings from every leaf, IPv4 and IPv6.
#
# Racks 1-2 (k8s1-k8s4) peer unnumbered with both leafs: one session per link
# carrying IPv4 and IPv6. Rack 3 (k8s5, k8s6) peers like Cilium's DefaultGateway
# auto-discovery: one session per family, to the eth1 leaf (leaf5) while it is up.
C=clab-3-stage-evpn-vxlan-nvd-25
fail=0

echo "== dynamic peers in ipvrf-k8s"
declare -A want=([1]=2 [2]=2 [3]=2 [4]=2 [5]=4 [6]=0)
for l in 1 2 3 4 5 6; do
  up=$(docker exec $C-leaf$l sr_cli "show network-instance ipvrf-k8s protocols bgp neighbor" 2>/dev/null \
       | grep -c "| established ")
  echo "leaf$l: $up established (expect ${want[$l]})"
  [ "$up" = "${want[$l]}" ] || fail=1
done

echo "== default route on the nodes"
for n in 1 2 3 4; do
  v4=$(docker exec $C-k8s$n ip route show vrf k8s default | grep -c "nexthop via inet6 fe80")
  v6=$(docker exec $C-k8s$n ip -6 route show vrf k8s default | grep -c "nexthop via fe80")
  echo "k8s$n: BGP default over link-local, v4 $v4, v6 $v6 next hops (expect 2/2)"
  [ "$v4" = 2 ] && [ "$v6" = 2 ] || fail=1
done
for n in 5 6; do
  v4=$(docker exec $C-k8s$n ip route show vrf k8s default | grep -v linkdown | sort -t' ' -k9 -n | head -1 | awk '{print $3}')
  peers=$(docker exec $C-k8s$n vtysh -c "show bgp vrf k8s summary" 2>/dev/null \
          | grep -oE "^(10\.30\.[0-9.]+|3fff:10:30:[0-9a-f:]+)" | tr '\n' ' ')
  echo "k8s$n: kernel default via $v4, BGP peers: $peers(expect 10.30.5.1 3fff:10:30:5::1)"
  [ "$peers" = "10.30.5.1 3fff:10:30:5::1 " ] || fail=1
done

echo "== leaf1 next hops per pod CIDR / node IP / VIP"
for p in 10.244.{1..6}.0/24 10.31.0.{1..6}/32 10.200.0.100/32; do
  nh=$(docker exec $C-leaf1 sr_cli "show network-instance ipvrf-k8s route-table ipv4-unicast prefix $p detail" 2>/dev/null \
       | grep -oE "^ *[0-9a-f.:]+ \((direct|indirect)\)" | awk '{print $1}' | sort -u | tr '\n' ' ')
  echo "$p: ${nh:-MISSING}"
  [ -n "$nh" ] || fail=1
done

echo "== pod-to-pod pings (source pod gateway of each node)"
for a in 1 2 3 4 5 6; do
  line="k8s$a ->"
  for b in 1 2 3 4 5 6; do
    [ "$a" = "$b" ] && continue
    docker exec $C-k8s$a ip vrf exec k8s ping -c2 -i0.2 -W1 -I 10.244.$a.1 10.244.$b.1 >/dev/null 2>&1 && r4=ok || { r4=FAIL; fail=1; }
    docker exec $C-k8s$a ip vrf exec k8s ping -6 -c2 -i0.2 -W1 -I 3fff:244:0:$a::1 3fff:244:0:$b::1 >/dev/null 2>&1 && r6=ok || { r6=FAIL; fail=1; }
    line+=" k8s$b:$r4/$r6"
  done
  echo "$line"
done

echo "== anycast VIP 10.200.0.100 / 3fff:200::100 from leaf5/leaf6"
# leaf1-4 have no address of their own in ipvrf-k8s (unnumbered node ports)
for l in 5 6; do
  docker exec $C-leaf$l ip netns exec srbase-ipvrf-k8s ping -c2 -i0.2 -W1 -I 10.30.$l.1 10.200.0.100 >/dev/null 2>&1 && r4=ok || { r4=FAIL; fail=1; }
  docker exec $C-leaf$l ip netns exec srbase-ipvrf-k8s ping -6 -c2 -i0.2 -W1 -I 3fff:10:30:$l::1 3fff:200::100 >/dev/null 2>&1 && r6=ok || { r6=FAIL; fail=1; }
  echo "leaf$l: $r4/$r6"
done

[ $fail = 0 ] && echo "PASS" || { echo "FAIL"; exit 1; }
