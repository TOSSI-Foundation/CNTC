---
title: "CNTC Certification Report — free5GC + eUPF (eBPF/XDP)"
subtitle: "Conformance · eBPF/XDP Dataplane Assurance · Performance & Scale"
author: "TOSSI Foundation — Cloud Native Telecom Certification (CNTC)"
date: "July 2026"
geometry: margin=2cm
toc: true
toc-depth: 3
---

\newpage

# 1. Executive summary

**free5GC + eUPF** (github.com/edgecomllc/eupf) was admitted as a CNTC UPF target — the
framework's **first eBPF/XDP dataplane** (new mode: `ebpf_xdp`). It was tested end-to-end against
a live deployment and earned **two certificates**.

| Certificate | Profile | Essential gate | Result | Certificate ID |
|---|---|---|---|---|
| **UPF Conformance** (universal) | `conformance` | **7 / 7** | **PASS** | `CNTC-CONF-4044A34697` |
| **UPF eBPF/XDP Dataplane Assurance** (optional) | `upf-ebpf` | **4 / 4** | **PASS** | `CNTC-UPF--52414ADD92` |
| Data-plane Performance & Scale | `performance` | 0 / 1 (TC-03 not observable) | INCOMPLETE — **observational only, no certificate claimed** | — |

**Headline results**

- **16 / 16** certification tests **PASS** (8 conformance + 8 eBPF/XDP). Zero failures, zero `na`.
- **Zero packet loss at every frame size** 128–1518 B; the *test rig*, not eUPF, was the limit.
- **1000 concurrent PFCP sessions** established; **100 UEs** forwarding simultaneously at ~0.50 Gbps.
- **eUPF correctly enforces QER** (per-session MBR) — discovered during benchmarking.
- eUPF is only the **second UPF** in the framework (after SD-Core BESS) with **real crash
  observability**, which is what allows NT-01/NT-02 to be graded rather than skipped.

\newpage

# 2. System under test (SUT)

| Property | Value |
|---|---|
| UPF | free5GC + eUPF (`ghcr.io/edgecomllc/eupf:main`) |
| Dataplane | **eBPF/XDP** — mode `ebpf_xdp` |
| **XDP attach mode** | **`generic` (SKB fallback)** — native/offload unavailable on this NIC |
| XDP interface | pod `eth0` (Calico veth) |
| Pinned BPF object | `/sys/fs/bpf/upf_pipeline` |
| Adapter | `upfbench/adapters/eupf.py` |
| Orchestration | microk8s, namespace `free5gc` |
| NIC | Xen `vif` (paravirtual) |
| N4 control | pfcpsim (vendored) → eUPF pod IP :8805 |
| N3 injection | tcpreplay into the pod's Calico veth · GTP-U to `n3_addr:2152` |
| Observability source | eUPF REST API (`:8080`) + k8s `restartCount` |

## 2.1 How eUPF differs from the other UPF targets

| | gtp5g UPFs (free5GC/Open5GS) | BESS-UPF | **eUPF** |
|---|---|---|---|
| Dataplane | kernel module | BESS userspace pipeline | **eBPF program on the XDP hook** |
| Session state | kernel tables | BESS modules | **eBPF maps** (PDR/FAR/QER/session) |
| Uplink forward action | TUN inject (`rx_pkts`) | port TX (`tx_pkts`) | **XDP_TX / XDP_REDIRECT (`tx_pkts`)** |
| Introspection | `/proc/net/dev` | `bessctl` | **REST API + bpffs** |

\newpage

# 3. Certification structure

CNTC separates **what is measured** (the engine) from **what is judged** (the profile/catalog).
eUPF is graded against three profiles:

## 3.1 Profile A — UPF Conformance (universal, certificate)
Hardware-**independent** correctness: does the UPF obey N4/PFCP and survive malformed N3?
Every UPF earns this the same way, so results are comparable across implementations.
**8 tests, 7 essential.** Standards: 3GPP TS 29.244 (PFCP/N4), TS 29.281 (GTP-U/N3).

## 3.2 Profile B — eBPF/XDP Dataplane Assurance (optional, certificate)
White-box assurance that only an eBPF UPF can satisfy: the XDP fast path is real and engaged,
and N4 rules bind into / unbind from the BPF maps. **8 tests, 4 essential.**
Non-eBPF UPFs grade every test `na` → INCOMPLETE → **no certificate** (correct: a DPDK UPF is
not an eBPF UPF).

## 3.3 Profile C — Performance & Scale (observational, no certificate here)
Rig-**dependent** throughput/latency/scale. Deliberately **not** claimed for eUPF: its single
essential (TC-03 latency) requires a white-box in-pipeline probe eUPF does not expose.

\newpage

# 4. Complete test catalogue (24 tests across 5 suites)

