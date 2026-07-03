# Test *your* SD‑Core UPF with CNTC — end‑to‑end guide

This is the "someone has their own VM with SD‑Core running and wants a CNTC verdict + certificate"
walkthrough. It is written from a real bring‑up (af_packet SD‑Core BESS‑UPF on a single‑NIC VM)
and folds in the fixes that make it work e2e. It builds on the framework's own docs — see
`docs/fresh-vm-setup.md`, `docs/config-reference.md`, `docs/benchmarking-guide.md`.

> **Assumption:** CNTC/upfbench runs **on the same machine** where the UPF is reachable (the
> node with `kubectl` access to the UPF pod, and a host interface that reaches the UPF's N3).
> That's how the `sdcore_bess` adapter works — it `kubectl exec`s into the UPF pod for counters
> and injects host traffic into the UPF's access interface.

---

## 0. What CNTC needs from your environment

| Need | Why | How CNTC uses it |
|------|-----|------------------|
| A running **SD‑Core BESS‑UPF** (aether‑onramp / charmed‑sdcore / docker) | the SUT | adapter reads live mode/ports/counters |
| **`kubectl` access** to the cluster running the UPF (`KUBECONFIG`) | drive a K8s‑deployed UPF | `kubectl exec upf-0 -c bessd -- bessctl …` |
| A **host interface that reaches the UPF's N3** (access macvlan on af_packet) | inject GTP‑U | `tcpreplay -i <gen_iface>` |
| **pfcpsim + pfcpctl** built (Go) | N4 sessions for pfcp/load/n3neg | control plane |
| **Python 3.10+**, `tcpreplay`, `scapy`, **passwordless sudo** | craft + send raw frames | traffic generator |
| (optional) `dash`+`plotly`, LaTeX | dashboard, PDF reports | reporting |

**DPDK/AF_XDP/CNDP mode** additionally needs **TRex + SR‑IOV VFs + 1GiB hugepages** (bare‑metal
only) — see `docs/dpdk-testing-guide.md`. On a **VM you almost always run af_packet**, which is
what this guide covers.

---

## 1. Install the tester (once, on the UPF node)

```bash
# from the framework repo root
sudo ./scripts/bootstrap_fresh_vm.sh          # apt deps + pip install + builds pfcpsim
#   (installs: python3/pip, tcpreplay, tcpdump, iproute2; pip install -e '.[dashboard]';
#    go build pfcpsim + pfcpctl)

# make the CLIs available
export PATH=$PATH:$HOME/.local/bin

# preflight — tells you exactly what's missing
python3 -m upfbench.cli doctor
```

`doctor` checks: Python deps (yaml/jinja2/scapy), pfcpsim built, kubectl reachable, dashboard
deps, and (for DPDK only) TRex/hugepages/VFs. **For af_packet you want:** core deps OK,
pfcpsim OK, kubectl "reachable (N nodes)". Hugepages/VFs/TRex WARN are **fine** on af_packet.

Make sure `kubectl` works from here:
```bash
export KUBECONFIG=$HOME/.kube/config
export PATH=$PATH:/var/lib/rancher/rke2/bin        # if RKE2
kubectl get pods -A | grep -iE 'upf|bess'          # find your UPF pod + namespace
```

---

## 2. Configure for **your** SD‑Core (the only real work)

Copy the sample and edit the fields that are deployment‑specific:

```bash
cp configs/sdcore-bess.yaml configs/my-upf.yaml
```

### 2.1 Fields you MUST set to match your deployment

| Field (under `upf:`) | What to put | How to find it |
|---|---|---|
| `adapter` | `sdcore_bess` | (BESS‑UPF) |
| `namespace` | your UPF's K8s namespace | `kubectl get pods -A \| grep upf` (e.g. `aether-5gc`, `omec`, `sdcore`) |
| `pod` | your UPF pod name | e.g. `upf-0` |
| `bessd_container` | `bessd` | usually `bessd` |
| `n3_iface` / `n6_iface` | BESS access/core port names | usually `access` / `core`; verify: `kubectl -n <ns> exec <pod> -c bessd -- bessctl show port` |
| `gen_iface` | **host** interface that reaches the UPF N3 | the access macvlan on the host (e.g. `access`); `ip -br link` |
| `n3_remote_ip` | UPF's N3/access IP (outer dst) | your deployment's access subnet |
| `ue_ip` / UE pool | must fall in your pfcpsim UE pool | matches `pfcpsim` `--ue-pool` (default `10.250.0.0/24`) |

Also set the informational `sut:` block (cpu/nic/kernel) — it just goes on the report + rig.

### 2.2 Fields you should LEAVE BLANK (learned the hard way)

```yaml
upf:
  n3_remote_mac: ""      # LEAVE BLANK -> resolved LIVE from the pod each run.
                         # The macvlan MAC is REGENERATED on every UPF pod restart, so a
                         # hardcoded MAC goes stale and traffic silently stops reaching the UPF.
```

> This was the single biggest gotcha: a stale hardcoded N3 MAC → 0 packets delivered → NDR 0.0
> and n3neg "valid forwarded 0". CNTC now resolves it live (both the tcpreplay generator and the
> n3neg suite). **Keep it blank** unless your UPF has a fixed MAC (e.g. a Docker bridge — then set
> `n3_mac_via: arp`).

### 2.3 af_packet tuning (so numbers are real, not 0)

af_packet's kernel socket tops out very low (~0.002–0.006 Mpps on a shared VM), so the default
DPDK‑oriented rates make the no‑drop search report 0. Set:

```yaml
performance:
  control: pybess                 # white-box forward-all (Suite 1)
  generator: tcpreplay            # host af_packet injection
  max_rate_mpps: 0.02             # search ceiling near the af_packet ceiling
  search_resolution_mpps: 0.0005  # fine enough to resolve the true low NDR
  trial_duration_s: 5

load:
  control: pfcpsim
  generator: tcpreplay

# N3 robustness over af_packet (no TRex): pfcpsim sessions + tcpreplay send_burst
n3neg:
  control: pfcpsim
  generator: tcpreplay
  # burst_pps: 5000               # (default) af_packet-friendly send rate; lower if RX overruns

reset_between_suites: true        # clean UPF state between suites (set false for n3neg-only)
```

> **`burst_pps`** (new knob) caps the n3neg injection rate for af_packet. 5000 is a good default;
> the af_packet RX ring drops most of a 50k‑pps blast, so a lower rate = clean delivery.

---

## 3. Run it end‑to‑end

```bash
export KUBECONFIG=$HOME/.kube/config
export PATH=$PATH:/var/lib/rancher/rke2/bin:/usr/local/bin:$HOME/.local/bin

# 1) performance + load + PFCP (13 tests)
cntc run --config configs/my-upf.yaml --suite all --campaign MY-UPF-001

# 2) N3 robustness (3 tests) — deliberately sends malformed GTP-U; the UPF may crash + recover
cntc run --config configs/my-upf.yaml --suite n3neg --campaign MY-UPF-001-N3
```

> `cntc run` delegates to the engine and auto‑grades. You can also use `upfbench run …` — same
> engine; `cntc run` just adds the verdict at the end. Suites can also be run individually
> (`--suite performance|load|pfcp|n3neg`) or as `--suite conformance` (= pfcp+n3neg, the cert set).

### 3.1 Combine into one campaign + grade

The engine's `all` doesn't include `n3neg`, so merge the two results, then grade:

```bash
python3 - <<'PY'
import json
a=json.load(open("campaigns/MY-UPF-001/results.json"))
n=json.load(open("campaigns/MY-UPF-001-N3/results.json"))
ns=[s for s in n["suites"] if s["suite"]=="n3neg"][0]
a["suites"]=[s for s in a["suites"] if s["suite"]!="n3neg"]+[ns]; a.pop("verdict",None)
json.dump(a,open("campaigns/MY-UPF-001/results.json","w"),indent=2,default=str)
PY

cntc verdict campaigns/MY-UPF-001/results.json --write-back    # PASS / FAIL / INCOMPLETE + scorecard
```

### 3.2 Certificate (only issues on PASS)

```bash
cntc certify campaigns/MY-UPF-001/results.json
#   PASS -> writes certificate.md / .html / .json + prints the cert
#   FAIL -> refuses, naming the blocking essential test
```

### 3.3 Dashboard

```bash
cntc profiles                                   # list requirement profiles
python3 -m upfbench.cli dashboard               # http://<host>:8050  (reads campaigns/ live)
#   (persistent: run it in tmux -> `tmux new-session -d -s dash 'python3 -m dashboard.app'`)
```
The campaign page shows the scorecard, the **verdict badge**, the **certificate banner**
(issued/withheld), and every test's numbers.

---

## 4. What "pass" means (the gate you're certifying against)

- **16 test cases** total: Performance (5) + Load (3) = *measured* (numbers, don't gate);
  **PFCP CF‑01..05 (5) + N3 NT‑01..03 (3) = essential** (gate certification).
- Certificate issues only if **every essential test passes** (conformance profile).
- Tune the gate in `cntc/standards/conformance.yaml` (bump its `version` when you do — see
  `docs/CNTC-GOVERNANCE.md`).

---

## 5. Troubleshooting — the real gotchas

| Symptom | Cause | Fix |
|---|---|---|
| UPF pod `0/5 Unknown` / not ready | stuck StatefulSet after a node/containerd hiccup | `kubectl -n <ns> delete pod <upf-pod>` → StatefulSet recreates it |
| **NDR 0.0 / "valid forwarded 0"** | stale hardcoded N3 MAC (macvlan MAC changed on restart) | set `n3_remote_mac: ""` (live resolve) — this is the #1 issue |
| Still 0 delivered | src MAC = a local macvlan MAC (self‑origin loopback filtered) | leave `trex_src_mac` unset, or use a fake `02:..` MAC |
| Packets delivered but 0 forwarded to N6 | forward rules not installed / wrong suite path | `--suite performance` uses pybess forward‑all; verify `bessctl show module pdrLookup` shows 1+ rules during a run |
| NDR still 0 though forwarding works | search floor above af_packet's ceiling | lower `max_rate_mpps` (0.02) + `search_resolution_mpps` (0.0005) |
| n3neg tests "error: needs pfcpsim + trex" | pfcpsim not built, or generator has no `send_burst` | build pfcpsim; use the patched tcpreplay (has `send_burst`) |
| n3neg extremely slow (~4 min) | 20 s crash‑settle × 6 variants (by design) | expected; run it in tmux and poll |
| Verdict `INCOMPLETE` | an essential suite didn't run | run `--suite conformance` (pfcp + n3neg) for the full gate |

---

## 6. Other UPFs / other modes (pointers)

- **OAI‑UPF / Open5GS‑UPF:** set `adapter: oai_upf` / `open5gs_upf`; for Docker‑bridge UPFs set
  `n3_mac_via: arp`. See `docs/oai-upf-testing.md`, `docs/open5gs-deployment.md`.
- **DPDK / AF_XDP / CNDP (bare‑metal):** use `configs/sdcore-bess-trex.yaml` + install TRex,
  bind a spare SR‑IOV VF to `vfio-pci`, allocate 1GiB hugepages. See `docs/dpdk-testing-guide.md`.
  The adapter reads the live mode and the injection guard picks the right generator automatically.

---

*This guide reflects a validated af_packet e2e run and the CNTC verdict/certificate layer.
Last verified: 2026‑07‑02 against SD‑Core BESS‑UPF `upf-bess:rel-2.4.3` on RKE2.*
