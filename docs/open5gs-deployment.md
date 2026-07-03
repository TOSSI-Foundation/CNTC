# Open5GS 5G SA core — deployment from scratch

How the Open5GS 5G core (the third UPF target for upfbench) was deployed on a fresh
Ubuntu host, and how to reproduce it. For a one-shot deploy, run
[`scripts/deploy_open5gs.sh`](../scripts/deploy_open5gs.sh); the sections below explain
exactly what that script does and why.

## What we deploy and why

We use **[herlesupreeth/docker_open5gs](https://github.com/herlesupreeth/docker_open5gs)** —
the most actively maintained Docker-Compose packaging of Open5GS 5G SA, which also bundles
UERANSIM (gNB + UE). It runs every network function as a container on one Docker bridge
(`172.22.0.0/24`), with a WebUI for subscriber management. This matches how we run OAI
(containers on a bridge) and keeps the UPF reachable for benchmarking.

## Prerequisites

- **OS/host:** Ubuntu 22.04, Docker installed, `git` / `make` / `gcc`, and the matching
  `linux-headers-$(uname -r)` (needed to build the kernel module below).
- **Docker Compose v2** (≥ 2.14). The repo's compose files use v2 syntax. If only the old
  v1 `docker-compose` is present, install the v2 plugin binary:
  ```bash
  sudo mkdir -p /usr/local/lib/docker/cli-plugins
  sudo curl -fsSL https://github.com/docker/compose/releases/download/v2.29.7/docker-compose-linux-x86_64 \
       -o /usr/local/lib/docker/cli-plugins/docker-compose
  sudo chmod +x /usr/local/lib/docker/cli-plugins/docker-compose
  docker compose version
  ```
- **`gtp5g` kernel module** — THE key Open5GS-specific requirement. Open5GS's 5G UPF
  (`open5gs-upfd`) builds its GTP-U data path on the out-of-tree `gtp5g` module, which must
  be compiled against the host kernel and loaded (containers share the host kernel):
  ```bash
  git clone --depth 1 https://github.com/free5gc/gtp5g.git ~/gtp5g
  cd ~/gtp5g && make && sudo make install      # also modprobes + persists via /etc/modules-load.d
  lsmod | grep gtp5g                            # confirm loaded (we used v0.10.2)
  ```
  Without `gtp5g`, the UPF starts but cannot forward user-plane traffic.

## Deploy steps

1. **Clone the repo:**
   ```bash
   git clone --depth 1 https://github.com/herlesupreeth/docker_open5gs.git ~/docker_open5gs
   cd ~/docker_open5gs
   ```
2. **Configure `.env`** (the single source of truth for IPs/PLMN/pools). Key edits:
   | Variable | Set to | Why |
   |---|---|---|
   | `DOCKER_HOST_IP` | the host's IP (e.g. `192.168.6.90`) | WebUI binding + advertised endpoints |
   | `MCC` / `MNC` | `001` / `01` | PLMN; the bundled UERANSIM configs match this |
   | `UE_IPV4_INTERNET` | `10.45.0.0/16` | UE address pool. **Changed from the repo default `192.168.100.0/24`, which clashes with the SD-Core UE pool on this host.** |
   | `UE_IPV4_IMS` | `10.46.0.0/16` | IMS APN pool (kept off 192.168.x for cleanliness) |

   The Docker network (`TEST_NETWORK=172.22.0.0/24`) does not clash with our other
   deployments (demo-oai `192.168.70.0/26`, SD-Core `192.168.250/252.x`).
3. **Get the base image** (prebuilt from GHCR — faster than building from source):
   ```bash
   sudo docker pull ghcr.io/herlesupreeth/docker_open5gs:master
   sudo docker tag  ghcr.io/herlesupreeth/docker_open5gs:master docker_open5gs
   ```
4. **Bring up the 5G SA core:**
   ```bash
   sudo docker compose -f sa-deploy.yaml up -d
   ```
   This starts mongo, nrf, scp, ausf, udm, udr, nssf, pcf, bsf, amf, smf, upf, webui
   (+ grafana/metrics).
5. **Provision a subscriber** (IMSI/K/OPc from `.env` UE1_*). Note the bundled UE config
   labels the operator code as **OP**; we store the same 128-bit value as **OPc** and use
   `opType: OPC` on the UE side so both ends agree:
   ```bash
   sudo docker exec -e DB_URI=mongodb://172.22.0.2/open5gs webui \
     /open5gs/misc/db/open5gs-dbctl add 001011234567895 \
     8baf473f2f8fd09487cccbd7097c6862 11111111111111111111111111111111
   ```
   (or via WebUI `http://<host>:9999`, admin / 1423). Other helpers:
   `open5gs-dbctl add_ue_with_slice <imsi> <k> <opc> <apn> <sst> <sd>`.

## Verify it's healthy

```bash
sudo docker logs smf 2>&1 | grep "PFCP associated"   # SMF<->UPF N4 association up
sudo docker logs upf 2>&1 | grep -E "pfcp_server|gtp_server"   # UPF N4 8805 + N3 2152 listening
sudo docker logs amf 2>&1 | grep -i "NF registered"  # NFs registered with NRF
sudo docker exec mongo mongosh open5gs --quiet --eval 'db.subscribers.countDocuments()'
```
Healthy looks like: SMF and UPF both log `PFCP associated`, UPF logs `pfcp_server() [172.22.0.8]:8805` and `gtp_server() [172.22.0.8]:2152`, AMF shows NF registrations.

## Key facts (for UERANSIM / upfbench)

| Item | Value |
|---|---|
| PLMN / TAC | 001 / 01, TAC 1 |
| DNN / slice | `internet` / SST 1 |
| UPF N4 (PFCP) | `172.22.0.8:8805` |
| UPF N3 (GTP-U) | `172.22.0.8:2152` |
| AMF N2 (NGAP) | AMF container `38412/SCTP` |
| UE pool | `10.45.0.0/16` |
| WebUI | `http://<host>:9999` (admin / 1423) |
| Subscriber | `001011234567895` / K `8baf473f2f8fd09487cccbd7097c6862` / OPc `11111111111111111111111111111111` |

## Manage

```bash
cd ~/docker_open5gs
sudo docker compose -f sa-deploy.yaml ps        # status
sudo docker compose -f sa-deploy.yaml logs -f upf
sudo docker compose -f sa-deploy.yaml down       # stop (add -v to wipe volumes/subscribers)
```

## Notes / gotchas

- **`gtp5g` must stay loaded** across reboots — `make install` writes `/etc/modules-load.d/gtp5g.conf`, so it auto-loads. After a kernel upgrade, rebuild it against the new headers.
- The bundled UERANSIM gNB/UE (`nr-gnb.yaml` / `nr-ue.yaml`) run as containers on the same
  bridge and are templated from `.env`, so they line up with the provisioned subscriber.
- For driving the UPF directly from upfbench (pfcpsim over N4 + tcpreplay over N3), the UPF
  sits at `172.22.0.8` on the `172.22.0.0/24` bridge — reachable from the host, same idea as
  the OAI standalone path.
