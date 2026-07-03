# CNTC — Cloud Native Telecom Certification

**CNTC** (*Cloud Native Telecom Certification*) is an open framework that tests open-source
telecom network functions and turns the raw results into a **graded, standards-aligned verdict
and certificate** — modelled on the CNTI (Cloud Native Telecom Initiative) idea of
*a published standard → automated tests → a pass/fail certification gate → a scorecard*.

## Objective

Give the whole cloud-native telecom stack **one reproducible “test → grade → certify” pipeline**:
one requirement catalog, one verdict engine, one scorecard — so any open-source NF can be measured
against a versioned standard on any rig and earn (or be refused) a certificate.

We are building it **outward from the data plane**, one layer at a time:

| Stage | Scope | Status |
|-------|-------|--------|
| 1 | **UPF** — 5G user plane over N3/N4 (performance, load, PFCP conformance, N3 robustness) | ✅ **available today** |
| 2 | **5G Core control plane** — AMF / SMF / … (NGAP · PFCP · SBI) | 🔜 next |
| 3 | **RAN** — gNB · O-CU / O-DU / O-RU | 🗺️ roadmap |
| 4 | **SMO** — Service Management & Orchestration | 🗺️ roadmap |
| 5 | **RIC** — Near-RT / Non-RT RIC · xApps / rApps (E2 · A1 · O1) | 🗺️ roadmap |
| 6 | **Full O-RAN ecosystem** — end-to-end certification across the stack | 🎯 vision |

Everything below is **Stage 1 (UPF)** — in production today. The engine that measures
(`upfbench`) and the umbrella that judges (`cntc`) are deliberately decoupled, so each later
stage adds a new engine + a requirement catalog **without touching the grading core**.

CNTC has two layers:

| Layer | What it is | Where |
|-------|-----------|-------|
| **Engine** (`upfbench`) | The UPF test engine — drives any open-source 5G UPF over N3/N4 and measures it (performance, load, PFCP conformance, N3 robustness). | [`upfbench/`](upfbench/) |
| **Verdict** (`cntc`) | The umbrella: a **requirement catalog** per profile (`cntc/standards/*.yaml`) + a pure **verdict engine** that grades results and emits a **scorecard** + certificate. | [`cntc/`](cntc/) |

The engine measures; the umbrella judges. They're decoupled — `cntc.verdict` grades the
serialized `results.json`, so it can also **re-grade any past campaign** without re-running it.

---

## Automation — one `make` entrypoint

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
| `make dashboard` · `dashboard-bg` · `dashboard-stop` | live Plotly dashboard: foreground · tmux · stop |
| `make profiles` · `make lint` · `make test` | list profiles · validate catalogs · run verdict unit tests |
| `make k8s-deploy` · `make k8s-run` · `make k8s-clean` | dashboard in Kubernetes · in-cluster run Job · teardown |

Override the vars inline, e.g. `make run CONFIG=configs/sdcore-bess.yaml CAMPAIGN=MY-UPF-001`.

---

## Quick start

```bash
pip install -e .                                   # installs both `cntc` and `upfbench`

# 1) engine only — measure a UPF (unchanged behavior, now ends with a scorecard)
upfbench run --config configs/sdcore-bess-trex.yaml --suite pfcp      # PFCP conformance
upfbench run --config configs/sdcore-bess-trex.yaml --suite n3neg     # N3 robustness

# 2) umbrella — list profiles / grade / re-grade
cntc profiles                                      # available requirement profiles
cntc run --config configs/sdcore-bess.yaml --suite pfcp --profile conformance
cntc verdict campaigns/<id>/results.json           # re-grade a past run -> scorecard
cntc verdict campaigns/<id>/results.json --profile performance --baseline campaigns/<ref>/results.json
```

Every run/verdict writes `campaigns/<id>/scorecard.md` and embeds a `verdict` block in
`results.json`. Exit code is non-zero unless the result is `PASS` (CI-friendly).

### What a scorecard looks like
```
  CNTC VERDICT  —  profile: conformance  (catalog v0.1.0)
   [PASS] * CF-01  PFCP association setup / release    status == pass
   ...
   [FAIL] * NT-02  Malformed GTP-U robustness (no crash)   status == fail
   essential gate (all): 7 passed / 1 failed / 0 na  (of 8)
  RESULT:  FAIL  ✗
```

---

## The verdict model (how grading works)

- **Profiles** (`cntc/standards/<profile>.yaml`) define the standard, split by portability:
  - **`conformance`** — *hardware-independent*, binary pass/fail: PFCP/N4 conformance
    (3GPP TS 29.244) + N3 GTP-U robustness. Certifiable on **any** rig. **8 essential tests.**
  - **`performance`** — *hardware-dependent*: throughput/latency graded **relative to a
    baseline** on the same rig class (never absolute Mpps — af_packet on a shared VM is ~⅕ of
    dedicated DPDK). Needs a `baseline:` in the config.
