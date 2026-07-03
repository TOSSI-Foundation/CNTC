# Testing SD-Core in DPDK mode with upfbench

How to benchmark an SD-Core BESS-UPF deployed in **DPDK mode** (and, by the same path,
**AF_XDP** and **CNDP**) using the framework's TRex generator. This is the host-based
workflow, validated on server `three` (Intel XXV710 / i40e, 25 GbE).

> **Why this is different from af_packet.** In `af_packet` mode the UPF's N3 access port
> is a kernel socket, so the framework injects with `tcpreplay` from the host. In
> **DPDK/AF_XDP/CNDP** the access port is a **DPDK/XDP-owned VF inside the pod** — there is
> *no host kernel socket to inject into*, and the UPF forwards millions of pps (far above
> tcpreplay's ~0.06 Mpps ceiling). So we drive it with **TRex on a spare SR-IOV VF**, whose
> frames hairpin through the NIC's on-chip switch (VEB) into the UPF's access VF. The
> framework refuses `tcpreplay` against a DPDK UPF on purpose (it would measure the
> generator, not the UPF).

---

## 1. How it works (the picture)

```
   TRex (gen VF, e.g. 18:0a.2)
        │  GTP-U frames, dst MAC = UPF access VF MAC
        ▼
   NIC on-chip switch (VEB)  ── hairpins within the same PF ──►  UPF access VF (18:0a.0)
                                                                      │
                                            BESS pipeline (PDR/QER/FAR, short-circuit)
                                                                      ▼
                                                                 UPF core VF (N6 TX)
```
- TRex sends real **GTP-U** on N distinct outer 5-tuples (multi-flow) so the NIC **RSS**
  spreads them across the UPF's worker RX queues.
- The framework's `pybess` control installs a forward-all rule + a **short-circuit**
  (`executeFAR → coreQSplit`, with an egress sink-MAC) so synthetic traffic forwards to
  the core-TX counter without dying in route lookup or looping back through the VEB.
- Throughput/latency are read from the UPF's own BESS port counters (black-box).

---

## 2. Prerequisites (one-time, on the server)

1. **Host / BIOS**: SR-IOV enabled on the NIC; IOMMU on (kernel cmdline
   `intel_iommu=on iommu=pt`).
2. **Hugepages** (1 GiB pages): enough for **UPF (2 GiB) + TRex (capped at 2 GiB) +
   headroom**. e.g. 16–40 × 1 GiB. Check: `grep HugePages /proc/meminfo`.
3. **SR-IOV VFs on the access PF**: the UPF uses 2 (access + core). You need **≥1 free VF
   on the *same* PF**, bound to `vfio-pci`, for the generator. (The VEB only hairpins
   *within a PF*, so the gen VF must be on the access PF.)
4. **TRex** installed (e.g. `/home/<user>/trex-v3.08`) with its Python automation API.
5. **Framework**: repo cloned, then `./scripts/bootstrap_fresh_vm.sh` (system packages +
   `pip install -e '.[dashboard]'` + builds pfcpsim). `kubectl` reachable
   (`export KUBECONFIG=$HOME/.kube/config`).

> **One-shot readiness check:** `python3 -m upfbench.cli doctor` verifies all of the above
> (deps, pfcpsim, TRex, kubectl, hugepages, free VFs, IOMMU) and prints exactly what's still
> missing. Launch the web dashboard any time with `python3 -m upfbench.cli dashboard`.

---

## 3. Deploy SD-Core in DPDK mode

Via aether-onramp, in `vars/main.yml`:
```yaml
core:
  values_file: "...sdcore-5g-sriov-values.yaml"   # SR-IOV values
  helm:
    local_charts: true
    chart_ref: <path to patched sdcore-helm-charts>
  upf:
    mode: dpdk
```
Then `make aether-5gc-install`. Confirm:
```bash
kubectl get pod upf-0 -n aether-5gc          # 5/5 Running
kubectl exec upf-0 -n aether-5gc -c bessd -- bessctl show port | grep -i driver
#   accessFast / coreFast should show Driver PMDPort  (= DPDK)
```

---

## 4. Map the topology (find the VFs and MAC)

You need three things: the UPF **access VF**, its **MAC** (the generator's target), and a
**free gen VF on the same PF**.

```bash
export KUBECONFIG=$HOME/.kube/config; export PATH=$PATH:/var/lib/rancher/rke2/bin

# (a) Which VFs the UPF uses (access + core PCI addresses):
kubectl get pod upf-0 -n aether-5gc \
  -o jsonpath='{.metadata.annotations.k8s\.v1\.cni\.cncf\.io/network-status}'; echo
#   -> access-net pci-address = the UPF access VF (e.g. 0000:18:0a.0)

# (b) The access VF's MAC and its PF, and free VFs on that PF:
#   find the PF that owns the access VF:
basename $(readlink -f /sys/bus/pci/devices/0000:18:0a.0/physfn)   # e.g. 0000:18:00.1
ip link show enp24s0f1 | grep -A0 "vf "    # lists vf MACs; vf0=access ...:33, vf1=core ...:34

# (c) Pick a FREE vfio-pci VF on the same PF for the generator (e.g. 0000:18:0a.2):
for vf in 0000:18:0a.2 0000:18:0a.3; do
  echo "$vf driver=$(basename $(readlink -f /sys/bus/pci/devices/$vf/driver))"
done   # want driver=vfio-pci and not used by the UPF
```
Record: **UPF access VF MAC** (e.g. `00:11:22:33:44:33`) and the **gen VF PCI** (e.g.
`0000:18:0a.2`). For bidirectional later, also pick a core-side gen VF.

---

## 5. Configure the framework (two files)

### `configs/trex_cfg.yaml` — pins TRex to your gen VF(s) only
```yaml
- version: 2
  interfaces: ['0000:18:0a.2', '0000:18:0a.3']   # YOUR free gen VFs
  port_info:
    - dest_mac: '00:11:22:33:44:33'   # UPF access VF MAC (uplink target)
      src_mac:  '00:11:22:33:44:35'   # gen VF's own MAC
    - dest_mac: '00:11:22:33:44:34'   # UPF core VF MAC (for downlink/bidir)
      src_mac:  '00:11:22:33:44:36'
  limit_memory: 2048                  # cap TRex to 2 GiB so it doesn't starve the UPF
  platform:
    master_thread_id: 2
    latency_thread_id: 3
    dual_if:
      - socket: 0
        threads: [4, 5, 6, 7]         # TRex cores — MUST be OFF the UPF worker cores
```
> Pinning to explicit interfaces keeps TRex's DPDK EAL from probing other vfio devices.
> `limit_memory` + the framework's auto-cleanup of leaked `rtemap_*` files prevent TRex
> from starving the UPF's hugepages.

### `configs/sdcore-bess-trex.yaml` — the campaign
```yaml
campaign: UPF-BM-SDCORE-DPDK
reset_between_suites: true            # fresh bessd per suite (clears WildcardMatch tuples)

sut: { cpu: "...", nic: "Intel XXV710 25GbE (i40e)", platform: "x86_64, RKE2" }

upf:
  adapter: sdcore_bess
  mode: dpdk                          # informational; the adapter reads the LIVE mode
  n3_iface: access
  n6_iface: core
  namespace: aether-5gc
  pod: upf-0
  bessd_container: bessd
  # --- generator wiring ---
  trex_root: "/home/<user>/trex-v3.08"
  trex_cfg: "configs/trex_cfg.yaml"
  trex_tx_port: 0
  n3_remote_mac: "00:11:22:33:44:33"  # UPF access VF MAC (must match trex_cfg dest_mac[0])
  rss_flows: 64                       # multi-flow: # of distinct outer-src IPs -> RSS spread

suite: performance
performance:
  control: pybess                     # white-box forward-all + short-circuit
  generator: trex                     # DPDK injection
  frame_sizes: [128, 256, 512, 1024, 1518]   # GTP-U min ~82B, so 64 is omitted
  trial_duration_s: 8
  max_rate_mpps: 15.0                 # search ceiling — raise if the UPF doesn't saturate
  pdr_tolerance: 0.001
  search_resolution_mpps: 0.1
  search_max_iters: 7
```

---

## 6. The two knobs you change, and WHERE

| To change | Where | Pod restart? |
|---|---|---|
| **Worker count** | SD-Core `upf` configmap → `upf.jsonc "workers"` (UPF-side, *not* the framework) | **Yes** |
| **Multi-flow / RSS spread** | `sdcore-bess-trex.yaml` → `rss_flows` (framework) | No |
| Gen VF / target MAC / TRex path | `sdcore-bess-trex.yaml` + `trex_cfg.yaml` | No |
| Frame sizes / rates / duration | `sdcore-bess-trex.yaml` `performance:` block | No |

### Worker count (UPF-side)
The framework *reads* the live worker count but does not set it. To run N workers:
```bash
# set workers=N in the upf configmap, then restart the pod
kubectl get cm upf -n aether-5gc -o json \
 | python3 -c "import json,sys,re; cm=json.load(sys.stdin); \
   cm['data']['upf.jsonc']=re.sub(r'\"workers\":\s*[0-9]+','\"workers\":4',cm['data']['upf.jsonc']); \
   [cm['metadata'].pop(k,None) for k in ('resourceVersion','uid','creationTimestamp')]; \
   print(json.dumps(cm))" | kubectl apply -f -
kubectl delete pod upf-0 -n aether-5gc
kubectl wait --for=condition=ready pod/upf-0 -n aether-5gc --timeout=150s
kubectl exec upf-0 -n aether-5gc -c bessd -- bessctl show worker | grep -c RUNNING   # == N
```
**Also set the pod CPU limit ≥ worker count** (`resources.limits.cpu`). If `limits.cpu <
workers`, Kubernetes CFS throttles the busy-poll workers to ~49% and caps throughput
(the classic "stuck at ~6 Mpps" trap).

### Multi-flow (framework-side)
`rss_flows` = how many distinct outer source IPs TRex emits. The NIC's RSS hashes those
across the worker RX queues. **Set `rss_flows ≥ worker count`** (64 covers up to ~16
workers). With a **single flow**, all traffic hashes to one queue → only **one** worker
runs (this is the per-core number, ~2.9 Mpps on i40e); multi-flow is what engages all N.

---

## 7. Run

```bash
cd <repo>
export KUBECONFIG=$HOME/.kube/config
export PATH=$PATH:/var/lib/rancher/rke2/bin
sudo -E python3 -m upfbench.cli run \
     --config configs/sdcore-bess-trex.yaml \
     --suite performance \
     --campaign UPF-BM-SDCORE-DPDK
```
- The TRex server auto-starts (capped at 2 GiB, leaked hugepage files cleaned first).
- The `reset_between_suites` hook gives bessd a clean tuple table.
- Output → `campaigns/UPF-BM-SDCORE-DPDK/{results.json, report-performance.pdf}`.

### Worker-scaling sweep (1w → 2w → 4w, like the reference)
Repeat: **set `workers:N` (§6 → restart) → run with a per-N campaign id**:
```
workers:1 → --campaign DPDK-1W
workers:2 → --campaign DPDK-2W
workers:4 → --campaign DPDK-4W
```
Compare the NDR across the three campaign reports. (`rss_flows: 64` stays fixed.)

### Same config for AF_XDP / CNDP
This *same* TRex path works for **af_xdp** and **cndp** too — just redeploy the UPF in
that mode (the adapter auto-detects and stamps the live mode in the report). Use distinct
campaign ids (`--campaign UPF-BM-SDCORE-AFXDP`, etc.).

---

## 8. Adding the Load & PFCP suites (pfcpsim)

Suites 2 (load) and 3 (PFCP conformance) drive the UPF over **N4 (PFCP)** using the
vendored `omec-project/pfcpsim`. They work on a DPDK UPF exactly as on any mode (PFCP is
dataplane-independent). One-time build + a few config knobs.

### 8.1 Build pfcpsim (one-time)
Needs **Go ≥ 1.25** (the vendored pfcpsim is `go 1.25.0`):
```bash
mkdir -p ~/go-sdk
curl -sL https://go.dev/dl/go1.25.0.linux-amd64.tar.gz | tar -C ~/go-sdk -xz
export PATH=$HOME/go-sdk/go/bin:$PATH
cd <repo>/third_party/pfcpsim
CGO_ENABLED=0 go build -o pfcpsim ./cmd/pfcpsim
CGO_ENABLED=0 go build -o pfcpctl ./cmd/pfcpctl
```
(The binaries are git-ignored — rebuild per machine.)

### 8.2 pfcpsim config knobs
Add to `configs/sdcore-bess-trex.yaml` under `upf:` (alongside the TRex knobs):
```yaml
  pfcpsim_iface: "eno1"          # the NODE interface the UPF pod can reply to (node IP)
  n3_addr: "192.168.252.3"       # UPF N3 IP advertised in F-TEIDs
  gnb_addr: "192.168.252.10"
  ue_pool: "10.250.0.0/24"       # UE pool for created sessions
  pfcpsim_mbr_kbps: 10000000     # ~10 Gbps: make the QER effectively unlimited (raw ceiling)
```
- **N4 address is auto-resolved** from the `upf` Kubernetes service ClusterIP — stable
  across pod restarts (no need to chase the pod IP). `n4_addr` is only a fallback.
- **`pfcpsim_iface`** must be the **node** interface (the pod must route PFCP responses
  back to it). Find it: `kubectl get node -o wide` → the interface holding the InternalIP.

### 8.3 Run them
```bash
# PFCP conformance (no traffic — fastest check that pfcpsim reaches the UPF over N4):
sudo -E python3 -m upfbench.cli run --config configs/sdcore-bess-trex.yaml --suite pfcp --campaign DPDK-PFCP
# Multi-UE load (pfcpsim installs N real sessions; TRex sends GTP-U on matching TEIDs):
sudo -E python3 -m upfbench.cli run --config configs/sdcore-bess-trex.yaml --suite load --campaign DPDK-LOAD
# All three suites, one combined report:
sudo -E python3 -m upfbench.cli run --config configs/sdcore-bess-trex.yaml --suite all  --campaign UPF-BM-SDCORE-DPDK
```
- **Load forwarding is automatic:** LT-02 applies the same BESS egress short-circuit on
  top of pfcpsim's per-UE FARs, so per-UE synthetic traffic reaches core TX on DPDK (no
  manual step). On non-BESS UPFs the short-circuit is a no-op.
- **Validated on DPDK** (i40e XXV710): PFCP CF-01..05 **5/5 pass** (incl. session modify);
  load LT-01 **5000 sessions**, LT-02 **~8 Mpps with 100% per-UE forwarding**, LT-03
  latency-under-load.

### 8.4 N3 data-plane negative / robustness suite (`n3neg`)

A black-box robustness suite that injects **malformed N3 GTP-U** at line rate and checks the
UPF (a) enforces TEID/PDR matching, (b) does **not crash**, and (c) keeps serving valid
traffic. It uses the same plumbing as load (pfcpsim installs one known session; TRex
hairpins the GTP-U through the NIC VEB), plus a **crash-detection + auto-recovery** layer.

Three tests:
- **NT-01 Unknown TEID** — GTP-U on a TEID with no PDR must be **dropped** (and a valid TEID
  on the same session must still forward). *Pass on DPDK.*
- **NT-02 Malformed GTP-U** — six variants (control/reserved message type, GTPv0 version,
  truncated header, zero-inner G-PDU, length-overflow), each its own burst with per-variant
  crash detection.
- **NT-03 PSC (0x85) ext-header** — a well-formed 5G PDU-Session-Container vs a **malformed**
  PSC ext-header.

**How crash detection works.** Packets are crafted in a clean subprocess with the *system*
scapy (TRex bundles an older scapy that can't build the PSC ext-header and can't coexist in
the TRex process — so no scapy object ever crosses into TRex; only raw bytes do). After each
burst the suite polls the bessd container's k8s `restartCount` (and liveness) for ~20 s — a
BESS worker segfault can lag the burst that triggers it — then, on a detected crash, **waits
for bessd to come back, re-installs the session + short-circuit, and continues**, attributing
the crash to the culprit packet. A crash ⇒ the test is **FAIL** (a single malformed N3 packet
that drops the user plane is a remote DoS).

```bash
# run it on its own (it deliberately tries to crash the UPF — NOT part of --suite all):
sudo -E python3 -m upfbench.cli run --config configs/sdcore-bess-trex.yaml --suite n3neg --campaign DPDK-N3NEG
```
Knobs (config `n3neg:` block): `burst_pkts` (per-variant burst, default 20000),
`frame_size` (default 256).

**Finding on SD-Core BESS-UPF (omec).** NT-01 passes (TEID/PDR enforced). NT-02/NT-03
reproducibly **crash `bessd` with a SIGSEGV in `GtpuDecap::ProcessBatch`** — a malformed PSC
ext-header and the truncation/length-overflow GTP-U variants make the fixed-offset decap read
past the buffer (null-deref). The data plane drops for ~1 min until k8s restarts the
container; the suite detects this, recovers, and reports it as FAIL. This is a genuine
remote-DoS-class robustness defect, not a framework artifact (confirmed via the bessd crash
backtrace and `restartCount`).

---

## 9. Reading the results (what the number means)

- **What's measured:** the **dataplane-processing throughput** — access RX → full
  PDR/QER/FAR pipeline → core TX, with the egress route-lookup/MAC-rewrite short-circuited
  (the same method the reference benchmarks used). It isolates the I/O backend, which is
  exactly what you want when comparing modes.
- **Per-core vs aggregate:** single-flow ≈ one worker ≈ the per-core ceiling (~2.9 Mpps on
  i40e, scalar Rx). Multi-flow with N workers ≈ aggregate. Report both.
- **Generator vs UPF limited:** the report logs the generator ceiling. If NDR ≈ that
  ceiling, you're generator-limited, not UPF-limited. With TRex the UPF often does **not**
  saturate on a trivial pipeline — you hit the **NIC VEB internal bandwidth (~92 Gbps,
  ~80–90 Mpps @128B)** before the UPF CPU. State which limit you hit.

---

## 10. Operational gotchas (learned the hard way)

| Symptom | Cause | Fix |
|---|---|---|
| `bessd` CrashLoopBackOff, EAL "Not enough memory on socket 0" | A SIGKILL'd TRex/testpmd **leaks 1 GiB hugepage files** (`/dev/hugepages/rtemap_*`) → UPF starved | `sudo find /dev/hugepages -maxdepth 1 -name 'rtemap_*' -delete` (the framework does this on TRex start; cap TRex with `limit_memory`) |
| `ENOSPC: failed to add a new wildcard pattern` (pdrLookup 0 rules) | BESS `WildcardMatch` keeps each mask **tuple** even after rules are cleared; churned over time | restart the pod (the `reset_between_suites` hook does this) |
| TC reports **0 forwarded / NDR 0** | no short-circuit → synthetic packets die at route lookup | use the framework's `pybess` control (it installs the short-circuit); confirm `n3_remote_mac` matches the live access VF |
| TX > sent / TRex timeout under load | NIC **VEB re-circulation loop** (egress dst-MAC = an access VF) | the framework's sink-MAC rewrite breaks it; ensure you're on the committed code |
| Stuck at ~6 Mpps, workers ~49% CPU | pod `limits.cpu < workers` → CFS throttle | raise `resources.limits.cpu` ≥ worker count |
| Multi-flow gives the single-core number | `rss_flows` too low, or RSS not spreading | `rss_flows ≥ workers`; confirm 4 workers via `bessctl show worker` |
| TC-01 `TRexTimeoutError` mid-sweep | TRex misses an auto-stop across the NDR search's many trials | fixed in `traffic/trex.py` (force-stop + reuse counters); ensure you're on the committed code |
| Suite 2/3 error "needs pfcpsim" | pfcpsim not built | build it (§8.1) |
| LT-02 `forwarded ≈ 0` on DPDK | egress short-circuit not applied on the pfcpsim path | fixed (`adapter.egress_shortcircuit_*` wired into LT-02); ensure you're on the committed code |
| pfcpsim association fails / no N4 reply | `pfcpsim_iface` not the node interface | set `pfcpsim_iface` to the interface holding the node InternalIP |
| `n3neg` NT-03 errors "container not found (bessd)" mid-run | a malformed packet **crashed bessd** (SIGSEGV in `GtpuDecap`) and a later test hit it mid-restart | expected for the SUT defect; the suite waits + recovers (`wait_healthy`). If it still errors, raise the per-variant settle window in `_common.settle_and_check` |
| `n3neg` reports a crash but you want the backtrace | confirm the SUT defect | `kubectl -n aether-5gc logs upf-0 -c bessd --previous` → look for `GtpuDecap::ProcessBatch` + `Signal: 11` |

---

## 11. Quick checklist

```
Performance suite:
[ ] UPF in dpdk mode, 5/5 Running, ports show PMDPort
[ ] pod limits.cpu >= workers
[ ] free vfio-pci gen VF on the access PF identified
[ ] trex_cfg.yaml: gen VF(s) + dest_mac = access VF MAC + limit_memory + cores off workers
[ ] sdcore-bess-trex.yaml: n3_remote_mac, trex_root, rss_flows >= workers
[ ] hugepages free (no leaked rtemap_*)

Load + PFCP suites (add):
[ ] pfcpsim + pfcpctl built in third_party/pfcpsim (Go >= 1.25)
[ ] config has pfcpsim_iface (node iface) + n3_addr + gnb_addr + ue_pool + pfcpsim_mbr_kbps
[ ] upf service exists: kubectl get svc upf -n aether-5gc

Run:
[ ] sudo -E python3 -m upfbench.cli run --config configs/sdcore-bess-trex.yaml --suite all --campaign ...
```
