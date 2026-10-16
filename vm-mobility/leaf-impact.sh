#!/usr/bin/env bash
# Move vm1 to <hypervisor> while every leaf pings the VM loopbacks from its own ip-vrf
# (10 pps, IPv4 and IPv6), and check that the PE-CE BGP sessions to the anchor leafs
# (leaf1/leaf2) survive the move.
#
#   [TENANT=1|2] leaf-impact.sh <host1|host3|host6> [topo.clab.yml] [settle-seconds]
#   TENANT=1: ipvrf-1, 6.6.6.1 (vES)   TENANT=2: ipvrf-2, 6.6.6.2 (GW-IP)
set -uo pipefail
DIR=$(dirname "$(readlink -f "$0")")
LAB=${LAB:-clab-3-stage-evpn-vxlan-nvd-25}
DST=${1:?usage: leaf-impact.sh <host1|host3|host6> [topo] [settle]}
TOPO=${2:-$DIR/../3-stage-evpn-vxlan-nvd-25.clab.yml}
SETTLE=${3:-30}
LEAFS="leaf1 leaf2 leaf3 leaf4 leaf5 leaf6"
OUT=$(mktemp -d)
. "$DIR/tenant.sh"

FRR_PY=$(cat <<'EOF'
import json, sys
for nb, d in sorted(json.load(sys.stdin).items()):
    print("vm1->%s %s est=%s drop=%s" % (nb, d.get("bgpState"), d.get("connectionsEstablished"), d.get("connectionsDropped")))
EOF
)

# BGP sessions: FRR (vm1) established/dropped counters and uptime, leaf1/2 established-transitions
bgp_state() {
  docker exec $LAB-vm1 vtysh -c "show bgp $FRR_VRF neighbors json" 2>/dev/null | python3 -c "$FRR_PY"
  for l in leaf1 leaf2; do
    for nb in $VMIP4 $VMIP6; do
      docker exec $LAB-$l sr_cli -d "info from state /network-instance $NI protocols bgp neighbor $nb" 2>/dev/null |
        awk -v l=$l -v nb=$nb '/session-state /{s=$2} /established-transitions /{t=$2} END{print l"->"nb" "s" transitions="t}'
    done
  done
}

bgp_state > $OUT/bgp.before
DUR=$((SETTLE + 10))
# -c, not -w: with a deadline iputils ping exits on the first ICMP error (e.g. a transient
# 'Destination unreachable: No route' during the move), which looks like a lasting outage.
CNT=$((DUR * 10))
# Source from the leaf's unique irb address (e.g. 10.1.4.20N): the default source can be an
# anycast gateway address (10.1.1.1) that every leaf owns, and the reply then lands elsewhere.
for l in $LEAFS; do
  n=${l#leaf}
  docker exec $LAB-$l ip netns exec srbase-$NI ping -D -i 0.1 -W1 -c $CNT -I $VMNET4.20$n $PFX4 > $OUT/$l.v4 2>&1 &
  docker exec $LAB-$l ip netns exec srbase-$NI ping -6 -D -i 0.1 -W1 -c $CNT -I $VMNET6::20$n $PFX6 > $OUT/$l.v6 2>&1 &
done
sleep 2
"$DIR/move-vm.sh" "$DST" "$TOPO"
wait
bgp_state > $OUT/bgp.after

gap() {  # longest run of missing replies, ICMP errors received, replies lost at the end
  awk -F'icmp_seq=' '
    /bytes from/ {split($2,a," "); s=a[1]+0; if (p && s-p-1 > g) g=s-p-1; p=s}
    /^\[[0-9.]+\] From / {e++}
    /transmitted/ {split($0,b," "); sent=b[1]+0}
    END {tail=(sent>p ? sent-p : 0)
         printf "%.1fs%s%s", g/10, (e ? "(" e "err)" : ""), (tail>5 ? "!FAILING-AT-END" : "")}' "$1"
}
printf "\n== per-leaf impact (%s ping, longest gap)\n" $NI
printf "  %-6s %-22s %-22s\n" leaf "$PFX4" "$PFX6"
for l in $LEAFS; do printf "  %-6s %-22s %-22s\n" $l "$(gap $OUT/$l.v4)" "$(gap $OUT/$l.v6)"; done

printf "\n== PE-CE BGP sessions (before -> after)\n"
paste -d'|' $OUT/bgp.before $OUT/bgp.after | awk -F'|' '{printf "  %-55s -> %s\n", $1, $2}'
echo
"$DIR/check.sh" | sed -n '/per leaf/,$p'
if [ -n "${KEEP:-}" ]; then echo "raw output kept in $OUT"; else rm -rf "$OUT"; fi
