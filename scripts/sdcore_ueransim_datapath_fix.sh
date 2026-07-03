#!/usr/bin/env bash
# sdcore_ueransim_datapath_fix.sh
#
# Restore the SD-Core BESS-UPF DOWNLINK data path so an EXTERNAL UERANSIM gNB+UE
# gets working end-to-end internet. The 5G control plane (register + PDU session)
# already works out of the box; only the downlink (internet -> UE) needs these fixes.
#
# WHY: aether's BESS-UPF uses a DPDK fast path + macvlan interfaces and is wired for
# its own in-cluster gnbsim. An external UERANSIM gNB (running on the host) needs the
# datapath taught about it. Three things break — and they break AGAIN every time the
# upf-0 pod is recreated, because the pod/macvlan MACs are regenerated:
#   1. BESS has no route to the external gNB's N3 IP        -> add a /32 route to accessRoutes
#   2. BESS's static downlink next-hop MAC goes stale       -> align the host access MAC to it
#   3. The host's ARP for the UPF N6 (core) goes stale      -> refresh the neighbour entry
# Plus: host NAT so the UE pool reaches the internet, and per-netns DNS for speedtest.
#
# Symptom when broken: control plane fine, uplink works, but downlink = 100% loss and
# BESS accessFast TX stays 0 (packets die at accessRoutes -> accessbad_route Sink).
#
# RE-RUN this after every `kubectl delete pod upf-0` (or host iface recreate), AFTER
# the UERANSIM gNB+UE are attached. Idempotent.
set -euo pipefail

# ---- config (adjust to your deployment) -------------------------------------
NS_K8S=${NS_K8S:-aether-5gc}
UPF_POD=${UPF_POD:-upf-0}
ACCESS_IF=${ACCESS_IF:-access}        # host iface on the N3/access subnet (gNB N3 lives here)
CORE_IF=${CORE_IF:-core}              # host iface on the N6/core subnet (the DN gateway)
UPLINK_IF=${UPLINK_IF:-eth0}          # host's real internet uplink (for NAT)
GNB_N3_IP=${GNB_N3_IP:-192.168.252.1} # the gNB's gtpIp (== host access iface IP)
UPF_CORE_IP=${UPF_CORE_IP:-192.168.250.3}   # UPF N6 IP (BESS coreFast)
UE_POOL=${UE_POOL:-192.168.100.0/24}        # SD-Core UE IP pool (DNN "internet")
KC="kubectl -n $NS_K8S exec $UPF_POD -c bessd --"

echo "[1/5] discover BESS static downlink next-hop MAC (accessDstMAC<MAC> module)"
BESS_MAC_HEX=$($KC bessctl show module 2>/dev/null | grep -oE 'accessDstMAC[0-9A-Fa-f]{12}' | head -1 | sed 's/accessDstMAC//')
[ -n "$BESS_MAC_HEX" ] || { echo "ERROR: could not find accessDstMAC module"; exit 1; }
BESS_MAC=$(echo "$BESS_MAC_HEX" | sed 's/../&:/g; s/:$//')
echo "      BESS sends downlink to MAC: $BESS_MAC"

echo "[2/5] align host $ACCESS_IF MAC to BESS's next-hop so downlink frames are delivered"
sudo ip link set "$ACCESS_IF" address "$BESS_MAC"

echo "[3/5] add /32 route for the external gNB ($GNB_N3_IP) into BESS accessRoutes (gate 0)"
kubectl -n "$NS_K8S" exec -i "$UPF_POD" -c bessd -- python3 - "$GNB_N3_IP" <<'PY'
import sys; sys.path.insert(0, '/opt/bess')
from pybess.bess import BESS
ip = sys.argv[1]
b = BESS(); b.connect(grpc_url='localhost:10514')
b.pause_all()
try:
    b.run_module_command("accessRoutes", "add", "IPLookupCommandAddArg",
                         {"prefix": ip, "prefix_len": 32, "gate": 0})
finally:
    b.resume_all()
print("      route added: %s/32 -> gate 0 (accessDstMAC -> accessMerge -> accessFast TX)" % ip)
PY

echo "[4/5] refresh host ARP for UPF N6 ($UPF_CORE_IP) to the CURRENT pod MAC"
UPF_CORE_MAC=$($KC bessctl show port 2>/dev/null | grep -A1 coreFast | grep -oE 'HWaddr [0-9a-f:]+' | awk '{print $2}')
[ -n "$UPF_CORE_MAC" ] || { echo "ERROR: could not read coreFast MAC"; exit 1; }
sudo ip neigh replace "$UPF_CORE_IP" lladdr "$UPF_CORE_MAC" dev "$CORE_IF"
echo "      $UPF_CORE_IP -> $UPF_CORE_MAC ($CORE_IF)"

echo "[5/5] host NAT + forwarding for the UE pool -> internet, and per-netns DNS"
sudo sysctl -w net.ipv4.ip_forward=1 >/dev/null
sudo iptables -t nat -C POSTROUTING -s "$UE_POOL" -o "$UPLINK_IF" -j MASQUERADE 2>/dev/null \
  || sudo iptables -t nat -A POSTROUTING -s "$UE_POOL" -o "$UPLINK_IF" -j MASQUERADE
for NS in $(ip netns list 2>/dev/null | grep -oE 'uesimtun[^ ]*'); do
  sudo mkdir -p "/etc/netns/$NS"
  echo "nameserver 8.8.8.8" | sudo tee "/etc/netns/$NS/resolv.conf" >/dev/null
done

echo
echo "DONE. Verify:  sudo ip netns exec \$(ip netns list | grep -oE 'uesimtun[^ ]*' | head -1) ping -c3 8.8.8.8"
echo "Note: re-run after any 'kubectl delete pod $UPF_POD' — pod MACs change and items 1,3,4 go stale."