- **Weight classes:** `essential` (gates certification) · `normal` · `bonus`.
- **Verdict rules** (per test, in the catalog): `status_pass`, `{kind: metric,…}` (absolute),
  `{kind: baseline_rel,…}` (relative). A test that didn't run / errored / is missing a metric
  is **`na`** — *never silently promoted to pass*.
- **Result:** `PASS` (all essentials passed) · `FAIL` (an essential failed) · `INCOMPLETE`
  (an essential didn't run). The gate can also be `min_essential: N` (CNTI-style "15 of 19").

To change the bar, edit the YAML catalog — no engine code changes. Add a profile by dropping
a new `cntc/standards/<name>.yaml`. Validate catalogs with `cntc lint`. The human-readable
standard and change process live in [docs/CNTC-REQUIREMENTS.md](docs/CNTC-REQUIREMENTS.md)
and [docs/CNTC-GOVERNANCE.md](docs/CNTC-GOVERNANCE.md).

Every run/verdict writes `scorecard.md`, `scorecard.html`, and the `verdict` block in
`results.json`; the combined PDF report and the dashboard both surface it.

---

## The engine (`upfbench`) — four test suites

| # | Suite | What it does |
|---|-------|--------------|
| 1 | **performance** | throughput (NDR/PDR) · latency/jitter · burst · multi-flow · bidirectional (UL+DL) — single tunnel, max rate |
| 2 | **load** | many UEs at once (UPF-isolated): pfcpsim sessions + per-TEID GTP-U → capacity, aggregate + per-UE throughput, latency-under-load |
| 3 | **pfcp** | N4 conformance (3GPP TS 29.244): association + session establish/modify/delete + error handling |
| 4 | **n3neg** | N3 data-plane negative/robustness: malformed GTP-U, unknown TEID, PSC (0x85) ext-header — crash detection + recovery |

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
- **upfbench/adapters/** — one plugin per UPF (`sdcore_bess`, `oai_upf`, `open5gs_upf`).
- **upfbench/control/** — `pybess` (Suite 1 white-box) or `pfcpsim` (portable PFCP/N4, Suites 2/3/4).
- **upfbench/traffic/** — `trex` (DPDK/XDP/CNDP GTP-U, multi-TEID, bidirectional), `tcpreplay`, `testpmd`.
- **upfbench/suites/** — the four test categories; each a folder of test cases.
- **cntc/standards/** — requirement catalogs · **cntc/verdict/** — the grading engine · **cntc/certification/** — scorecards.
- **dashboard/** — live Plotly Dash web UI · **third_party/pfcpsim/** — vendored omec-project/pfcpsim.

### Dashboard
A live, view-only **Plotly Dash** app over `campaigns/` (`upfbench dashboard`). Pages:
Overview, UPFs, Runs, Compare, Findings, Test catalog, Methodology. See
[dashboard/README.md](dashboard/README.md). *(Next: surface the CNTC scorecard/verdict per
campaign — see the plan.)*

---

## Docs
- [docs/config-reference.md](docs/config-reference.md) — which config fields to change per UPF/mode.
- [docs/benchmarking-guide.md](docs/benchmarking-guide.md) — **start here**: run, pick suites, reproduce baselines.
- [docs/dpdk-testing-guide.md](docs/dpdk-testing-guide.md) — kernel-bypass (DPDK/AF_XDP/CNDP) testing with TRex.
- [docs/fresh-vm-setup.md](docs/fresh-vm-setup.md) · [docs/RUNBOOK.md](docs/RUNBOOK.md) · [docs/PLAN.md](docs/PLAN.md).

## Status
- **Engine:** all four suites validated end-to-end on SD-Core BESS-UPF (DPDK) and OAI-UPF
  (simpleswitch); the n3neg suite found a **real remote-DoS crash** (malformed N3 GTP-U
  segfaults bessd in `GtpuDecap::ProcessBatch`).
- **Verdict layer (M0–M4 complete):**
  - **M0** conformance profile — CF-01..05 + NT-01..03 graded, essential gate, `verdict` in
    `results.json`, `cntc verdict` re-grades past runs.
  - **M1** scorecard everywhere — `scorecard.md` + `scorecard.html` (dep-free), a CNTC-verdict
    section in the combined PDF (`all.tex.j2`), and a verdict badge on the dashboard campaign page.
  - **M2** `--suite conformance` (= pfcp + n3neg) so robustness is in the certification run;
    crash-undetectable adapters grade `na`, never a silent pass.
  - **M3** performance profile — baseline-relative grading (`peak_ndr_mpps`, `p99_us`), with
    loud warnings when `baseline`/`rig_class` are missing (never a faked performance PASS).
  - **M4** governance — `cntc lint` catalog linter, [requirements rulebook](docs/CNTC-REQUIREMENTS.md)
    + [governance note](docs/CNTC-GOVERNANCE.md). **14/14 unit tests pass** (`tests/test_verdict.py`).
- **Next:** metric-key drift check in `cntc lint`; **Stage 2** — a 5G Core control-plane engine
  under the same umbrella (see the [Objective](#objective) roadmap and [docs/PLAN.md](docs/PLAN.md)).
