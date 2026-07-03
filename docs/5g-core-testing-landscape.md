---
title: "5G Core Conformance & Performance Testing — Landscape"
subtitle: "Standards, organizations, tools, and where upfbench fits"
author: "coRAN Labs"
date: "June 2026"
geometry: margin=2cm
---

# 1. The big picture

There is **no single "5G core" certification stamp** the way mobile devices are certified.
Formal 3GPP **conformance + certification** programs target **devices (UEs)** and **base
stations** — not core network functions. For the **5G core**, correctness is validated
through three overlapping activities:

1. **Protocol conformance** — does each network function (NF) obey the 3GPP stage-3 protocol
   spec for its interface (e.g. N4/PFCP = TS 29.244, SBI = TS 29.500-series)?
2. **Interoperability** — does vendor A's NF work with vendor B's? (ETSI Plugtests, GSMA.)
3. **Load / performance / NFV** — capacity, throughput, latency under realistic subscriber
   load (ETSI NFV-TST, RFC 2544).

`upfbench` is an **open-source** tool that covers a slice of (1) and (3) for the **UPF** over
the **N4 interface** — the same job commercial N4 emulators and core load testers do, using
open components (`pfcpsim` + `tcpreplay`) instead of paid systems.

# 2. Standards & specifications

| Spec | Body | What it defines | Used for |
|---|---|---|---|
| **TS 29.244** | 3GPP | PFCP — the SMF–UPF **N4** control protocol (PDR/FAR/QER/URR, association, session est/mod/del) | **upfbench Suite 3** asserts UPF compliance here |
| TS 29.500-series | 3GPP | **SBI** — HTTP/2 REST APIs between NFs (Namf, Nsmf, Nudm, …) | SBI conformance (separate area) |
| TS 23.501 / 23.502 | 3GPP | 5G system architecture & procedures (stage 2) | Defines the behaviour being tested |
| TS 38.523-1 | 3GPP/ETSI | **UE** conformance test spec (5GS) | Device certification (RAN5) |
| TS 38.141 | 3GPP | **Base station** conformance | gNB testing |
| ETSI GS NFV-TST 009 | ETSI NFV ISG | NFV data-plane **performance** methodology | **upfbench Suite 1** methodology |
| RFC 2544 / RFC 8219 | IETF | Throughput/latency benchmarking (NDR/PDR) | **upfbench Suite 1** methodology |

# 3. Organizations

| Organization | Role in testing | Scope |
|---|---|---|
| **3GPP** (RAN5, CT4, SA5) | Writes the protocol & conformance specs | RAN5 = UE conformance; CT4 = core protocols (N4, SBI); SA5 = management |
| **GCF** (Global Certification Forum) | Mandates 3GPP test cases to **certify devices** | Devices / IoT — *not core NFs* |
| **PTCRB** | North-American device certification | Devices |
| **GSMA** | Interoperability testing & network requirements | VoLTE/5G/IMS interop, network slicing templates |
| **ETSI TC INT** | Core Network **and Interoperability Testing** | Core NF interop, test specs |
| **ETSI Plugtests** | Multi-vendor interoperability events | Hands-on interop validation |
| **ETSI NFV ISG (TST WG)** | NFV performance & test methodology | Virtualised NF performance |
| **O-RAN Alliance / OTIC labs** | Open RAN conformance & interop | RAN (adjacent to core) |

# 4. Commercial test tools

| Tool / vendor | What it provides | Relevant to |
|---|---|---|
| **GL Communications — MAPS 5G N4 Emulator** | Emulates SMF & UPF per TS 29.244; regression, edge-case, **conformance** & performance on N4 | N4/PFCP conformance (same as our Suite 3) |
| **Emblasoft (Evolver)** | Simulates the SMF toward a UPF per TS 29.244; N4 functional + load | N4 conformance + load |
| **Valid8** | Per-NF **conformance test suites** (e.g. AMF, SMF) | SBI + N-interface conformance |
| **Spirent Landslide** | Emulates millions of subscribers, control + data plane; SA/NSA | Core **load / performance** (our Suites 1–2) |
| **Keysight LoadCore** | Emulates AMF/SMF/UPF; tens of thousands of events/s, hundreds of Gbps; node + E2E | Core **load / performance**; validated 100 Gbps UPF (NEC) |
| **Rohde & Schwarz** | 3GPP **base-station** conformance | RAN side |
| **Vendor self-declarations** (e.g. Cisco UPF "N4 3GPP Compliance" doc) | Per-release statement of which TS 29.244 IEs/procedures are supported | N4 conformance evidence |

