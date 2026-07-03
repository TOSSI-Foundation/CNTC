# Config reference — what to change on a new server

This page tells you **exactly which fields are environment-specific** (change them on every
new server / UPF deployment) and which are portable test knobs (leave them, or only tune
rates). If you're moving upfbench to a fresh box and a UPF won't respond, the answer is
almost always one of the fields below.

## First: bring the box up
```bash
./scripts/bootstrap_fresh_vm.sh     # one-time: system deps + builds pfcpsim
upfbench doctor                     # preflight — tells you what's still missing
upfbench run --config <config> --suite all
```

## How configs are structured
Every UPF config is **one YAML file** with two parts:
- **Connection & topology** (top) — how to reach the UPF and where to inject traffic.
  **🔴 This is what changes per server.**
- **Test knobs** (bottom: `performance:` / `load:` / `pfcp:` / `n3neg:`) — frame sizes,
  rates, durations. **🟢 Portable** — you only *tune* `max_rate_mpps` to the UPF's speed.

**One file per UPF — with a single exception:** the SD-Core **DPDK/AF_XDP/CNDP** config
references a *second* file. `sdcore-bess-trex.yaml` has a line
`trex_cfg: "configs/trex_cfg.yaml"`, which is TRex's own hardware-pinning file. af_packet
and OAI are single self-contained files.

| Mode / UPF | Config file(s) | Generator |
|---|---|---|
| SD-Core **af_packet** | `sdcore-bess.yaml` | tcpreplay (host kernel socket) |
| SD-Core **DPDK / AF_XDP / CNDP** | `sdcore-bess-trex.yaml` **+** `trex_cfg.yaml` | TRex (NIC VF hairpin) |
| **OAI-UPF** | `oai-upf-local.yaml` | tcpreplay (docker bridge) |

> **Golden rule:** never copy our IPs / MACs / PCI addresses — every one is per-deployment.
> The adapter reads the UPF's *mode* and *image* live, but **the addressing you supply.**

---

