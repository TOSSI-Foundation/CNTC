#!/usr/bin/env bash
# deploy_open5gs.sh — deploy an Open5GS 5G SA core from scratch on a fresh Ubuntu host.
#
# Uses herlesupreeth/docker_open5gs (Docker-Compose 5G SA core + UERANSIM). Handles the
# Open5GS-specific prerequisites: Docker Compose v2 and the gtp5g kernel module (the 5G
# UPF data plane needs it). Idempotent — safe to re-run. See docs/open5gs-deployment.md.
#
# Usage:   sudo-capable user:  ./scripts/deploy_open5gs.sh
# Override defaults via env, e.g.:  HOST_IP=10.0.0.5 UE_POOL=10.45.0.0/16 ./deploy_open5gs.sh
set -euo pipefail

# ---- tunables ---------------------------------------------------------------
REPO_DIR=${REPO_DIR:-$HOME/docker_open5gs}
GTP5G_DIR=${GTP5G_DIR:-$HOME/gtp5g}
HOST_IP=${HOST_IP:-$(hostname -I | awk '{print $1}')}
MCC=${MCC:-001}
MNC=${MNC:-01}
UE_POOL=${UE_POOL:-10.45.0.0/16}        # UE internet APN pool (avoid clashes with other cores)
UE_POOL_IMS=${UE_POOL_IMS:-10.46.0.0/16}
SUB_IMSI=${SUB_IMSI:-001011234567895}   # must match .env UE1_IMSI
SUB_K=${SUB_K:-8baf473f2f8fd09487cccbd7097c6862}
SUB_OPC=${SUB_OPC:-11111111111111111111111111111111}
COMPOSE_V2_VER=${COMPOSE_V2_VER:-v2.29.7}

say(){ echo -e "\n=== $* ==="; }

say "1/8 prerequisites (docker, build tools, kernel headers)"
command -v docker >/dev/null || { echo "ERROR: docker not installed"; exit 1; }
for p in git make gcc; do command -v $p >/dev/null || { echo "ERROR: $p missing"; exit 1; }; done
if [ ! -d "/lib/modules/$(uname -r)/build" ]; then
  echo "installing kernel headers..."; sudo apt-get update -qq && sudo apt-get install -y -qq "linux-headers-$(uname -r)"
fi

say "2/8 Docker Compose v2 plugin"
if ! docker compose version >/dev/null 2>&1; then
  sudo mkdir -p /usr/local/lib/docker/cli-plugins
  sudo curl -fsSL "https://github.com/docker/compose/releases/download/${COMPOSE_V2_VER}/docker-compose-linux-x86_64" \
       -o /usr/local/lib/docker/cli-plugins/docker-compose
  sudo chmod +x /usr/local/lib/docker/cli-plugins/docker-compose
fi
docker compose version | head -1

say "3/8 gtp5g kernel module (Open5GS 5G UPF data plane)"
if ! lsmod | grep -q gtp5g; then
  [ -d "$GTP5G_DIR" ] || git clone --depth 1 https://github.com/free5gc/gtp5g.git "$GTP5G_DIR"
  ( cd "$GTP5G_DIR" && make && sudo make install )   # 'make install' also modprobes + persists
  sudo modprobe gtp5g || true
fi
lsmod | grep gtp5g && echo "gtp5g loaded" || { echo "ERROR: gtp5g failed to load"; exit 1; }

say "4/8 clone docker_open5gs"
[ -d "$REPO_DIR" ] || git clone --depth 1 https://github.com/herlesupreeth/docker_open5gs.git "$REPO_DIR"
cd "$REPO_DIR"

say "5/8 configure .env (host IP, PLMN, UE pools)"
sed -i "s#^DOCKER_HOST_IP=.*#DOCKER_HOST_IP=${HOST_IP}#" .env
sed -i "s#^MCC=.*#MCC=${MCC}#" .env
sed -i "s#^MNC=.*#MNC=${MNC}#" .env
sed -i "s#^UE_IPV4_INTERNET=.*#UE_IPV4_INTERNET=${UE_POOL}#" .env
sed -i "s#^UE_IPV4_IMS=.*#UE_IPV4_IMS=${UE_POOL_IMS}#" .env
grep -E "^DOCKER_HOST_IP|^MCC|^MNC|^UE_IPV4_INTERNET" .env

say "6/8 obtain base image (pull prebuilt from GHCR)"
if ! docker image inspect docker_open5gs >/dev/null 2>&1; then
  sudo docker pull ghcr.io/herlesupreeth/docker_open5gs:master
  sudo docker tag ghcr.io/herlesupreeth/docker_open5gs:master docker_open5gs
fi

say "7/8 bring up the 5G SA core"
sudo docker compose -f sa-deploy.yaml up -d
echo "waiting for UPF<->SMF N4 association..."
for i in $(seq 1 30); do
  sudo docker logs smf 2>&1 | grep -q "PFCP associated" && break; sleep 3
done

say "8/8 provision subscriber + verify"
MONGO_IP=$(grep -E "^MONGO_IP=" .env | cut -d= -f2)
sudo docker exec -e DB_URI="mongodb://${MONGO_IP}/open5gs" webui \
  /open5gs/misc/db/open5gs-dbctl add "$SUB_IMSI" "$SUB_K" "$SUB_OPC" 2>&1 | tail -1 || true
echo "--- N4 association (SMF) ---"; sudo docker logs smf 2>&1 | grep -i "PFCP associated" | tail -1
echo "--- UPF GTP-U/PFCP ---";       sudo docker logs upf 2>&1 | grep -iE "pfcp_server|gtp_server|PFCP associated" | tail -2
echo "--- subscriber ---"; sudo docker exec mongo mongosh open5gs --quiet \
  --eval 'db.subscribers.find({},{imsi:1,_id:0}).toArray()' 2>/dev/null || true

cat <<EOF

Open5GS 5G SA core is up.
  WebUI:     http://${HOST_IP}:9999   (admin / 1423)
  PLMN:      ${MCC}/${MNC}   DNN: internet   SST: 1
  UPF (N4):  $(grep -E '^UPF_IP=' .env | cut -d= -f2):8805   (N3 GTP-U :2152)
  UE pool:   ${UE_POOL}
  Subscriber: ${SUB_IMSI}  (K=${SUB_K}, OPc=${SUB_OPC})
Down:  cd ${REPO_DIR} && sudo docker compose -f sa-deploy.yaml down
EOF
