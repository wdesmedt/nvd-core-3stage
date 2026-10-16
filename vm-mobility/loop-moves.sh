#!/usr/bin/env bash
# Move vm1 back and forth between host3 and host6 with leaf-impact.sh until a leaf still
# can't reach a VM loopback at the end of the probe window, or <max> clean moves.
# On failure: capture ND/ARP/MAC/route/vES/RT2 state on every leaf and vm1's neighbor
# cache, time the recovery of the failing leafs, then capture again.
#
#   loop-moves.sh [max-moves=20] [settle-seconds=30] [topo.clab.yml]
set -uo pipefail
DIR=$(dirname "$(readlink -f "$0")")
LAB=${LAB:-clab-3-stage-evpn-vxlan-nvd-25}
MAX=${1:-20}
SETTLE=${2:-30}
TOPO=${3:-$DIR/../3-stage-evpn-vxlan-nvd-25.clab.yml}
LEAFS="leaf1 leaf2 leaf3 leaf4 leaf5 leaf6"
MAC=02:00:00:01:04:16

leaf_state() {  # <leaf>
  local l=$1 c="docker exec $LAB-$1 sr_cli -d"
  echo "### $l $(date +%T.%3N)"
  echo "-- ND / ARP for the VM";       $c "show arpnd neighbors interface irb0 subinterface 104" | grep -E "3fff:10:1:4::16 |fe80::ff:fe01:416 "
                                       $c "show arpnd arp-entries interface irb0 subinterface 104" | grep -E "10.1.4.16 "
  echo "-- MAC table";                 $c "info flat from state /network-instance macvrf-104 bridge-table mac-table mac $MAC"
  echo "-- ipvrf-1 routes (state)";    $c "info flat from state /network-instance ipvrf-1 route-table ipv6-unicast" | grep -E "route (2001:db8::6:1/128|3fff:10:1:4::16/128) " | grep -E " (active|next-hop-group) "
                                       $c "info flat from state /network-instance ipvrf-1 route-table ipv4-unicast" | grep -E "route (6.6.6.1/32|10.1.4.16/32) " | grep -E " (active|next-hop-group) "
  echo "-- route detail";              $c "show network-instance ipvrf-1 route-table ipv6-unicast prefix 2001:db8::6:1/128 detail" 2>&1 | grep -vE "^[-=+]+$|^$"
                                       $c "show network-instance ipvrf-1 route-table ipv6-unicast prefix 3fff:10:1:4::16/128 detail" 2>&1 | grep -vE "^[-=+]+$|^$"
  echo "-- vES";                       $c "show system network-instance ethernet-segments vES-host6-tenant1-v6 detail" | grep -E "Oper State|Candidates :"
                                       $c "show system network-instance ethernet-segments vES-host6-tenant1 detail" | grep -E "Oper State|Candidates :"
  echo "-- RT2 for the VM MAC";        $c "show network-instance default protocols bgp routes evpn route-type 2 mac-address $MAC detail" 2>&1 |
                                         grep -E "Route Distinguisher|IP Address|ESI|Path 1|Communities|Invalid" | sort -u
}
capture() {  # <dir> <tag>
  for l in $LEAFS; do leaf_state $l; done > "$1/leafs-$2.txt" 2>&1
  { echo "### vm1 $(date +%T.%3N)"; docker exec $LAB-vm1 sh -c 'ip -6 neigh show dev br0.104; ip -4 neigh show dev br0.104; bridge fdb show br br0 | grep -v permanent'
    docker exec $LAB-vm1 vtysh -c "show bgp summary" 2>/dev/null | grep -E "^[0-9a-f]+[.:]"; } > "$1/vm1-$2.txt" 2>&1
}

current=$(grep -oE '"vm1:eth1", "host[0-9]+' "$TOPO" | grep -oE 'host[0-9]+$')
for i in $(seq 1 "$MAX"); do
  dst=$([ "$current" = host6 ] && echo host3 || echo host6)
  out=$(KEEP=1 "$DIR/leaf-impact.sh" "$dst" "$TOPO" "$SETTLE" 2>&1)
  raw=$(echo "$out" | sed -n 's/^raw output kept in //p')
  current=$dst
  summary=$(echo "$out" | sed -n '/per-leaf impact/,/^$/p' | awk 'NR>2 && NF{printf "%s v4=%s v6=%s  ", $1, $2, $3}')
  if ! echo "$out" | grep -q FAILING-AT-END; then
    echo "move $i -> $dst: clean   $summary"
    # ICMP errors seen during the move, with leaf, family and timestamp
    grep -H -E "^\[[0-9.]+\] From " "$raw"/*.v4 "$raw"/*.v6 2>/dev/null |
      sed -E 's|^.*/(leaf[0-9]+)\.(v[46]):|    icmp-error \1 \2 |'
    rm -rf "$raw"; continue
  fi

  cap=$DIR/captures/$(date +%Y%m%d-%H%M%S)-move$i-$dst
  mkdir -p "$cap"; mv "$raw" "$cap/pings"; echo "$out" > "$cap/leaf-impact.txt"
  echo "move $i -> $dst: FAILED  $summary"
  capture "$cap" failing
  failing=$(echo "$out" | sed -n '/per-leaf impact/,/^$/p' | awk '/FAILING-AT-END/{print $1}')
  echo "  failing leafs: $failing; capture in $cap"

  # time recovery per failing leaf and family, polling every 2 s for up to 5 min
  for l in $failing; do
    n=${l#leaf}
    for af in 4 6; do
      if [ $af = 4 ]; then a="-I 10.1.4.20$n 6.6.6.1"; else a="-6 -I 3fff:10:1:4::20$n 2001:db8::6:1"; fi
      docker exec $LAB-$l ip netns exec srbase-ipvrf-1 ping -c1 -W1 $a >/dev/null 2>&1 && continue
      t0=$(date +%s); rec=">300s"
      while [ $(( $(date +%s) - t0 )) -lt 300 ]; do
        sleep 2
        if docker exec $LAB-$l ip netns exec srbase-ipvrf-1 ping -c1 -W1 $a >/dev/null 2>&1; then rec="$(( $(date +%s) - t0 + SETTLE ))s after the move (approx.)"; break; fi
      done
      echo "  $l IPv$af recovered: $rec" | tee -a "$cap/recovery.txt"
    done
  done
  capture "$cap" recovered
  exit 1
done
echo "$MAX clean moves"