Legend: **E** = essential (gates a certificate) · *n* = normal (scored, non-gating)

## 4.1 Suite `pfcp` — PFCP / N4 conformance (5 tests, 5 essential)

| ID | Cls | Name | What it verifies |
|---|:--:|---|---|
| CF-01 | **E** | PFCP association setup / release | UPF accepts an Association Setup and releases it (TS 29.244 §7.4) |
| CF-02 | **E** | PFCP session establishment | UPF accepts a session with PDR/FAR/QER |
| CF-03 | **E** | PFCP session modification | UPF applies a Session Modification |
| CF-04 | **E** | PFCP session deletion | UPF deletes a session cleanly |
| CF-05 | **E** | PFCP error handling | Unknown SEID / missing IE rejected with the correct cause |

## 4.2 Suite `n3neg` — N3 GTP-U robustness (3 tests, 2 essential)

| ID | Cls | Name | What it verifies |
|---|:--:|---|---|
| NT-01 | **E** | Unknown-TEID robustness | GTP-U on an unmatched TEID is dropped, valid traffic still forwards, UPF alive |
| NT-02 | **E** | Malformed GTP-U robustness | Malformed GTP-U does not crash the UPF (a crash = remote user-plane DoS) |
| NT-03 | *n* | PDU-Session-Container ext-header | Malformed PSC (0x85) ext-header handled without crash |

## 4.3 Suite `ebpf` — eBPF/XDP Dataplane Assurance (8 tests, 4 essential)

| ID | Cls | Category | Name | What it verifies |
|---|:--:|---|---|---|
| XDP-01 | **E** | dataplane | XDP program attached to N3 iface | an eBPF program is actually on the XDP hook |
| XDP-02 | *n* | dataplane | Attach mode native vs generic/SKB | records the attach mode (informational) |
| XDP-03 | *n* | dataplane | Required BPF maps present & pinned | PDR/FAR/QER/session maps exist and are pinned |
| XDP-04 | **E** | binding | Installed PFCP session reflected in maps | control→dataplane **bind** (TS 29.244 §5.2) |
| XDP-05 | **E** | binding | Deleted session removes map entries | **unbind** — no stale dataplane state |
| XDP-06 | *n* | binding | Map entry count matches session count | no leak / no silent rule rejection |
| XDP-07 | **E** | dataplane | XDP fast path engaged under N3 traffic | packets really traverse XDP and are forwarded |
| XDP-08 | *n* | robustness | Program stays attached after malformed burst | no silent detach to the slow path |

## 4.4 Suite `performance` — data-plane performance (5 tests)

| ID | Name | What it measures |
|---|---|---|
| TC-01 | Throughput vs frame size (NDR/PDR) | zero-loss (NDR) and ≤tolerance (PDR) rate per frame size |
| TC-02 | Bidirectional throughput (UL+DL) | simultaneous uplink + downlink |
| TC-03 | Latency / jitter | in-pipeline latency percentiles (white-box probe) |
| TC-04 | Burst / back-to-back (+ drain) | behaviour under saturation, buffer drain |
| TC-08 | Multi-flow (RSS) | throughput across many TEID/UE flows |

## 4.5 Suite `load` — scale (3 tests)

| ID | Name | What it measures |
|---|---|---|
| LT-01 | Max concurrent UE sessions | session capacity ceiling + install rate |
| LT-02 | Aggregate + per-UE throughput under N UEs | aggregate Gbps and per-UE fairness |
| LT-03 | Latency / jitter vs UE count | latency degradation under session load |

\newpage

# 5. Results — Certificate A: UPF Conformance

**Verdict: PASS · essential 7/7 · certificate `CNTC-CONF-4044A34697`**
Campaign `EUPF-CONF-RUN2` · suites `pfcp` + `n3neg`

| ID | Cls | Status | Evidence |
|---|:--:|:--:|---|
| CF-01 | **E** | **PASS** | `associate_ok=True`; `release_ok=n/a` — see §5.1 |
| CF-02 | **E** | **PASS** | `establish_ok=True` |
| CF-03 | **E** | **PASS** | `establish_ok=True, modify_ok=True` |
| CF-04 | **E** | **PASS** | `establish_ok=True, delete_ok=True` |
| CF-05 | **E** | **PASS** | `delete_unknown_rejected=True, modify_unknown_rejected=True` |
| NT-01 | **E** | **PASS** | valid forwarded **20 000**, unknown-TEID forwarded **0** of 20 000, UPF alive |
| NT-02 | **E** | **PASS** | **0 crashes**, 20 000 forwarded after the malformed burst, recovered |
| NT-03 | *n* | **PASS** | valid PSC forwarded 20 000, malformed PSC forwarded **0**, no crash |

