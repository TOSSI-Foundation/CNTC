# CNTC: Cloud Native Telecom Certification

**CNTC** (*Cloud Native Telecom Certification*) is an open framework that tests open-source
telecom network functions and turns the raw results into a **graded, standards-aligned verdict
and certificate**, the model is simple:
*a published standard → automated tests → a pass/fail certification gate → a scorecard*.

## Objective

Give the whole cloud-native telecom stack **one reproducible “test → grade → certify” pipeline**:
one requirement catalog, one verdict engine, one scorecard, so any open-source NF can be measured
against a versioned standard on any rig and earn (or be refused) a certificate.

We are building it **outward from the data plane**, one layer at a time:

| Stage | Scope | Status |
|-------|-------|--------|
| 1 | **UPF**: 5G user plane over N3/N4 (performance, load, PFCP conformance, N3 robustness) | ✅ **available today** |
| 2 | **5G Core control plane**: AMF · SMF · NRF · AUSF · UDM · UDR · PCF (NAS/NGAP · N4/PFCP · SBI) | ✅ **available today** |
| 3 | **RAN**: split gNB over F1/E1 (O-CU-CP / O-CU-UP / O-DU) and over nFAPI (PNF / VNF), on OCUDU and OAI | ✅ **available today** |
| 4 | **SMO**: Service Management & Orchestration | 🗺️ roadmap |
| 5 | **RIC**: Near-RT / Non-RT RIC · xApps / rApps (E2 · A1 · O1) | 🗺️ roadmap |
| 6 | **Full O-RAN ecosystem**: end-to-end certification across the stack | 🎯 vision |

**Stages 1, 2 and 3 are in production today**: the **user plane** (`upfbench`), the **control
plane** (`cpbench`) and the **RAN** (`ranbench`). The engines that measure and the umbrella that judges (`cntc`) are
deliberately decoupled, so each stage adds a new engine + a requirement catalog **without touching
the grading core**, which is exactly how the control plane was added.

CNTC has two kinds of layer, **engines that measure**, and **one umbrella that judges**:

| Layer | What it is | Where |
|-------|-----------|-------|
| **Engine** (`upfbench`) | The **user-plane** engine that drives any open-source 5G UPF over N3/N4 (performance, load, PFCP conformance, N3 robustness). | [`upfbench/`](upfbench/) |
| **Engine** (`cpbench`) | The **control-plane** engine that drives the 5G core NFs (AMF/SMF/NRF/AUSF/UDM/UDR/PCF) over N1/N2 (NAS/NGAP), N4 (PFCP) and the SBI, and captures the signalling on the wire. | [`cpbench/`](cpbench/) |
| **Engine** (`ranbench`) | The **RAN** engine. Certifies the CU/DU split (O-CU-CP, O-CU-UP, O-DU) over N2 (NGAP), F1-C (F1AP), E1 (E1AP), F1-U and N3 (GTP-U) with RRC read out of the F1AP containers, and the L1/L2 split (PNF, VNF) over nFAPI (SCF222/225 P5 and P7). | [`ranbench/`](ranbench/) |
| **Verdict** (`cntc`) | The umbrella: a **requirement catalog** per profile (`cntc/standards/*.yaml`) + a pure **verdict engine** that grades *any* engine's results and emits a **scorecard** + certificate. | [`cntc/`](cntc/) |

The engines measure; the umbrella judges. They are decoupled, so `cntc.verdict` grades the
serialized `results.json`, so it can also **re-grade any past campaign** without re-running it.

---

## Automation: one `make` entrypoint

Every step of the pipeline is wrapped in a Makefile, so a full certification is a handful of
commands. Run `make` on its own to print the menu.

```bash
make prereqs                                        # deps + build pfcpsim + doctor   (needs sudo)
make configure                                      # wizard -> configs/<campaign>.yaml
make run     CONFIG=configs/my-upf.yaml CAMPAIGN=SDCORE-AF-001   # run all + n3neg -> verdict -> certify
make certify CAMPAIGN=SDCORE-AF-001                 # issue the certificate iff the verdict is PASS
make dashboard                                      # live web UI over campaigns/   (or: make dashboard-bg)
```

