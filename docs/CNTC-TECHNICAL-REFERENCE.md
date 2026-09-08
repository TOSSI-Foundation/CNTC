# CNTC: Complete Technical Reference

**Cloud-Native Telecom Certification (CNTC)**: a reproducible framework that turns 3GPP
specifications into an automated **pass/fail certificate** for a 5G core, covering both the
**user plane** (the UPF) and the **control plane** (AMF/SMF/NRF/AUSF/UDM).

> **Purpose of this document.** A single, self-contained technical description of the whole
> project, architecture, every component, the data flows, the full test catalog, the
> certification logic, deployment modes, and verified results, detailed enough to (a) design
> accurate architecture/flow diagrams for the blog and release video, and (b) independently
> verify what the framework does and claims.

---

## 1. The core idea in one line

**Engines *measure*; the umbrella *judges*.** Two decoupled layers:

| Layer | Package | Role | Output |
|---|---|---|---|
| **Engine** | `upfbench` (user plane), `cpbench` (control plane) | Drive each network function over its real interfaces and record what happened | `campaigns/<id>/results.json` |
| **Umbrella** | `cntc` | Grade any `results.json` against a per-target **requirement catalog**, then issue a certificate | scorecard + certificate |

The umbrella is **pure and data-driven**: it grades whatever test IDs appear in a `results.json`
against a YAML catalog. It has no idea whether the run came from `upfbench` or `cpbench`, both
emit the *same* results schema (`cntc_common`). One grading core certifies the whole core.

```
          MEASURE (engine)                         JUDGE (umbrella)
  ┌────────────────────────────┐          ┌──────────────────────────────┐
  │ cpbench  (AMF/SMF/NRF/…)    │  results │ cntc.verdict.evaluate()      │  scorecard
  │ upfbench (UPF: N3/N4/eBPF)  │ ───────► │   vs standards/<profile>.yaml│ ─────────► certificate
  └────────────────────────────┘  .json   │ cntc.certification.issue()   │  (PASS only)
                                            └──────────────────────────────┘
```

---

## 2. Repository map (what every package is)