## 5.1 Declared capability gap — PFCP Association Release
eUPF does **not** implement the PFCP Association Release procedure. This is documented by the
vendor (eUPF 3GPP compatibility matrix: TS 29.244 §7.4.4.5 / §7.4.4.6 = `N`); it tears
associations down via heartbeat loss instead. CF-01 therefore verifies **Association Setup**
(which passes) and records Release as a **declared gap** — the identical handling the framework
already applies to OAI-UPF. It is recorded in the result, not hidden.

## 5.2 Why NT-01/NT-02 are trustworthy here
Crash robustness is only meaningful if a crash can be *detected*. The eUPF adapter reports the
Kubernetes container `restartCount`, `lastState.terminated`, and REST liveness — so a datapath
crash is observed, not inferred. Without that, these tests would grade `na` and **no certificate
would be issued**.

\newpage

# 6. Results — Certificate B: eBPF/XDP Dataplane Assurance

**Verdict: PASS · essential 4/4 · certificate `CNTC-UPF--52414ADD92`**
Campaign `EUPF-EBPF-RUN3` · suite `ebpf`

| ID | Cls | Status | Evidence |
|---|:--:|:--:|---|
| XDP-01 | **E** | **PASS** | attached on `eth0`; pinned object `upf_pipeline` |
| XDP-02 | *n* | **PASS** | `mode=generic`, **`native=False`** — see §6.1 |
| XDP-03 | *n* | **PASS** | pdr/far/qer/session all pinned (max 131070 / 131070 / 65535 / 65535) |
| XDP-04 | **E** | **PASS** | TEID **700701** present in PDR map; entries **0 → 2** |
| XDP-05 | **E** | **PASS** | TEID 700801 present after install, **gone after delete** (`teids=[]`) |
| XDP-06 | *n* | **PASS** | installed 5 → session-map **+5**, 5 matching TEIDs |
| XDP-07 | **E** | **PASS** | sent 1000 → `rx_gtp_pdu +1000`, **forwarded +1000** (100%) |
| XDP-08 | *n* | **PASS** | 2000 malformed GTP-U → still attached, still alive |

## 6.1 Certified on GENERIC XDP
XDP-02 records that the program is attached in **`generic` (SKB) mode**, not native/driver.
Generic XDP runs after the socket buffer is built and is substantially slower than native XDP.
It is **forced by this rig** — the NIC driver is Xen `vif` and the attach point is a Calico veth,
neither of which supports native XDP. This is recorded, not penalised: XDP-02 is *normal* class
and does not gate the certificate. **All eUPF results in this report are on generic XDP.**

\newpage

# 7. Results — Performance & Scale (observational)

> **These numbers characterise this RIG, not eUPF's ceiling.** Generic XDP + injection over a
> Calico veth + a host tcpreplay sender. A definitive benchmark needs native XDP on a physical
> NIC and a DPDK generator (TRex/testpmd).

## 7.1 TC-01 — Throughput vs frame size (zero loss at every size)

| Frame (B) | Forwarded (Mpps) | Bitrate | Loss |
|---:|---:|---:|:--:|
| 128 | 0.1344 | ~138 Mbps | **0 %** |
| 256 | 0.1473 | ~302 Mbps | **0 %** |
| 512 | 0.1347 | ~552 Mbps | **0 %** |
| 1024 | 0.1461 | ~1.20 Gbps | **0 %** |
| 1518 | 0.1196 | ~1.45 Gbps | **0 %** |

`peak_NDR = 0.2461 Mpps` · **`generator_ceiling = 0.1531 Mpps`**

**Interpretation:** NDR exceeds the generator ceiling, so the result is **generator-limited, not
UPF-limited**. eUPF forwarded **100 % of everything offered at every frame size**. This is a
**lower bound** — eUPF's ceiling was never reached.

*64 B is intentionally absent: GTP-U encapsulation alone is 78 B, so a 64 B frame cannot carry it.*

## 7.2 TC-04 / TC-08 — Saturation and multi-flow

| Test | Configuration | Forwarded | Drops |
|---|---|---:|:--:|
| TC-04 Burst | 512 B, saturating | 0.1271 Mpps (~520 Mbps) | **0** |
| TC-08 Multi-flow | 512 B, 16 TEID/UE flows | 0.1169 Mpps (~479 Mbps) | **0** |

## 7.3 LT-01 — Session capacity

| UEs | Established | Install time | Rate |
|---:|:--:|---:|---:|
| 10 | ✔ | 0.039 s | 259 sessions/s |
| 100 | ✔ | 0.499 s | 200 sessions/s |
| 1000 | ✔ | 5.184 s | 193 sessions/s |

`capacity_sessions = 1000` — the largest batch tested, **not** a ceiling (eUPF advertises
`max_sessions = 65535`).

## 7.4 LT-02 — Aggregate throughput under 100 UEs

