---
title: "UPF Benchmark Framework — Project Plan"
author: "coRAN Labs"
---

# UPF Benchmark Framework — Project Plan

*A reusable, portable framework to test any open-source 5G UPF. The user picks a test
suite; the framework runs it and produces a standards-aligned PDF report (RFC 2544 /
RFC 8219 / ETSI NFV-TST 009). SD-Core BESS-UPF first, then OAI / Open5GS / free5GC / eUPF.*

---

## 1. The idea in one picture

The user runs `upfbench`, picks a **test suite** (or several), and gets the matching
report. The report template *is* the specification — the framework's job is to fill it
in automatically.

```
  campaign config (YAML)          the framework runs                  the report (PDF)
 ┌──────────────────────┐        ┌──────────────────────┐           ┌──────────────────────────┐
 │ which UPF            │        │ 1. set up the UPF    │           │ System Under Test        │
 │ which suite          │  ───►  │ 2. install rules/N4  │   ───►    │ Methodology + Catalog    │
 │ hardware/sw facts    │        │ 3. inject traffic    │           │ Results (tables/curves)  │
 │ suite knobs          │        │ 4. measure           │           │ Comparison vs baseline   │
 └──────────────────────┘        │ 5. collect + store   │           │ Reproducibility appendix │
                                 └──────────────────────┘           └──────────────────────────┘
```

This automates the manual benchmark loop we already ran by hand on the SD-Core BESS-UPF,
and makes the per-UPF parts swappable so it extends to other UPFs without rewrites.

---

## 2. The three test suites (the user's choices)

```
  ┌─────────────────────────  upfbench  ─────────────────────────┐
  │  Which test do you want to run?                              │
  │                                                              │
  │   1) UPF Performance   throughput · latency · jitter · burst │
  │   2) Multi-UE Load     N UEs · capacity · per-UE rate · p99  │
  │   3) PFCP Conformance  N4 association + session correctness  │
  │                                                              │
  │   (pick one, several, or 'all')                              │
  └──────────────────────────────────────────────────────────────┘
```

### Suite 1 — UPF Performance
Single tunnel, maximum rate. The dataplane benchmark we already measured.

- **Measures:** throughput (NDR/PDR per frame size), latency avg/p50/p99/p99.9, jitter,
  burst/drain, multi-flow.
- **Needs:** traffic generator + rule install + counters — buildable today with the
  tools we already use (testpmd + pybess).
- **Tests:** TC-01 throughput · TC-02 bidirectional · TC-03 latency/jitter · TC-04 burst
  · TC-08 multi-flow.

### Suite 2 — Multi-UE Load (UPF-isolated)
Many UEs at once. pfcpsim installs **N real PFCP sessions** (one PDR/FAR/QER set per UE);
TRex sends **GTP-U on N distinct TEIDs**. Find the breaking point. No full core needed.

- **Measures:** max concurrent sessions (capacity ceiling); aggregate + per-UE throughput
  (total + fairness/degradation); latency-under-load (how p99/jitter degrade as UE count
  and offered load rise).
- **Needs:** pfcpsim (sessions) + TRex multi-TEID (traffic) + session accounting.
- **Tests:** LT-01 session capacity · LT-02 throughput per-UE · LT-03 latency-under-load.

### Suite 3 — PFCP Conformance (TS 29.244 correctness)
pfcpsim drives each N4 procedure and asserts the UPF responds correctly (pass/fail).
PFCP/N4 is implemented to 3GPP TS 29.244 — pfcpsim already encodes the spec messages/IEs.

- **Measures:** a conformance matrix (per-procedure pass/fail).
- **Needs:** pfcpsim + an assertion engine.
- **Tests:** CF-01 association setup/release · CF-02 session establishment · CF-03
  modification · CF-04 deletion · CF-05 error handling (unknown SEID, missing mandatory
  IE → correct cause code).

**Dependency insight:** Suite 1 runs today with existing tools. Suites 2 **and** 3 both
depend on **pfcpsim** — so integrating pfcpsim lights up two suites at once.

---

## 3. Reuse, don't reinvent

| Layer | Reuse (existing code/spec) |
|---|---|
| N4 / PFCP control + conformance | **omec-project/pfcpsim** — speaks PFCP per 3GPP TS 29.244; emulates SMF/SGW-C; designed to test "various UPF implementations" |
| N3/N6 traffic | **Cisco TRex** + Scapy/STLVM GTP-U builders (multi-TEID for per-UE) |
| Metrics + search algorithm | **RFC 2544 / 8204 / 9004 + ETSI TST009** (zero-loss NDR, PDR tolerance, binary search, 24h soak) |
| Architecture pattern | **VSPERF** switch/traffic-gen-agnostic design |
| White-box telemetry (optional) | Prometheus scrape (eUPF) / pybess `Measure` (BESS) |

Our unique contribution = the **UPF-agnostic adapter layer** that unifies all three axes
over standardized N4 (PFCP) + N3 (GTP-U), which no existing tool does portably.

---

## 4. Project structure