| Target | What it does |
|--------|--------------|
| `make prereqs` | install deps, build the vendored pfcpsim, run the `doctor` preflight (sudo) |
| `make configure` | interactive wizard → writes `configs/<campaign>.yaml` |
| `make run` | **full e2e**: all suites + n3neg → merge → verdict → certify |
| `make run-conformance` | pfcp + n3neg only (the certification set) + grade |
| `make run-perf` · `make run-n3neg` | performance + load + pfcp · N3 robustness only |
| `make verdict` | (re)grade a campaign → scorecard (`--write-back`) |
| `make certify` | issue a certificate **iff** the verdict is `PASS` |
| `make cp-configure` · `make cp-doctor` · `make cp-run` | **control plane**: wizard · preflight · run AMF/SMF/NRF/AUSF/UDM/UDR/PCF (docker **or** Kubernetes) |
| `make ran-prereqs` · `ran-configure` · `ran-doctor` · `ran-run` · `ran-certify` | **RAN, CU/DU split**: install the tester · wizard · preflight · run O-CU-CP/O-CU-UP/O-DU · certify (OCUDU or OAI) |
| `make ran-oai-fapi-doctor` · `make ran-oai-fapi-run` | **RAN, L1/L2 split**: preflight · run the pure-OAI PNF/VNF stack over nFAPI |
| `make eupf-run` · `make eupf-certify` | free5GC + eUPF (eBPF/XDP): run · **dual** certificate (conformance + `upf-ebpf`) |
| `make dashboard` · `dashboard-bg` · `dashboard-stop` | live Plotly dashboard: foreground · tmux · stop |
| `make profiles` · `make lint` · `make test` | list profiles · validate catalogs · run verdict unit tests |
| `make k8s-deploy` · `make k8s-run` · `make k8s-clean` | dashboard in Kubernetes · in-cluster run Job · teardown |

Override the vars inline, e.g. `make run CONFIG=configs/sdcore-bess.yaml CAMPAIGN=MY-UPF-001`.

---

## Quick start

```bash
pip install -e .                                   # installs both `cntc` and `upfbench`

# 1) engine only: measure a UPF (unchanged behavior, now ends with a scorecard)
upfbench run --config configs/sdcore-bess-trex.yaml --suite pfcp      # PFCP conformance
upfbench run --config configs/sdcore-bess-trex.yaml --suite n3neg     # N3 robustness

# 2) control plane: certify a live 5G core's NFs (bring-your-own free5GC / Open5GS / OAI)
./scripts/bootstrap_cpbench.sh                     # deps + UERANSIM built from source (AGPL, fetched)
make cp-doctor  CONFIG=configs/free5gc-cp.yaml     # preflight -> READY
make cp-run     CONFIG=configs/free5gc-cp.yaml NF=amf
cntc certify    campaigns/<id>/results.json --profile amf-conformance   # cert iff every essential passed

# 3) umbrella: list profiles / grade / re-grade (works for BOTH engines)
cntc profiles                                      # available requirement profiles
cntc run --config configs/sdcore-bess.yaml --suite pfcp --profile conformance
cntc verdict campaigns/<id>/results.json           # re-grade a past run -> scorecard
cntc verdict campaigns/<id>/results.json --profile performance --baseline campaigns/<ref>/results.json
```

Every run/verdict writes `campaigns/<id>/scorecard.md` and embeds a `verdict` block in
`results.json`. Exit code is non-zero unless the result is `PASS` (CI-friendly).

### What a scorecard looks like
```
  CNTC VERDICT, profile: conformance  (catalog v0.1.0)
   [PASS] * CF-01  PFCP association setup / release    status == pass
   ...
   [FAIL] * NT-02  Malformed GTP-U robustness (no crash)   status == fail
   essential gate (all): 7 passed / 1 failed / 0 na  (of 8)
  RESULT:  FAIL  ✗
```

---

## The verdict model (how grading works)

- **Profiles** (`cntc/standards/<profile>.yaml`) define the standard, split by portability:
  - **`conformance`**: *hardware-independent*, binary pass/fail: PFCP/N4 conformance
    (3GPP TS 29.244) + N3 GTP-U robustness. Certifiable on **any** rig. **8 essential tests.**
  - **`performance`**: *hardware-dependent*: throughput/latency graded **relative to a
    baseline** on the same rig class (never absolute Mpps, af_packet on a shared VM is ~⅕ of
    dedicated DPDK). Needs a `baseline:` in the config.
