#!/usr/bin/env bash
# Move vm1 to <hypervisor> while clients ping 6.6.6.1 at 10 pps, then report the
# longest gap per client and the state on every leaf.
#
#   test-move.sh <host1|host3|host6> [topo.clab.yml] [settle-seconds]
set -uo pipefail
DIR=$(dirname "$(readlink -f "$0")")
LAB=${LAB:-clab-3-stage-evpn-vxlan-nvd-25}
DST=${1:?usage: test-move.sh <host1|host3|host6> [topo] [settle]}
TOPO=${2:-$DIR/../3-stage-evpn-vxlan-nvd-25.clab.yml}
SETTLE=${3:-45}
OUT=$(mktemp -d)
CLIENTS="host2 host7 host1 host3"

for c in $CLIENTS; do
  docker exec $LAB-$c ip vrf exec vrf1 ping -D -i 0.1 -W1 6.6.6.1 > $OUT/$c.txt 2>&1 &
done
sleep 2
"$DIR/move-vm.sh" "$DST" "$TOPO"
sleep "$SETTLE"
for c in $CLIENTS; do docker exec $LAB-$c pkill -INT ping 2>/dev/null; done
wait 2>/dev/null

printf "\n== longest ping gap per client (vrf1 -> 6.6.6.1)\n"
for c in $CLIENTS; do
  awk -v c=$c -F'icmp_seq=' '
    /bytes from/ {split($2,a," "); s=a[1]+0; if (p && s-p-1 > g) g=s-p-1; p=s; last=s}
    /icmp_seq=/  {split($2,b," "); if (b[1]+0 > max) max=b[1]+0}
    END {printf "  %-5s gap=%5.1fs  still-failing-at-end=%s\n", c, g/10, (max>last+5 ? "YES" : "no")}' $OUT/$c.txt
done
echo
"$DIR/check.sh"
rm -rf "$OUT"