```
control_cntc/
├── cpbench/            CONTROL-PLANE engine: drives AMF/SMF/NRF/AUSF/UDM, records results
│   ├── cli.py            `cpbench run|doctor` entrypoint
│   ├── config.py         campaign config model + sudo-aware path expansion
│   ├── doctor.py         preflight: deps, CLIs, UERANSIM build, core up, addressing, subscriber
│   ├── runner.py         orchestrates a run: resolve → provision → drive → grade → scorecards
│   ├── adapters/         talk to a specific core distribution (how to find/inspect the NFs)
│   │   ├── base.py         adapter interface + loader
│   │   ├── free5gc.py       free5GC on docker-compose (via `docker inspect`)
│   │   └── free5gc_k8s.py   free5GC on Kubernetes (via `kubectl`)
│   ├── drivers/          generate the stimulus (traffic/signalling) against an NF
│   │   ├── ueransim.py      real gNB+UE (N1/N2): registration, 5G-AKA, PDU session, N2/N4 capture
│   │   ├── ueransim_k8s.py  in-cluster UE variant (observe logs / drive via kubectl)
│   │   └── sbi_client.py    HTTP/2 + TLS + OAuth2 client for the SBI (Nnrf/Nausf/Nudm/Namf/Nsmf)
│   ├── observers/        decode captured signalling
│   │   └── nas_ngap.py      NAS/NGAP + PFCP decode helpers (security-header, SUCI, PFCP IEs)
│   └── suites/           the test cases themselves, one package per NF
│       ├── base.py         NfTestCase + RunContext (endpoint, sbi, driver, core, observer)
│       ├── registry.py     maps NF → its list of test classes
│       ├── sbi_common.py   SBI checks shared by all NFs (reject-unauth, requires-TLS, malformed)
│       ├── amf/checks.py    14 AMF cases   smf/checks.py  11 SMF cases
│       ├── nrf/checks.py     8 NRF cases   ausf/checks.py  4 AUSF cases
│       └── udm/checks.py     7 UDM cases
│
├── cntc/               THE UMBRELLA, grades results, issues certificates (engine-agnostic)
│   ├── cli.py            `cntc profiles|lint|verdict|certify|run`
│   ├── standards/        the requirement CATALOGS (YAML), the source of truth for grading
│   │   ├── <nf>-conformance.yaml   Level 1 per-NF catalogs (amf/smf/nrf/ausf/udm)
│   │   ├── <nf>-adversarial.yaml   Level 2 roadmap catalogs (amf/smf/nrf/ausf)
│   │   ├── conformance.yaml         universal UPF conformance profile
│   │   ├── upf-ebpf.yaml            optional eBPF/XDP dataplane-assurance profile
│   │   └── cp-performance.yaml      non-gating signalling performance
│   ├── verdict/evaluate.py   pure function: (suites, catalog) → verdict (per-test + gate + result)
│   └── certification/
│       ├── scorecard.py       render verdict → console / Markdown / HTML
│       └── certificate.py     issue a certificate iff the essential gate is all-PASS
│
├── cntc_common/        THE SHARED SCHEMA, Store / SuiteResult / TestResult / results.json
│   └── results.py
│
├── upfbench/           USER-PLANE engine (sibling; same results schema)
│   ├── adapters/         per-UPF: free5gc_upf, eupf (eBPF/XDP), open5gs_upf, oai_upf, sdcore_bess
│   ├── traffic/          traffic generators: trex, testpmd, tcpreplay
│   ├── control/          N4/PFCP driving: pfcpsim, pybess
│   ├── suites/           performance (TC), load (LT), pfcp (CF), n3neg (NT), ebpf (XDP)
│   └── runner.py, search.py (RFC 2544/TST009 binary search), metrics/
│
├── dashboard/          LIVE WEB UI over all campaigns (Dash/Plotly; read-only)
│   ├── app.py            multi-page app; nav → overview/runs/catalog/compare/methodology
│   ├── data.py           discovers campaigns/<id>/results.json, normalizes, verified-vs-experimental
│   ├── catalog_data.py   the test-catalog page data (CP built live from cntc/standards/*.yaml)
│   ├── pages/            overview, campaigns (Runs), campaign_detail, catalog, compare, findings…
│   └── export_static.py  render the whole dashboard to static HTML
│
├── configs/            campaign configs (free5gc-cp.yaml=docker, free5gc-k8s-aether.yaml=k8s, eupf.yaml)
├── scripts/            bootstrap_cpbench.sh, cpbench-configure.py (wizard), eupf-certify.sh
├── standards not …     (see cntc/standards) 
├── docs/               this file, TOSSI-RELEASE, VIDEO-DEMO-SCRIPT, levels guide, diagrams/
└── campaigns/          run outputs: <id>/results.json + scorecard.{md,html} + certificate.{md,html,json}
```

---

## 3. The control-plane run pipeline (the main flow to diagram)

A single `cpbench run --config <cfg> --nf <sel> --campaign <id>` executes this pipeline
(`cpbench/runner.py`):

```
 1. LOAD CONFIG        cpbench/config.py  → Campaign{core, drivers, subscribers, sut, target_nfs}
 2. LOAD ADAPTER       adapters/<core.adapter>.py   (free5gc | free5gc_k8s)
 3. RESOLVE LIVE       adapter.nf_endpoint(nf)  → NF SBI host:port, resolved NOW
                         docker: `docker inspect`   k8s: `kubectl get svc … clusterIP`
                       adapter.describe() → which NFs are up, core facts
 4. PROVISION          adapter.provision_subscribers()  → add 5G-AKA SIM to UDM/UDR via WebUI
 5. DRIVE + OBSERVE    for each target NF, run its suite (suites/registry.py):
                         • UERANSIM driver: NG-Setup→5G-AKA→SMC→Register→PDU→ping→release→dereg
                           with tcpdump on N2/N4 + tshark decode (NAS security header, SUCI, PFCP)
                         • SBI client: HTTP/2+TLS+OAuth2 calls to Nnrf/Nausf/Nudm/Namf/Nsmf
                       each test → TestResult{id, status: pass|fail|na|error, metrics, notes}
 6. STORE              cntc_common.Store → campaigns/<id>/results.json  (+ raw/ captures, logs)
 7. GRADE (per NF)     cntc.verdict.evaluate(suites, <nf>-conformance.yaml) → per-NF verdict
                       write scorecard-<nf>.{md,html}
 8. WRITE-BACK VERDICT single NF → that NF's verdict;  multi NF → composite (FAIL if any FAIL)
                       → results.json["verdict"], scorecard.{md,html}
 (then, separately)    cntc certify results.json --profile <nf>-conformance → certificate.{md,html,json}
```