```
upf-benchmark-framework/
├── pyproject.toml
├── README.md
├── third_party/
│   └── pfcpsim/                   # vendored omec-project/pfcpsim (reused)
├── upfbench/
│   ├── cli.py                     # the 3-choice menu + `run --suite ...`
│   ├── config.py                  # campaign YAML loader/validator
│   ├── runner.py                  # orchestrator (the 5-step loop)
│   ├── search.py                  # NDR/PDR binary search (RFC 2544 / TST009)
│   ├── results.py                 # campaign store (results.json)
│   │
│   ├── suites/                    # the three user-facing categories
│   │   ├── performance/           #   Suite 1
│   │   │   ├── tc01_throughput.py
│   │   │   ├── tc02_bidirectional.py
│   │   │   ├── tc03_latency.py
│   │   │   ├── tc04_burst.py
│   │   │   └── tc08_multiflow.py
│   │   ├── load/                  #   Suite 2 (UPF-isolated multi-UE)
│   │   │   ├── lt01_session_capacity.py
│   │   │   ├── lt02_throughput_per_ue.py
│   │   │   └── lt03_latency_under_load.py
│   │   └── pfcp/                  #   Suite 3 (TS 29.244 conformance)
│   │       ├── cf01_association.py
│   │       ├── cf02_establish.py
│   │       ├── cf03_modify.py
│   │       ├── cf04_delete.py
│   │       └── cf05_error_handling.py
│   │
│   ├── adapters/                  # per-UPF plugin (swappable)
│   │   ├── base.py
│   │   └── sdcore_bess.py         # first UPF
│   ├── control/                   # rule install / N4
│   │   ├── base.py
│   │   ├── pybess.py              # Suite 1 fast path
│   │   └── pfcpsim.py             # wraps third_party/pfcpsim → unlocks Suites 2 + 3
│   ├── traffic/                   # generators
│   │   ├── base.py
│   │   ├── testpmd.py             # Suite 1
│   │   └── trex.py                # multi-TEID for Suite 2
│   ├── metrics/
│   │   ├── throughput.py
│   │   ├── latency.py
│   │   ├── sessions.py            # capacity / per-UE accounting (Suite 2)
│   │   └── resource.py            # pidstat/cgroup CPU+mem
│   └── report/
│       ├── render.py
│       └── templates/
│           ├── performance.tex.j2 # = the benchmark report template
│           ├── load.tex.j2        # multi-UE report
│           └── pfcp.tex.j2        # conformance matrix
│
├── configs/
│   └── sdcore-bess.yaml
└── campaigns/                     # outputs (results.json + raw + report.pdf per run)
```

Each suite is a folder of test cases; `adapters / control / traffic / metrics` are shared
plugin pools all suites draw from. Adding a UPF or a generator never touches the suites.

---

## 5. Campaign config (the input)

```yaml
campaign: UPF-BM-2026-001
sut:                              # → fills the report's System-Under-Test section
  cpu: "Intel Xeon 48c"
  nic: "Intel E810-C 25GbE, ice"
  upf_image: "upf-bess:rel-2.4.1@sha256:..."
upf:
  adapter: sdcore_bess
  n3_iface: access
  n6_iface: core

suite: performance              # performance | load | pfcp | all

performance:
  control: pybess               # later: pfcpsim
  generator: testpmd            # later: trex
  frame_sizes: [64,128,256,512,1024,1518]
load:
  ue_counts: [10, 100, 1000, 5000]
  per_ue_rate_mbps: 5
pfcp:
  procedures: [association, establish, modify, delete, error_handling]
```

`upfbench run --config configs/sdcore-bess.yaml` → `campaigns/UPF-BM-2026-001/report.pdf`.

---

## 6. How the suites map to the report template

| Report section | Filled by |
|---|---|
| Executive Summary (headline KPIs) | report generator (computed) |
| System Under Test (HW/SW/topology) | config + adapter |
| Methodology + Test Catalog | the suite's test definitions |
| Traffic Profile | traffic generator settings |
| Results (TC/LT/CF tables, NDR/PDR curve) | metrics collectors |
| Analysis (bottleneck, scaling) | heuristics + notes |
| Comparison vs Baseline | results store (compare campaigns) |
| Reproducibility Appendix (exact commands) | runner auto-captures every command |
| Limitations | notes |

---

## 7. Phased roadmap

- **Phase 0 — Scaffold (DONE):** repo + config + CLI + report templates; pipeline runs
  end-to-end.
- **Phase 1 — Suite 1 Performance on SD-Core af_packet (DONE):** pybess rules + host-side
  GTP-U via **tcpreplay** (no DPDK NIC on this VM; testpmd/TRex deferred to real hardware),
  auto CPU-affinity tuning. TC-01 NDR/PDR, TC-03 latency, TC-04 burst, TC-08 multi-flow.
  TC-02 bidirectional deferred. Validated against the reference af_packet PDF (methodology +
  qualitative behavior match; absolute ~1/5 on this shared single-NIC VM).
- **Phase 2 — Suite 3 PFCP Conformance (DONE):** pfcpsim built + wired; CF-01..05 pass
  (incl. CF-03 modify — the earlier "blocked" assumption was wrong). Conformance matrix.
- **Phase 3 — Suite 2 Multi-UE Load (DONE):** pfcpsim N real sessions + matched per-UE
  GTP-U (tcpreplay multi-flow). LT-01 capacity (5000), LT-02 per-UE throughput +
  forwarding verification, LT-03 latency-under-load.
- **Reporting (DONE):** per-suite PDFs (LaTeX) + baseline comparison.
- **Phase 4 — More UPFs + remaining items (NEXT):** OAI / Open5GS / free5GC / eUPF
  adapters; TC-02 bidirectional (real DL GTP-U); true per-UE fairness via BESS FlowMeasure;
  worker auto-tune (TC-05); TRex generator for DPDK hardware.

See **`docs/RUNBOOK.md`** for setup/run and the detailed remaining-work list.

---

## 8. Decisions

1. **Python** — pybess, the TRex API, and Scapy are all Python.
2. **One CLI, three selectable suites** — suites run alone or together (`--suite all`).
3. **Suite 1 first**, because it is buildable with existing tools and can be validated
   against numbers we already trust before building the unproven parts.
4. **Reuse omec-project/pfcpsim** for the N4/PFCP layer — it already implements the
   3GPP TS 29.244 message set, so we wrap it rather than re-implement PFCP.
