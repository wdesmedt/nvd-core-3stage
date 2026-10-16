# Per-tenant settings for the vm-mobility scripts, sourced by check.sh and leaf-impact.sh.
#   TENANT=1 (default): VLAN 104, ipvrf-1, 6.6.6.1, IP-aliasing virtual ES
#   TENANT=2:           VLAN 204, ipvrf-2, 6.6.6.2, RT5 with GW-IP (no vES)
TENANT=${TENANT:-1}
case $TENANT in
  1) NI=ipvrf-1; MACVRF=macvrf-104; VLAN=104; VMNET4=10.1.4; VMNET6=3fff:10:1:4
     PFX4=6.6.6.1; PFX6=2001:db8::6:1; VRF=vrf1; FRR_VRF=""; CLIENTS="host2 host7 host1 host3"
     VES="vES-host6-tenant1 vES-host6-tenant1-v6" ;;
  2) NI=ipvrf-2; MACVRF=macvrf-204; VLAN=204; VMNET4=10.2.4; VMNET6=3fff:10:2:4
     PFX4=6.6.6.2; PFX6=2001:db8::6:2; VRF=vrf2; FRR_VRF="vrf vrf2"; CLIENTS="host4 host7 host1 host3"
     VES="" ;;
  *) echo "TENANT must be 1 or 2" >&2; exit 2 ;;
esac
VMIP4=$VMNET4.16; VMIP6=$VMNET6::16
VM_MAC=02:00:00:01:04:16