**Key design points for the diagram:**
- **Addresses are resolved at runtime**, never hardcoded, redeploying the core needs zero
  config edits. The config only carries what can't be derived (N2 endpoint, capture interface,
  subscriber credentials).
- **One real registration is cached** process-wide: a `--nf all` run attaches the UE *once*;
  AUSF/UDM read that same attach to prove their **transitive** role (a successful 5G-AKA proves
  the AUSF ran authentication and the UDM served vectors + subscription).
- **`na` is first-class.** A test that cannot be judged on a given deployment records `na` with
  the real reason. `na` is **never** silently promoted to pass.

### 3a. The stimulus/observe topology (control plane)

```
  ┌──────────┐   N1/N2 (NAS/NGAP, SCTP 38412)     ┌───────────────────────────────┐
  │ UERANSIM │──────────────────────────────────►│  AMF ── AUSF ── UDM (5G-AKA)   │
  │ gNB + UE │◄──────────────────────────────────│   │                            │
  └────┬─────┘        tcpdump on N2 → tshark      │  SMF ── N4/PFCP ──► UPF        │
       │  (decode NAS security header, SUCI)      │   │                            │
       │                                          │  NRF (SBI registry)           │
  ┌────┴───────┐   SBI (HTTP/2 + TLS + OAuth2)    └──────────────┬────────────────┘
  │ SBI client │─────────────────────────────────────────────────┘
  └────────────┘   Nnrf / Nausf / Nudm / Namf / Nsmf
```

---

## 4. Adapters and drivers (the pluggable edges)

**Adapters** answer "how do I find and inspect this core?", they are the *only* code that knows
about docker vs Kubernetes.

| Adapter | Core | NF discovery | Liveness | SBI scheme |
|---|---|---|---|---|
| `free5gc` | free5GC docker-compose | `docker inspect <ctr>` → IP | container running | cleartext **HTTP** |
| `free5gc_k8s` | free5GC on Kubernetes | `kubectl get svc … clusterIP:port` | pod phase Running | **HTTPS** (SD-Core/Aether chart) |

The SBI scheme is **auto-detected** per NF port (TLS probe → https, else http), so the exact same
test suite runs correctly against a cleartext-SBI docker core and a TLS-SBI Kubernetes core.

**Drivers** answer "how do I stimulate this NF?"

| Driver | Interface | What it does |
|---|---|---|
| `ueransim` | N1/N2 (SCTP) | native gNB+UE; full attach cycle; captures N2 (NAS) + N4 (PFCP) via tcpdump, decodes with tshark |
| `ueransim_k8s` | N1/N2 | in-cluster UE; observe pod logs or drive via `kubectl exec` |
| `sbi_client` | SBI (HTTP/2) | TLS + OAuth2 client; `request()`, `tls_probe()`, `get_access_token()` |

---

## 5. The full test catalog

### 5.1 Control plane: Level 1 "Conformance & Observable Security" (57 tests, 34 essential)

Every test is anchored to a clause of that NF's own 3GPP spec (protocol + SCAS security).
`●` = **essential** (must PASS to certify); `○` = supporting (informs the scorecard, does not gate).