# 5. Open-source tools

| Tool | What it provides |
|---|---|
| **pfcpsim** (omec-project) | Emulates an SMF over N4 (PFCP); drives association + session procedures — *the N4 driver upfbench uses* |
| **TRex** (Cisco) | High-rate traffic generator (DPDK) for UPF throughput testing |
| **iperf / iperf3** | Uplink/downlink peak-rate, TCP/UDP benchmarking |
| **tcpreplay** | Replays crafted GTP-U pcaps on N3 — *the generator upfbench uses* |
| **UERANSIM / gNBsim** | Emulated gNB + UE for end-to-end 5G data-path testing |
| **Open5GS / free5GC / OAI / SD-Core** | Open-source 5G cores (the systems under test) |

# 6. Where upfbench fits

| upfbench suite | Equivalent commercial category | Standard | What it does |
|---|---|---|---|
| **Suite 1 — performance** | Spirent Landslide / Keysight LoadCore | RFC 2544, NFV-TST 009 | Throughput (NDR/PDR), latency/jitter, burst, multi-flow on N3→UPF→N6 |
| **Suite 2 — load** | Spirent Landslide / Keysight LoadCore | — | Session capacity, per-UE throughput, latency under N UEs |
| **Suite 3 — pfcp conformance** | GL MAPS / Emblasoft / Valid8 | **TS 29.244** | N4 procedure conformance: association, session establish/modify/delete, unknown-SEID rejection |

**Honest scope vs. commercial conformance suites:** upfbench Suite 3 covers the N4
**procedures** (happy-path + unknown-SEID negative). Commercial suites additionally do
exhaustive **IE-level and malformed-message** validation with exact Cause-code checks
(the "raw-PFCP harness" upfbench's CF-05 notes as future work). upfbench's value is being
**open, reproducible, and UPF-agnostic** (one adapter per UPF), at near-zero cost.

# 7. Sources

- 3GPP RAN WG5 — <https://www.3gpp.org/3gpp-groups/radio-access-networks-ran/ran-wg5>
- 3GPP / GCF 5G device certification — <https://www.3gpp.org/news-events/partner-news/gcf-5g>
- ETSI TS 138 523-1 (5GS UE conformance) — <https://www.etsi.org/deliver/etsi_ts/138500_138599/13852301/15.01.00_60/ts_13852301v150100p.pdf>
- Keysight — 5G conformance testing process & coverage — <https://www.keysight.com/blogs/en/inds/2020/05/29/5g-testing-conformance-testing-process-and-coverage-matter>
- Rohde & Schwarz — 3GPP base-station conformance — <https://www.rohde-schwarz.com/us/solutions/wireless-communications-testing/mobile-network-infrastructure-testing/3gpp-base-station-conformance-testing/3gpp-base-station-conformance-testing_256119.html>
- GL Communications — MAPS 5G N4 Emulator — <https://www.gl.com/5G-N4-interface-emulator-using-maps.html>
- Emblasoft — N4 interface testing — <https://emblasoft.com/blog/comprehensive-testing-and-simulation-of-the-n4-interface-is-key-to-creating-multi-vendor-cloud-native-5g-networks>
- Cisco — N4 Interface 3GPP Compliance (PDF) — <https://www.cisco.com/c/en/us/td/docs/wireless/ucc/upf/2021-01/b_ucc-5g-upf-config-and-admin-guide_2021-01/m_5g-upf-3gpp-dec-2018-n4-spec-compliance.pdf>
- Valid8 — 5G AMF Conformance Test Suite — <https://www.valid8.com/datasheets/5g-amf-conformance-test-suite>
- PFCP (overview) — <https://en.wikipedia.org/wiki/PFCP>
- ETSI TC INT (Core Network & Interoperability Testing) — <https://www.etsi.org/committee/1401-int>
- GSMA Interoperability Testing — <https://www.gsma.com/services/interoperability-testing/>
- Spirent Landslide — <https://www.spirent.com/products/core-network-test-5g-lte-ims-wifi-diameter-landslide>
- Keysight LoadCore (datasheet PDF) — <https://www.keysight.com/us/en/assets/3120-1180/data-sheets/LoadCore-5G-Core-Testing.pdf>
- Open-source tools to benchmark 5G core UPF (Medium) — <https://medium.com/@googler_ram/open-source-tools-to-benchmark-5g-core-upf-bfc46f05eb78>

*Compiled June 2026. Web sources accessed via search; specification section numbers per 3GPP Release 16/17.*