- **Weight classes:** `essential` (gates certification) · `normal` · `bonus`.
- **Verdict rules** (per test, in the catalog): `status_pass`, `{kind: metric,…}` (absolute),
  `{kind: baseline_rel,…}` (relative). A test that didn't run / errored / is missing a metric
  is **`na`**: *never silently promoted to pass*.
- **Result:** `PASS` (all essentials passed) · `FAIL` (an essential failed) · `INCOMPLETE`
  (an essential didn't run). The gate can also be `min_essential: N` (e.g. "15 of 19").

To change the bar, edit the YAML catalog, no engine code changes. Add a profile by dropping
a new `cntc/standards/<name>.yaml`. Validate catalogs with `cntc lint`. The human-readable
standard and change process live in [docs/CNTC-REQUIREMENTS.md](docs/CNTC-REQUIREMENTS.md)
and [docs/CNTC-GOVERNANCE.md](docs/CNTC-GOVERNANCE.md).

Every run/verdict writes `scorecard.md`, `scorecard.html`, and the `verdict` block in
`results.json`; the combined PDF report and the dashboard both surface it.

---

## The engine (`upfbench`): four test suites

| # | Suite | What it does |
|---|-------|--------------|
| 1 | **performance** | throughput (NDR/PDR) · latency/jitter · burst · multi-flow · bidirectional (UL+DL), single tunnel, max rate |
| 2 | **load** | many UEs at once (UPF-isolated): pfcpsim sessions + per-TEID GTP-U → capacity, aggregate + per-UE throughput, latency-under-load |
| 3 | **pfcp** | N4 conformance (3GPP TS 29.244): association + session establish/modify/delete + error handling |
| 4 | **n3neg** | N3 data-plane negative/robustness: malformed GTP-U, unknown TEID, PSC (0x85) ext-header, crash detection + recovery |

```bash
./scripts/bootstrap_fresh_vm.sh                   # one-time: deps + pfcpsim (fresh server)
upfbench doctor                                   # check the box is ready (deps, TRex, hugepages, VFs…)
upfbench list                                     # show suites + test cases
upfbench run --config configs/sdcore-bess-trex.yaml --suite all   # run a campaign (DPDK)
upfbench dashboard                                # live web UI over campaigns/  (http://<host>:8050)
```

**Validated UPFs:** SD-Core BESS-UPF (DPDK / AF_XDP / CNDP / AF_PACKET) and OAI-UPF
(simpleswitch). Open5GS-UPF adapter present. One adapter per UPF makes them comparable.

### Architecture
```
                              configs/<campaign>.yaml
                                        |
 ENGINE (upfbench):  runner → [ adapter | control | traffic | metrics ] → results.json → report + dashboard
                                  ↑          ↑          ↑
                         per-UPF plugin  pybess/pfcpsim  trex/testpmd/tcpreplay
                                        |
 UMBRELLA (cntc):     verdict.evaluate(results, standards/<profile>.yaml) → verdict block + scorecard.md
```
- **upfbench/adapters/**: one plugin per UPF (`sdcore_bess`, `oai_upf`, `open5gs_upf`).
- **upfbench/control/**: `pybess` (Suite 1 white-box) or `pfcpsim` (portable PFCP/N4, Suites 2/3/4).
- **upfbench/traffic/**: `trex` (DPDK/XDP/CNDP GTP-U, multi-TEID, bidirectional), `tcpreplay`, `testpmd`.
- **upfbench/suites/**: the four test categories; each a folder of test cases.
- **cntc/standards/**: requirement catalogs · **cntc/verdict/**: the grading engine · **cntc/certification/**: scorecards.
- **dashboard/**: live Plotly Dash web UI · **third_party/pfcpsim/**: vendored omec-project/pfcpsim.

### Dashboard
A live, view-only **Plotly Dash** app over `campaigns/` (`make dashboard`). Pages:
Overview, **Control plane**, UPFs, Runs, Compare, Findings, Test catalog, Methodology, the
Control-plane page and the Test catalog now cover **both** the control plane (57 tests across seven
NFs) and the user plane, and each run surfaces its CNTC scorecard + certificate. See
[dashboard/README.md](dashboard/README.md).

---

## The control-plane engine (`cpbench`): per-NF, standard-anchored

`cpbench` certifies the **5G core control plane**: it drives each network function over its real
interfaces and grades it against that NF's own 3GPP spec, both its **stage-3 protocol** spec and
its **SCAS** security-assurance spec.

| NF | Protocol | SCAS | Driven via |
|----|----------|------|-----------|
| **AMF** | TS 24.501 (NAS) · TS 38.413 (NGAP) · TS 33.501 (5G-AKA) | TS 33.512 | UERANSIM over N1/N2 + on-the-wire NAS capture |
| **SMF** | TS 29.502 (Nsmf) · TS 29.244 (N4/PFCP) | TS 33.515 | PDU sessions via UERANSIM + N4 capture |
| **NRF** | TS 29.510 (Nnrf) | TS 33.518 | SBI client (HTTP/2 + TLS + OAuth2) |
| **AUSF** | TS 29.509 (Nausf) | TS 33.516 | SBI client + transitive via registration |
| **UDM** | TS 29.503 (Nudm) | TS 33.514 | SBI client + transitive via registration |
| **UDR** | TS 29.504 / 29.505 (Nudr) · TS 29.519 (policy data) | TS 33.501 | SBI client, each data type fetched directly |
| **PCF** | TS 29.507 (Npcf AM) · TS 29.512 (Npcf SM) | TS 33.501 | SBI client, association lifecycle driven directly |

- **Two certification levels.** **Level 1, Conformance & Observable Security** (57 tests, 34
  essential) is **shipped**: everything provable with a spec-compliant peer + observation
  (registration, 5G-AKA, NAS ciphering/integrity, no-auth-bypass, SBI TLS/OAuth2, malformed →
  reject). **Level 2, Adversarial Robustness** (20 tests) is on the roadmap as data-only catalogs.
- **Same gate as the UPF:** an NF earns a certificate only when **all its essential tests pass**;
  a case that can't be judged on a deployment is **`na`** → `INCOMPLETE`, **never a silent pass**.
- **Runs against your live core**, on both deployment styles, **docker-compose** (`free5gc`
  adapter) and **Kubernetes** (`free5gc_k8s` adapter, addresses resolved live via `kubectl`), 
  with an in-cluster or host-side UERANSIM UE. Bring your own free5GC / Open5GS / OAI.
- **Tool stack (open, arm's-length):** UERANSIM (gNB+UE, AGPL, fetched & built, not bundled),
  our own SBI client, `tcpdump`+`tshark` for N2/N4 wire capture, `pfcpsim` for N4.

```bash
./scripts/bootstrap_cpbench.sh                    # deps + UERANSIM (fresh server)
make cp-configure                                 # wizard -> control-plane campaign config
make cp-doctor CONFIG=configs/free5gc-cp.yaml     # preflight -> READY
make cp-run    CONFIG=configs/free5gc-cp.yaml NF=all
cntc certify   campaigns/<id>/results.json --profile amf-conformance
```

The full design, the test catalog, and the deployment runbooks are in
[docs/CPBENCH-IMPLEMENTATION.md](docs/CPBENCH-IMPLEMENTATION.md),
[docs/CNTC-LEVELS-AND-USAGE.md](docs/CNTC-LEVELS-AND-USAGE.md),
[docs/CNTC-TECHNICAL-REFERENCE.md](docs/CNTC-TECHNICAL-REFERENCE.md) and
[docs/FREE5GC-K8S-DEPLOY.md](docs/FREE5GC-K8S-DEPLOY.md).

---

## The RAN engine (`ranbench`): per product class, standard-anchored

`ranbench` certifies a **split gNB**. It does not treat the gNB as one thing: 3GPP TS 33.523
defines separate security product classes for a disaggregated base station, so CNTC certifies
each one separately, with its own catalog and its own certificate, plus a composite gNB verdict
that passes only when all three do.

| Product class | Interfaces | Specs | Level-1 tests |
|----|----------|------|-----------|
| **O-CU-CP** | N2 (NGAP) · F1-C (F1AP) · E1 (E1AP) · RRC | TS 38.413 · 38.473 · 38.463 · 38.331 · 33.511 · 33.523 | 30 (16 essential) |
| **O-CU-UP** | E1 (E1AP) · F1-U · N3 (GTP-U) | TS 38.463 · 38.425 · 38.415 · 29.281 · 33.523 | 14 (7 essential) |
| **O-DU** | F1-C (F1AP) · F1-U · the cell | TS 38.473 · 38.425 · 38.331 · 38.321 · 33.523 | 14 (7 essential) |

**Why the CU/DU split matters for testing.** In a split gNB every RRC message crosses F1 wrapped
in an F1AP container (TS 38.473 clause 8.4), so RRC conformance and AS-security activation are
observable on an ordinary IP link. No radio, no PHY decoding, no key material. In a monolithic
gNB the same evidence exists only over the air and is ciphered after security activation.

**How a run works.** One UE attach produces the evidence for all 58 tests. `ranbench` starts the
three products in order, runs the attach (cell search, RACH, RRC setup, registration, PDU
session, user traffic), stops everything so the captures flush, then decodes the per-interface
pcaps the RAN wrote itself and grades every requirement against it.

**Bring your own RAN and core.** The RAN is the subject of the certificate and the 5G core is a
peer, so neither is installed by CNTC. Supporting a different RAN means one new adapter under
`ranbench/adapters/` plus a campaign config. The catalogs, the test cases and the grading are
unchanged.

```bash
./scripts/bootstrap_ranbench.sh                   # deps + the OAI UE simulator (external)
make ran-configure                                # wizard: derives the UE radio params from the O-DU
make ran-doctor CONFIG=configs/ocudu-ran.yaml     # preflight, must say READY
make ran-run    CONFIG=configs/ocudu-ran.yaml TARGET=all CAMPAIGN=MY-RAN-001
make ran-certify CAMPAIGN=MY-RAN-001 TARGET=cuup  # certificate only if every essential passed
# for the OAI CU/DU stack instead: OAI_GNB=1 ./scripts/bootstrap_ranbench.sh, then CONFIG=configs/oai-ran.yaml
```

**Verified against two independent CU/DU stacks.** **OCUDU** (the Linux Foundation CU/DU
project, srsRAN lineage) as a three-process split against free5GC on Kubernetes, with an OAI
nr-UE over a ZeroMQ virtual radio: 34 pass, 5 fail, 19 na, the O-DU certified. **OAI** (branch
`develop`) as the same three product classes over rfsim, driven by `configs/oai-ran.yaml`: 39
pass, 5 fail, 14 na. 53 of 58 verdicts are identical between the two stacks, and the five
failures are the same on both (NEA0 null ciphering, no IPsec on N2/N3), which is the cross-stack
confirmation that they are real. Both are reproducible: repeated runs give byte-identical
verdicts. The rig, and the parameters that must agree between the O-DU and the UE, are documented
in [docs/RANBENCH-RIG.md](docs/RANBENCH-RIG.md).

### A second RAN cut: the L1/L2 split over nFAPI (PNF / VNF)

The CU/DU split above cuts the gNB at F1. `ranbench` also certifies the **L1/L2 split**, which
cuts it lower, at the FAPI boundary between the physical layer and the MAC. This is a different
kind of certificate, and the difference is stated on it: 3GPP defines no product class for an L1
and there is no SCAS for this split, so the two products and their requirements come from the
**Small Cell Forum** instead.

| Product class | Interface | Specs | Level-1 tests |
|----|----------|------|-----------|
| **PNF** (L1 / PHY) | nFAPI P5 (SCTP) · P7 (UDP) | SCF222 (10th ed.) · SCF225 | 26 (14 essential) |
| **VNF** (L2 driver) | nFAPI P5 · P7, the other end | SCF222 · SCF225 | 15 (9 essential) |

A **PNF or VNF certificate asserts conformance to an SCF interface specification in that role.
It is not a 3GPP SCAS product class certificate**, and that sentence is carried in the catalog,
the scorecard and the certificate. The physical layer (TS 38.211 to 38.214) is deliberately not
anchored: the nFAPI wire carries what the PHY reports, not what it transmitted.

**One capture, two products.** The PNF and the VNF are the two ends of one interface, so a single
nFAPI capture is read in both directions: the PNF -> VNF messages judge the PNF, the VNF -> PNF
messages judge the VNF. This is the same arrangement F1-C uses for the O-DU and the O-CU-CP.

**nFAPI has its own decoder, and it must.** Every other RAN interface is decoded with tshark.
5G NR nFAPI reuses the LTE message ids with different meanings, and Wireshark 3.6.2 renames NR
messages to their LTE homonyms rather than failing (`SLOT.indication` reads back as
`SUBFRAME_INDICATION`, seven of nine wrong). `ranbench/observers/nfapi.py` reads the message id
from the header bytes against the SCF222 set, and was validated against the bridge's own message
record.

```bash
make ran-doctor CONFIG=configs/fapi-split.yaml            # must say READY, rfsim port included
make ran-run    CONFIG=configs/fapi-split.yaml TARGET=all CAMPAIGN=FAPI-001
make ran-certify CAMPAIGN=FAPI-001 TARGET=vnf             # or TARGET=pnf
```

**Verified against a cross-vendor stack**: an OAI L1 (PNF) bridged by xFAPI (VNF, coRAN Labs) to
an OCUDU L2, with an OCUDU CU and an OAI UE against free5GC. The reference run grades the **VNF
PASS** (9 of 9 essential, certificate issued) and the **PNF FAIL** on one ordering requirement:
the PHY emitted P7 slot traffic 49 ms before it answered `START.response`. The rig, the build
steps and the hard-won facts are in [docs/FAPI-SPLIT-RIG.md](docs/FAPI-SPLIT-RIG.md).

**Also certified as pure OAI (PNF and VNF both OAI):** the same two catalogs, observer and
suites judge a stack where both ends of nFAPI are OAI `nr-softmodem`, the VNF carrying L2 and L3
and terminating N2/N3 to the core itself, so there is no separate L2, no CU and no xFAPI. Two
processes instead of four, and no DPDK. Only a two-process adapter and driver were added; the
interface evidence path is unchanged.

```bash
OAI_GNB=1 ./scripts/bootstrap_ranbench.sh                 # builds nr-softmodem + the UE (once)
make ran-oai-fapi-doctor                                  # or: ran-doctor CONFIG=configs/fapi-oai.yaml
make ran-oai-fapi-run TARGET=all CAMPAIGN=FAPI-OAI-001
make ran-certify CAMPAIGN=FAPI-OAI-001 TARGET=vnf         # or TARGET=pnf
```

The **PNF is the same OAI L1 binary as the cross-vendor stack**, and it reproduces the identical
finding: P7 slot traffic 48 ms before `START.response`, PNF essential 12 pass / 1 fail / 1 na in
both. So the same PHY defect is confirmed under two different harnesses and two different VNFs
driving it. On a shared VM without CPU isolation the VNF's slot-timing requirements grade `na`
(the host descheduled the stack, which cannot be attributed to the VNF); they are judgeable on an
isolated, CPU-pinned rig or a real radio.

---

## Docs
- [docs/RANBENCH-RIG.md](docs/RANBENCH-RIG.md): the CU/DU RAN rig, building the stack and the UE, and the parameters that must agree.
- [docs/FAPI-SPLIT-RIG.md](docs/FAPI-SPLIT-RIG.md): the L1/L2 split rig (PNF / VNF over nFAPI), building the four-component cross-vendor stack, and the developer's guide to running it.
- [docs/config-reference.md](docs/config-reference.md): which config fields to change per UPF/mode.
- [docs/benchmarking-guide.md](docs/benchmarking-guide.md): **start here**: run, pick suites, reproduce baselines.
- [docs/dpdk-testing-guide.md](docs/dpdk-testing-guide.md): kernel-bypass (DPDK/AF_XDP/CNDP) testing with TRex.
- [docs/fresh-vm-setup.md](docs/fresh-vm-setup.md) · [docs/RUNBOOK.md](docs/RUNBOOK.md).

## Status
- **Engine:** all four suites validated end-to-end on SD-Core BESS-UPF (DPDK) and OAI-UPF
  (simpleswitch); the n3neg suite found a **real remote-DoS crash** (malformed N3 GTP-U
  segfaults bessd in `GtpuDecap::ProcessBatch`).
- **Verdict layer (M0-M4 complete):**
  - **M0** conformance profile, CF-01..05 + NT-01..03 graded, essential gate, `verdict` in
    `results.json`, `cntc verdict` re-grades past runs.
  - **M1** scorecard everywhere, `scorecard.md` + `scorecard.html` (dep-free), a CNTC-verdict
    section in the combined PDF (`all.tex.j2`), and a verdict badge on the dashboard campaign page.
  - **M2** `--suite conformance` (= pfcp + n3neg) so robustness is in the certification run;
    crash-undetectable adapters grade `na`, never a silent pass.
  - **M3** performance profile, baseline-relative grading (`peak_ndr_mpps`, `p99_us`), with
    loud warnings when `baseline`/`rig_class` are missing (never a faked performance PASS).
  - **M4** governance, `cntc lint` catalog linter, [requirements rulebook](docs/CNTC-REQUIREMENTS.md)
    + [governance note](docs/CNTC-GOVERNANCE.md). **14/14 unit tests pass** (`tests/test_verdict.py`).
- **RAN (`ranbench`), Stage 3, shipped:** 58 Level-1 tests across O-CU-CP, O-CU-UP and O-DU,
  verified against a live **OCUDU** split gNB with free5GC and an OAI UE. A full run grades every
  test from one attach. Measured over three runs, one per product class: **34 pass, 5 fail,
  19 not applicable**, with the **O-DU certified** (7 of 7 essential) and the O-CU-CP and
  O-CU-UP failing on security.
  - The finding is **algorithm downgrade**: the security policy signals confidentiality as
    *required*, and the algorithm actually selected is the null cipher **NEA0**, while integrity
    gets a real algorithm (128-NIA2). It is read from the E1AP Bearer Context Setup on the wire,
    not inferred, and it is confirmed independently on two product classes over two interfaces
    (`CUCP-SEC-02`, `CUCP-SEC-03` on F1, `CUUP-SEC-01` on E1).
  - **N2 and N3 carry no IPsec** and are reported as exposed. F1-C, F1-U and E1 run between
    loopback addresses on this single-host rig, so their transport protection is not observable
    here and records `na` naming the deployment change that would allow it to be certified.
  - Requirements that cannot be judged are never promoted. A procedure the core never asked for,
    a path the rig cannot interrupt, and an interface that never leaves the host all record `na`
    with the reason, and a class with an unjudged essential is INCOMPLETE, never certified.
- **RAN L1/L2 split (`ranbench`), nFAPI, shipped:** 41 Level-1 tests across the **PNF** (26) and
  the **VNF** (15), anchored to SCF222 and SCF225, graded from one nFAPI capture read in both
  directions. Verified against a cross-vendor stack: an OAI L1, xFAPI as the bridge, an OCUDU L2
  and CU, and an OAI UE against free5GC. The reference run certifies the **VNF** (9 of 9
  essential) and fails the **PNF** on `PNF-P5-07`, the PHY starting its slot loop 49 ms before
  answering `START.response`. A dedicated nFAPI decoder reads message ids from the header bytes,
  because Wireshark 3.6.2 mislabels every NR message as its LTE homonym. These certificates state
  that they are SCF interface conformance, not 3GPP SCAS product-class certificates.
- **Control plane (`cpbench`), Stage 2, shipped:** 57 Level-1 tests across AMF/SMF/NRF/AUSF/UDM/UDR/PCF,
  verified against a live **free5GC** on both **docker-compose** and **Kubernetes**. On docker,
  AMF/AUSF/UDM certify; on Kubernetes, AMF certifies (full in-cluster registration + 5G-AKA + NAS
  security + negative attach) and the SBI checks surface real findings (no-TLS / token-less
  discovery), the framework reports the gap, it never rubber-stamps.
- **Next:** metric-key drift check in `cntc lint`; RAN Level 2 (adversarial catalogs already
  ship as data at `cntc/standards/*-adversarial.yaml`); **Stage 4 (SMO)** and **Stage 5 (RIC)**
  under the same umbrella (see the [Objective](#objective) roadmap).