**AMF, 14 tests, 9 essential** · TS 24.501 (NAS) · TS 38.413 (NGAP) · TS 33.501 (5G-AKA) · TS 33.512 (SCAS)
| ID | ● | Test |
|---|---|---|
| AMF-REG-01 | ● | Initial registration |
| AMF-AUTH-01 | ● | 5G-AKA authentication |
| AMF-AUTH-02 | ● | Security-mode command / complete |
| AMF-DEREG-01 | ● | UE-initiated deregistration |
| AMF-NGAP-01 | ● | NG Setup, gNB↔AMF association |
| AMF-NGAP-02 | ○ | Initial UE message / NAS transport |
| AMF-NGAP-03 | ○ | Initial context setup |
| AMF-NGAP-04 | ○ | UE context release |
| AMF-SEC-01 | ● | NAS integrity after security activation (no cleartext NAS) |
| AMF-SEC-02 | ● | NAS confidentiality (ciphering) after SMC |
| AMF-SEC-04 | ● | RES* failure → registration rejected (no auth bypass) |
| AMF-SEC-06 | ○ | SUPI confidentiality (SUCI used; SUPI not in cleartext) |
| AMF-SEC-07 | ○ | SBI (Namf) requires TLS + valid OAuth2 token |
| AMF-NEG-01 | ● | Malformed NGAP PDU → reject, no AMF crash |

**SMF, 11 tests, 7 essential** · TS 29.502 (Nsmf) · TS 24.501 §6 (NAS-SM) · TS 29.244 (PFCP/N4) · TS 33.515 (SCAS)
| ID | ● | Test |
|---|---|---|
| SMF-SESS-01 | ● | PDU session establishment |
| SMF-SESS-03 | ● | PDU session release |
| SMF-SESS-04 | ○ | Multiple PDU sessions / multi-DNN |
| SMF-SESS-05 | ○ | UE IP address allocation |
| SMF-N4-01 | ● | SMF programs UPF over N4 (PFCP Session Establishment) |
| SMF-N4-03 | ○ | N4 session deletion on PDU release |
| SMF-DP-01 | ● | Data-path verify, traffic forwards through the programmed UPF |
| SMF-SEC-01 | ● | SBI (Nsmf) requires TLS + valid OAuth2 token |
| SMF-SEC-02 | ● | Reject PDU-session create with missing/invalid mandatory IE |
| SMF-SEC-03 | ○ | Session-event logging present |
| SMF-NEG-01 | ● | Invalid session request → reject, no crash |

**NRF, 8 tests, 3 essential** · TS 29.510 (Nnrf) · TS 33.501 §13 (SBI authz) · TS 33.518 (SCAS)
| ID | ● | Test |
|---|---|---|
| NRF-REG-01 | ○ | NFRegister : a peer NF registers its profile |
| NRF-DISC-01 | ○ | NFDiscover by NF type returns correct profiles |
| NRF-DISC-02 | ○ | NFDiscover by service name |
| NRF-SEC-01 | ○ | OAuth2 access-token grant (Nnrf_AccessToken) |
| NRF-SEC-02 | ● | Reject discovery/registration without a valid token → 401/403 |
| NRF-SEC-03 | ● | SBI endpoint requires TLS |
| NRF-NEG-01 | ● | Malformed NFRegister → 400 ProblemDetails, no crash |
| NRF-NEG-02 | ○ | Discover unknown NF type → empty result / 404 |

