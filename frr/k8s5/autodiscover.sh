#!/bin/sh
# Mimics Cilium BGP autoDiscovery mode DefaultGateway: one session per address
# family, to the gateway of the lowest-metric kernel default route in VRF k8s
# that is not linkdown. Re-checked every 2 s; the peer moves when that route
# changes (e.g. the link to the primary leaf goes down).
VRF=k8s
AS=65100

gateway() {
    ip "$1" route show vrf "$VRF" default | grep -v -e linkdown -e 'proto bgp' |
        awk '{m = 0; for (i = 1; i < NF; i++) { if ($i == "via") g = $(i + 1); if ($i == "metric") m = $(i + 1) }
              print m, g}' | sort -n | awk 'NR == 1 { print $2 }'
}

move_peer() {  # family old new
    fam=$1 old=$2 new=$3
    set -- -c "configure terminal" -c "router bgp $AS vrf $VRF"
    [ -n "$old" ] && set -- "$@" -c "no neighbor $old"
    [ -n "$new" ] && set -- "$@" -c "neighbor $new peer-group fabric-v$fam"
    vtysh "$@" >/dev/null 2>&1 || return 1
    echo "$(date +%T) v$fam peer ${old:-none} -> ${new:-none}"
}

cur4= cur6=
while :; do
    new4=$(gateway -4)
    if [ "$new4" != "$cur4" ] && move_peer 4 "$cur4" "$new4"; then cur4=$new4; fi
    new6=$(gateway -6)
    if [ "$new6" != "$cur6" ] && move_peer 6 "$cur6" "$new6"; then cur6=$new6; fi
    sleep 2
done