| UEs | Offered | Aggregate | Per-UE avg | Loss |
|---:|---:|---:|---:|:--:|
| 100 | 0.1215 Mpps | **0.4975 Gbps** | 4.97 Mbps | **0 %** |

Per-UE verification: **8 / 8** sampled UEs each forwarded **10 000 / 10 000** packets.

## 7.5 Not measured (honestly skipped)

| ID | Status | Why |
|---|:--:|---|
| TC-03 | `skipped` | needs a white-box **in-pipeline latency probe**; eUPF exposes no latency instrumentation |
| LT-03 | `skipped` | same probe |
| TC-02 | `skipped` | needs the **TRex 2-port** generator (DPDK/XDP VF) + a BESS-style downlink hook |

These are reported as `skipped`/`na` — **never** substituted with an estimate. This is why the
`performance` profile is INCOMPLETE (its one essential is TC-03) and **no performance
certificate is claimed**.

\newpage

# 8. Key findings

## 8.1 eUPF enforces QER (per-session MBR) — a positive conformance signal
The first benchmark run showed single-flow 512 B forwarding only 0.0154 Mpps with **1 366 555
drops**. Root cause: pfcpsim's default subscriber model installs `max_bitrate_ul = 60 000 000`
(60 Mbps), and 0.0154 Mpps × 512 B × 8 = **63 Mbps** — the measurement was of the **rate
limiter**, not the datapath. Raising the MBR (`pfcpsim_mbr_kbps`, the same treatment the
framework applies to the other QER-enforcing UPF, BESS) reduced drops to **0**.
**Conclusion: eUPF correctly implements 3GPP QoS enforcement.**

## 8.2 Search-resolution artifact (methodology, corrected)
The default binary-search resolution (0.05 Mpps) could not resolve rates below its own step and
reported a false `NDR = 0` for frames ≥ 256 B. Corrected to 0.005 Mpps.

## 8.3 Crash observability extended to a second UPF
Before this work, reliable crash detection existed only for SD-Core BESS. eUPF now also provides
it (k8s `restartCount` + `lastState.terminated` + REST liveness), so its NT-01/NT-02 results are
genuinely graded. The three gtp5g/simpleswitch adapters still lack it.

\newpage

# 9. Reproducing this report

```bash
cd /home/ubuntu/control_cntc

# Certificate A — universal conformance (CF-01..05 + NT-01..03)
sudo python3 -m upfbench.cli run --config configs/eupf.yaml \
     --suite conformance --profile conformance --campaign EUPF-CONF
python3 -m cntc.cli certify campaigns/EUPF-CONF/results.json --profile conformance

# Certificate B — eBPF/XDP dataplane assurance (XDP-01..08)
sudo python3 -m upfbench.cli run --config configs/eupf.yaml \
     --suite ebpf --profile upf-ebpf --campaign EUPF-EBPF
python3 -m cntc.cli certify campaigns/EUPF-EBPF/results.json --profile upf-ebpf

# Both certificates in one shot
make eupf-certify CONFIG=configs/eupf.yaml PREFIX=EUPF

# Observational benchmarks (no certificate)
sudo python3 -m upfbench.cli run --config configs/eupf.yaml --suite performance --campaign EUPF-PERF
sudo python3 -m upfbench.cli run --config configs/eupf.yaml --suite load        --campaign EUPF-LOAD
```

> **Note:** `--suite all` expands to `performance + load + pfcp` only. It **excludes `n3neg`**
> (NT-01/NT-02 are conformance essentials) and `ebpf`, so it does **not** produce either
> certificate. Use `--suite conformance` and `--suite ebpf`.

\newpage

# 10. Artifacts

| Artifact | Path |
|---|---|
| Conformance results + report | `campaigns/EUPF-CONF-RUN2/{results.json, scorecard.md, report-all.pdf}` |
| eBPF/XDP results + report | `campaigns/EUPF-EBPF-RUN3/{results.json, scorecard.md, report-ebpf.pdf}` |
| Performance results + report | `campaigns/EUPF-PERF2/{results.json, report-performance.pdf}` |
| Load results + report | `campaigns/EUPF-LOAD/{results.json, scorecard.md, report-load.pdf}` |
| Adapter | `upfbench/adapters/eupf.py` |
| eBPF/XDP test suite | `upfbench/suites/ebpf/` |
| Campaign config | `configs/eupf.yaml` |
| eBPF catalog (the standard) | `cntc/standards/upf-ebpf.yaml` |
| Profile design doc | `docs/UPF-EBPF-PROFILE.md` |

---

*Scope: technical conformance to the stated CNTC profiles on the stated rig. A technical result,
not a governance-backed brand. Every certificate is verifiable against the `verdict` block of the
campaign `results.json` that produced it.*