**AUSF, 4 tests, 3 essential** · TS 29.509 (Nausf) · TS 33.501 (5G-AKA/EAP-AKA') · TS 33.516 (SCAS)
| ID | ● | Test |
|---|---|---|
| AUSF-AUTH-01 | ● | UEAuthentication_Authenticate initiate (5G-AKA) |
| AUSF-AUTH-02 | ○ | Confirm RES*, successful authentication |
| AUSF-SEC-02 | ● | SBI (Nausf) requires TLS + valid OAuth2 token |
| AUSF-NEG-01 | ● | Malformed auth request → reject, no crash |

**UDM, 7 tests, 4 essential** · TS 29.503 (Nudm) · TS 33.501 (SUCI/SIDF) · TS 33.514 (SCAS)
| ID | ● | Test |
|---|---|---|
| UDM-SDM-01 | ● | Subscription data retrieval (Nudm_SDM_Get) |
| UDM-SDM-02 | ○ | Session-management subscription data (for SMF) |
| UDM-UECM-01 | ○ | AMF registration for UE (Nudm_UECM_Registration) |
| UDM-AUTH-01 | ● | Authentication-vector generation (Nudm_UEAuthentication_Get) |
| UDM-SEC-01 | ○ | SUCI de-concealment (SIDF) authorization |
| UDM-SEC-02 | ● | SBI (Nudm) requires TLS + valid OAuth2 token |
| UDM-NEG-01 | ● | Unknown SUPI → 404 ProblemDetails, no crash |

### 5.2 Control plane: Level 2 "Adversarial Robustness & Privileged Interop" (20 tests, roadmap)

Everything that needs a **non-compliant or privileged peer** (forged/replayed/malformed protocol
state, a registered-NF PKI identity). Split by NF: AMF 10, SMF 4, NRF 3, AUSF 3. **Not shipped**: 
catalogs exist at `cntc/standards/<nf>-adversarial.yaml` (version `0.1.0-roadmap`) so the roadmap
is data, not prose. No engine change is needed to ship them later; only the driver stimulus.

### 5.3 User plane (UPF): 16 conformance + performance tests (sibling engine)

| Suite | IDs | What |
|---|---|---|
| Performance | TC-01/02/03/04/08 | RFC 2544/TST009 throughput (NDR/PDR), latency/jitter (RFC 8219), burst, multi-flow |
| Multi-UE Load | LT-01/02/03 | session capacity, per-UE fairness, latency vs UE count |
| PFCP Conformance | CF-01…05 | N4 association/establish/modify/delete/error (TS 29.244) |
| N3 Robustness | NT-01/02/03 | unknown TEID, malformed GTP-U, PSC ext-header (must not crash) |

### 5.4 Optional eBPF/XDP dataplane assurance (8 tests): `upf-ebpf.yaml`

For eBPF/XDP UPFs (e.g. free5GC + **eUPF**). A **second, optional** certificate on top of the
universal conformance one. Proves the XDP program is attached, the N4 rules bind to BPF maps, and
the fast path forwards. IDs XDP-01…08 (4 essential). Deliberately **outside** the universal
conformance gate, an eBPF UPF still earns the normal conformance cert first.

---

## 6. Certification logic (the gate)

`cntc/verdict/evaluate.py` grades each test against the catalog's rule (`verdict: status_pass`,
threshold, etc.) into one of `pass | fail | na`. Then the **gate** (`gate.essential: all`):

```
        ┌───────────────────────────────────────────────┐
        │ every ESSENTIAL test == pass  ───────────────► │  PASS   → certificate issued
        │ any  ESSENTIAL test  == fail  ───────────────► │  FAIL   → no certificate
        │ any  ESSENTIAL test  == na  (couldn't run)  ─► │  INCOMPLETE → no certificate
        └───────────────────────────────────────────────┘
     supporting (non-essential) tests never block the gate; they inform the scorecard.
```

`cntc/certification/certificate.py::issue()` returns a certificate **only** on PASS. The cert ID
is `CNTC-<PROFILE>-<hash>` (e.g. `CNTC-AMF-A5031E277C`), derived from subject + profile + catalog
version + timestamp. A single-NF run writes that NF's own verdict, so the dashboard banner and a
`certify --profile <nf>-conformance` yield the **same** certificate ID.

---

## 7. Deployment modes: docker vs Kubernetes (a key diagram)

The **same tests, same NFs, different result**: because the difference is the deployment's
hardening, not the tests.

```
        DOCKER free5GC (docker-compose)            KUBERNETES free5GC (SD-Core/Aether Helm)
  adapter: free5gc                            adapter: free5gc_k8s
  discovery: docker inspect → 10.100.200.x    discovery: kubectl get svc → ClusterIP:port
  SBI: cleartext HTTP                         SBI: HTTPS (TLS)
  UE: native UERANSIM on host (N2 capture)    UE: none in-cluster → registration tests = na
```

### 7.1 Verified results (live free5GC, both modes)

| NF | Docker | Kubernetes (SD-Core) | Why the difference |
|---|---|---|---|
| **AMF** | **PASS** (9/9 essential) → `CNTC-AMF-…` | INCOMPLETE | k8s has no in-cluster UE → registration tests `na` |
| **AUSF** | **PASS** | INCOMPLETE | AUSF auth is transitive on registration (no UE on k8s) |
| **UDM** | **PASS** | INCOMPLETE | UDM SDM/auth-vector are transitive on registration |
| **SMF** | FAIL : no TLS on SBI | INCOMPLETE | docker SBI cleartext; k8s SMF SBI OK but sessions need a UE |
| **NRF** | FAIL : no TLS on SBI | **FAIL**: unauth discovery returns **HTTP 200** (real auth bypass) | genuine SBI-authorization gap on the k8s NRF |