## A) SD-Core af_packet — `configs/sdcore-bess.yaml` (1 file, simplest)
af_packet injects into the pod's access macvlan as a host kernel socket — **no MACs, no VFs,
no second file** (the UPF's MAC is read from the pod automatically). Change only:

| Field | Set to | Read it live |
|---|---|---|
| `namespace` / `pod` | k8s location (usually `aether-5gc` / `upf-0`) | `kubectl get pods -A \| grep upf` |
| `n3_remote_ip` | the UPF's N3 / access IP (outer GTP-U dst) | the access subnet / `upf.jsonc` |
| `gen_iface` | the host send interface (the access macvlan) | `ip -br addr` |
| `pfcpsim_iface` *(load/pfcp only)* | node interface the UPF can reply to | the node InternalIP iface |
| `ue_pool` *(load/pfcp only)* | UE IP subnet | `upf.jsonc` `cpiface.ue_ip_pool` |

→ Often just **3 fields**. `n4_addr` is a fallback — pfcpsim auto-resolves the `upf` k8s service.

---

## B) SD-Core DPDK / AF_XDP / CNDP — `sdcore-bess-trex.yaml` + `trex_cfg.yaml` (2 files, most to change)
Kernel-bypass injects via a spare NIC VF hairpinned through the on-chip switch (VEB), so you
need MACs **and** PCI addresses.

**File 1 — `configs/sdcore-bess-trex.yaml`:**
| Field | Set to | Read it live |
|---|---|---|
| `namespace` / `pod` / `bessd_container` | k8s location | `kubectl get pods -A \| grep upf` |
| `n3_remote_mac` / `n6_remote_mac` | UPF **access / core VF MACs** (VEB targets) | `ip link show <VF>` / the SR-IOV NAD |
| `trex_src_mac` / `trex_dl_src_mac` | the **generator VF MACs** | `ip link show <genVF>` |
| `trex_tx_port` / `trex_dl_port` | TRex port indices | match the order in `trex_cfg.yaml` |
| `n3_remote_ip` / `n3_addr` | UPF N3 IP | access subnet |
| `gnb_ip` / `gnb_addr` | emulated gNB IP (free addr on the subnet) | pick one |
| `ue_pool` | UE subnet | `upf.jsonc` |
| `pfcpsim_iface` | node interface the UPF can reply to | node InternalIP iface |
| `trex_root` | TRex install path | your path |

**File 2 — `configs/trex_cfg.yaml`:** TRex's hardware pinning (all per-server):
- `interfaces:` — the **generator VF PCI addresses** (e.g. `0000:18:0a.2`, `0a.3`). Find a
  free VF on the **same PF** as the UPF access/core VFs: `dpdk-devbind.py --status` / `lspci`.
- `port_info[].dest_mac` — must equal the UPF VF MACs (`n3_remote_mac` / `n6_remote_mac`).
- `platform.dual_if.threads` — CPU cores for TRex, **off** the UPF's bessd worker cores.

> The downlink VF (`0a.3` / `trex_dl_port: 1`) is only used by TC-02 bidirectional. If you
> don't have a second free VF on the core PF, TC-02 will skip; everything else still runs.

---

## C) OAI-UPF — `configs/oai-upf-local.yaml` (1 file) — read these LIVE
OAI runs as a Docker container; **read every address from the live container**, don't copy.

| Field | Set to | Read it live |
|---|---|---|
| `container` | docker container name | `docker ps \| grep upf` |
| `n4_addr` / `pfcp_remote_addr` / `n3_remote_ip` | UPF **container IP**(:8805) | `docker inspect -f '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}' oai-upf` |
| `pfcpsim_iface` / `gen_iface` | host **bridge** iface for the docker net | `docker network ls \| grep oai` → `ip -br addr` |
| `ue_pool` | OAI's **DNN / SGi subnet** | `docker exec oai-upf ip -br addr` → look at `tun0` |
| `gnb_addr` / `gnb_ip` | a free IP **on the bridge subnet** | pick one |
| `gen_cpu_affinity` | keep the sender off the UPF's cores | per host CPU layout |

**Leave alone (OAI quirks — already correct, do not touch):**
`pfcpsim_no_urr: true` (OAI rejects URR IEs), `pfcp_no_assoc_release: true`,
`n3_mac_via: arp`, `docker_cmd`. The adapter also auto-sets `fwd_field = rx_pkts`
(OAI's N6 is `tun0`, a TUN device — decapped uplink shows as `tun0` rx).

**Deploy + isolate first:** `cd ~/oai-cn5g && sudo docker compose up -d`, then
`sudo docker stop oai-smf` so pfcpsim is the sole N4 peer. Always run with
`--reset-between-suites` (the reset hook `docker restart oai-upf` gives each suite a fresh
session table; OAI wedges otherwise).

---

## Test knobs (the portable bottom half — tune, don't relocate)
These travel between servers; you only adjust them to the UPF's speed:
- `max_rate_mpps` — the NDR/PDR search ceiling. SD-Core DPDK ~15; OAI simpleswitch ~0.12.
- `search_resolution_mpps`, `search_max_iters`, `trial_duration_s` — search precision/length.
- `frame_sizes`, `tc02_*`, `burst_*`, `multiflow_*` — per-test parameters.
- `ue_counts` (load) — cap to the UE pool size (e.g. a /24 → ≤250).
- `pfcpsim_mbr_kbps` — set very high to make the QER effectively unlimited (raw ceiling).

## Quick sanity if a UPF won't forward
1. `upfbench doctor` — deps / TRex / hugepages / VFs / kubectl all green?
2. Did you read the **live** UPF IP/MAC/bridge (not copy ours)?
3. Does `ue_pool` match the UPF's actual UE subnet? (uplink won't match a PDR otherwise)
4. OAI only: is `oai-smf` stopped, and are you using `--reset-between-suites`?