**Docker: 3/5 certifiable. Kubernetes: NRF fails on a real, verified auth-bypass; the other four
are INCOMPLETE for lack of an in-cluster UE.** The framework **reports the gap; it never
rubber-stamps** and never fakes a pass. (The k8s NRF finding is verified: unauthenticated
`GET /nnrf-disc/v1/nf-instances` returns 200 with a full NF list.)

---

## 8. Data schema (`cntc_common/results.py`): what a diagram of the artifact should show

```
results.json
├── campaign            "VERIFY-AMF"
├── started             ISO-8601
├── sut                 { core_release, rig_class, cpu, … }        (report facts)
├── suites[]            one per NF/suite
│   └── tests[]         { id, name, status: pass|fail|na|error, metrics{}, tables{}, notes }
└── verdict             (written back after grading)
    ├── profile         "amf-conformance"
    ├── result          PASS | FAIL | INCOMPLETE
    ├── gate            { essential: "all" }
    ├── tests[]         graded { id, outcome, class: essential|normal, requirement }
    └── per_nf          (composite only) { amf: PASS, nrf: FAIL, … }

certificate.{md,html,json}   issued only when result == PASS
└── { certificate_id: "CNTC-AMF-…", profile, catalog_version, subject, essential_gate, issued }
```

---

## 9. Tool stack (open, arm's-length)

| Tool | Role | License / how obtained |
|---|---|---|
| **UERANSIM** | gNB + UE simulator (N1/N2) | **AGPL-3.0**: fetched & built by the bootstrap, **not bundled**; invoked as a separate process so licenses never mix |
| **SBI client** | our own HTTP/2 + TLS + OAuth2 client (Nnrf/Nausf/Nudm/Namf/Nsmf) | part of CNTC (Apache-2.0), built on `httpx[http2]` |
| **tcpdump + tshark** | capture & decode N2 (NAS) and N4 (PFCP) on the wire | system packages |
| **pfcpsim** | N4/PFCP driving (user-plane engine) | external process |
| **TRex / testpmd / tcpreplay** | user-plane traffic generators | external processes |
| **Dash + Plotly** | live dashboard | Python deps (optional extra) |

**CNTC itself is Apache-2.0.** External drivers keep their own licenses and are fetched
separately. Because UERANSIM is invoked as an arm's-length subprocess, its AGPL does not reach the
Apache-2.0 codebase.

---

## 10. Diagrams to produce (suggested set)

For the blog + release video, the highest-value diagrams, all fully specified above:

1. **Two-layer architecture** (§1), engines *measure* → `results.json` → umbrella *judges* →
   scorecard + certificate. *This is the hero diagram.*
2. **Control-plane run pipeline** (§3), the 8-step flow from config to certificate.
3. **Stimulus/observe topology** (§3a), UERANSIM (N1/N2) + SBI client vs the control-plane NFs, with the
   N2/N4 wire captures.
4. **Adapter/driver plug-in model** (§4), the pluggable edges (docker vs k8s adapters; UERANSIM
   vs SBI drivers) around a fixed suite/verdict core.
5. **Certification gate** (§6), essential-all → PASS/FAIL/INCOMPLETE decision.
6. **Docker vs Kubernetes** (§7), same tests, two deployments, the verified results table.
7. **Two certification levels** (§5.1–5.2), L1 shipped (44/26) vs L2 roadmap (20).
8. **Results artifact** (§8), the `results.json` → `verdict` → `certificate` object model.

### Brand / style notes for the diagrammer
- Light theme, teal accent `#0d9488`, secondary magenta `#b13a77`, pass-green `#1a7f37`,
  fail-red `#cf222e`, ink `#1f2328`, muted `#656d76` (matches the dashboard/theme).
- Keep the **measure vs judge** split visually explicit in every architecture diagram, it is the
  project's central idea.
- Existing reference PNGs live in `docs/diagrams/` (`cntc-architecture.*`, `cntc-docker-vs-k8s.*`)
, align new work with those.

---

*TOSSI Foundation · CNTC, certifying the cloud-native telecom stack, one standard at a time.*
