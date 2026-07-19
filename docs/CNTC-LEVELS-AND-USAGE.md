---
title: "CNTC Control Plane — Level 1 vs Level 2, and How to Run It"
subtitle: "What each certification level proves, the full test lists, and the exact commands"
author: "TOSSI Foundation"
date: "July 2026"
geometry: margin=2cm
---

# CNTC Control Plane — Certification Levels & Usage Guide

This document explains the **two certification levels** we split the control-plane test suite
into, exactly what is in each, what the results look like on a real free5GC, and the **commands
to run everything** on a fresh server.

---

## 1. The two levels in one sentence each

| | |
|---|---|
| **Level 1 — "Conformance & Observable Security"** | Everything provable by connecting a **well-behaved, spec-compliant peer** (a normal UE and a normal SBI client), running the standard procedures, and **observing the result — including capturing the actual packets on the wire**. |
| **Level 2 — "Adversarial Robustness & Privileged Interop"** | Everything that needs a **badly-behaved or privileged peer** — one that lies (wrong credentials), replays old messages, sends deliberately broken packets, or impersonates a registered network function using real certificates. |

**A simple analogy:** *Level 1 is a thorough inspection. Level 2 is hiring a burglar to try to
break in.* Both are valuable; they answer different questions.

---

## 2. Why we split on this axis (and not on "security")

The obvious idea is to split "functional tests" from "security tests". **That does not work
here** — we measured it:

| Category | Implemented (L1) | Remaining (L2) |
|---|:--:|:--:|
| protocol | 24 | 13 |
| **security** | **14** | **4** |
| robustness | 6 | 4 |

**Security is already 78% complete.** Splitting on "security" would push most of Level 1's best
content into Level 2 and leave an incoherent release story. Level 1 contains a *lot* of
security: NAS ciphering and integrity, SBI authorization and TLS enforcement, SUPI
confidentiality, no-auth-bypass, and no-crash-on-malformed-input.

The axis that actually separates the two cleanly is **what the test demands of the peer**:

- can a **compliant** peer + observation prove it? → **Level 1**
- does it require **forging protocol state** or a **privileged identity**? → **Level 2**

This also matches the **tooling** boundary exactly, which is why it is a real engineering
boundary and not an arbitrary one:

| | Level 1 tooling | Level 2 tooling |
|---|---|---|
| Drivers | compliant UE simulator (UERANSIM), SBI (HTTP/2) client | raw NGAP/NAS message crafter, patched/misbehaving UE |
| Identity | anonymous / unprivileged | registered-NF certificates (PKI) |
| Observation | packet capture + decode (tshark) | same, plus injection |

---

## 3. The key benefit: Level 1 is *cleanly certifiable*

**Every single Level 1 test is implemented.** That is the defining property of the level.

Because of it, a Level 1 verdict is always a clean **PASS or FAIL about the deployment** — it can
never come back `INCOMPLETE` because *the tester* didn't build something. Unimplemented Level 2
tests are held in separate catalogs so they cannot distort a Level 1 verdict.

| | Level 1 | Level 2 |
|---|---|---|
| Tests | **44** | **20** |
| Essential (gate the certificate) | **26** | 10 |
| Implementation status | **100% implemented** | roadmap (v2.0) |
| Catalog file | `cntc/standards/<nf>-conformance.yaml` | `cntc/standards/<nf>-adversarial.yaml` |
| Ships in | **v1.0 (now)** | v2.0 |

**Certification rule (both levels):** an NF earns its certificate only if **every ESSENTIAL test
passes**. An essential FAIL → no certificate. An essential that cannot be judged → `na` →
`INCOMPLETE` (never a silent pass). Normal/bonus tests are scored and reported but never gate.

> The certificate always names the level — e.g. *"CNTC AMF **Level 1** (Conformance & Observable
> Security)"* — so it can never be mistaken for covering adversarial testing.

---

## 4. Level 1 — the full test list (44 tests, 26 essential)

`E` = essential (gates the certificate) · `n` = normal · `B` = bonus

### AMF — 14 tests, 9 essential
| | Test | What it checks |
|:--:|---|---|
| E | AMF-REG-01 | Initial registration (TS 24.501 §5.5.1.2) |
| E | AMF-AUTH-01 | 5G-AKA authentication (TS 33.501 §6.1.3.2) |
| E | AMF-AUTH-02 | Security-mode command / complete (TS 24.501 §5.4.2) |
| E | AMF-DEREG-01 | UE-initiated deregistration (TS 24.501 §5.5.2.2) |
| E | AMF-NGAP-01 | NG Setup — gNB↔AMF association (TS 38.413 §8.7) |
| n | AMF-NGAP-02 | Initial UE message / UL-DL NAS transport (§8.6) |
| n | AMF-NGAP-03 | Initial context setup (§8.3) |
| n | AMF-NGAP-04 | UE context release (§8.3) |
| E | AMF-SEC-01 | NAS integrity protection after security activation |
| E | AMF-SEC-02 | NAS confidentiality (ciphering) after SMC |
| E | AMF-SEC-04 | RES* failure → registration rejected (no auth bypass) |
| n | AMF-SEC-06 | SUPI confidentiality (SUCI used, not cleartext) |
| n | AMF-SEC-07 | SBI (Namf) requires TLS + valid OAuth2 token |
| E | AMF-NEG-01 | Malformed NGAP PDU → reject, no crash |

### SMF — 11 tests, 7 essential
| | Test | What it checks |
|:--:|---|---|
| E | SMF-SESS-01 | PDU session establishment (TS 24.501 §6.4.1) |
| E | SMF-SESS-03 | PDU session release (§6.4.3) |
| n | SMF-SESS-04 | Multiple PDU sessions / multi-DNN |
| n | SMF-SESS-05 | UE IP address allocation |
| E | SMF-N4-01 | SMF programs UPF over N4 (PFCP Session Establishment) |
| n | SMF-N4-03 | N4 session deletion on PDU release |
| E | SMF-DP-01 | Data-path verify — traffic forwards through the UPF |
| E | SMF-SEC-01 | SBI (Nsmf) requires TLS + valid OAuth2 token |
| E | SMF-SEC-02 | Reject PDU-session create with invalid mandatory IE |
| n | SMF-SEC-03 | Session-event logging present |
| E | SMF-NEG-01 | Invalid session request → reject, no crash |

### NRF — 8 tests, 3 essential
| | Test | What it checks |
|:--:|---|---|
| n | NRF-REG-01 | NFRegister — a peer NF registers its profile |
| n | NRF-DISC-01 | NFDiscover by NF type returns correct profiles |
| n | NRF-DISC-02 | NFDiscover by service name |
| n | NRF-SEC-01 | OAuth2 access-token endpoint present + validates clients |
| E | NRF-SEC-02 | Reject discovery/registration without a valid token |
| E | NRF-SEC-03 | SBI endpoint requires TLS |
| E | NRF-NEG-01 | Malformed NFRegister → 4xx, no crash |
| n | NRF-NEG-02 | Discover unknown NF type → empty / 404 |

*NRF-REG-01 and NRF-DISC-01 are deliberately **normal**, not essential: on a deployment that
enforces OAuth2 they need a privileged NF identity, so they must inform the scorecard without
gating the certificate.*

### AUSF — 4 tests, 3 essential
| | Test | What it checks |
|:--:|---|---|
| E | AUSF-AUTH-01 | UEAuthentication_Authenticate (5G-AKA) runs |
| n | AUSF-AUTH-02 | Confirm RES* — successful authentication |
| E | AUSF-SEC-02 | SBI (Nausf) requires TLS + valid OAuth2 token |
| E | AUSF-NEG-01 | Malformed auth request → reject, no crash |

### UDM — 7 tests, 4 essential
| | Test | What it checks |
|:--:|---|---|
| E | UDM-SDM-01 | Subscription data retrieval (Nudm_SDM_Get) |
| n | UDM-SDM-02 | Session-management subscription data (for SMF) |
| n | UDM-UECM-01 | AMF registration for UE (Nudm_UECM) |
| E | UDM-AUTH-01 | Authentication-vector generation |
| n | UDM-SEC-01 | SUCI de-concealment (SIDF) authorization |
| E | UDM-SEC-02 | SBI (Nudm) requires TLS + valid OAuth2 token |
| E | UDM-NEG-01 | Unknown SUPI → 404 ProblemDetails, no crash |

---

## 5. Level 2 — the roadmap list (20 tests, 10 essential)

These are **not implemented yet** — they grade `na`, so a Level 2 run is `INCOMPLETE` by design
until the v2.0 tooling lands.

### AMF — 10 tests
| | Test | Why it is Level 2 |
|:--:|---|---|
| n | AMF-REG-02 | Periodic / mobility registration update — timer-driven trigger |
| n | AMF-CONN-01 | Service request — needs an idle→connected transition |
| n | AMF-CONN-02 | UE context release / CM-state transition |
| n | AMF-CONN-03 | Paging — network-initiated |
| n | AMF-DEREG-02 | Network-initiated deregistration |
| E | AMF-NGAP-05 | Error indication / unknown-IE — needs crafted NGAP |
| E | AMF-SEC-03 | NAS replay protection — needs to replay a real message |
| E | AMF-SEC-05 | No bidding-down — needs an algorithm-downgrading UE |
| E | AMF-NEG-02 | Malformed NAS — needs crafted NAS |
| E | AMF-NEG-03 | Unknown gNB / bad NG-Setup — needs crafted NG Setup |

### SMF — 4 tests
| | Test | Why it is Level 2 |
|:--:|---|---|
| n | SMF-SESS-02 | PDU session modification — not exposed by the compliant UE |
| n | SMF-N4-02 | N4 session modification |
| E | SMF-NEG-02 | Duplicate-session handling — stress/edge |
| E | SMF-NEG-03 | Session-count ceiling — scale/stress |

### NRF — 3 tests
| | Test | Why it is Level 2 |
|:--:|---|---|
| E | NRF-REG-02 | NFUpdate / heartbeat — needs a registered NF identity (PKI) |
| E | NRF-REG-03 | NFDeregister — needs a registered NF identity |
| n | NRF-DISC-03 | NFStatusSubscribe / notify — needs a registered NF identity |

### AUSF — 3 tests
| | Test | Why it is Level 2 |
|:--:|---|---|
| E | AUSF-AUTH-03 | Wrong RES* → auth failure — needs a lying UE |
| B | AUSF-AUTH-04 | EAP-AKA′ — needs a different auth method / UE |
| n | AUSF-SEC-01 | SUPI / Kausf internal handling — not externally observable |

**UDM has no Level 2 tests — it is 7/7 complete at Level 1.**

**v2.0 build order** (each group maps to one new capability):
1. **Raw N2 crafter** (Python: SCTP + NGAP/NAS encoding) → 6 tests
2. **NF-identity / PKI** via the SBI client → 3 tests
3. **Network-initiated triggers** → 5 tests
4. **Modify / stress** → 4 tests
5. **EAP / internals** → 2 tests

---

## 6. Results — Level 1 on a real free5GC

Verified against a live free5GC, both deployment styles:

| NF | L1 tests | L1 essentials | **Docker** | **Kubernetes** |
|---|:--:|:--:|:--:|:--:|
| **AMF** | 14 | 9 | **PASS ✅** (9/9) | INCOMPLETE (8/9)¹ |
| **SMF** | 11 | 7 | FAIL — no TLS | FAIL — no TLS + no authz |
| **NRF** | 8 | 3 | FAIL — no TLS | FAIL — no authz + no TLS |
| **AUSF** | 4 | 3 | **PASS ✅** (3/3) | FAIL — no authz |
| **UDM** | 7 | 4 | **PASS ✅** (4/4) | FAIL — no authz |
| | **44** | **26** | **3 / 5 certifiable** | **0 / 5 certifiable** |

Certificates issued on docker: `CNTC-AMF--8D15FE0A72`, `CNTC-AUSF-3CECFDF28C`, `CNTC-UDM--C3FF58F297`.

**The headline finding:** *same tests, same NFs, different result.* The Kubernetes free5GC runs
its SBI **without TLS and without OAuth2 authorization**, so it genuinely fails the security bar
that the docker deployment clears. **UDM passes on docker and fails on k8s for exactly that
reason.** That is the framework working as intended — it reports a real deployment weakness
rather than rubber-stamping.

¹ The single Kubernetes `INCOMPLETE` is **our** limitation, not the deployment's: the in-cluster
driver cannot run a wrong-credential UE for `AMF-SEC-04`. The AMF's N2 NodePort is reachable
from the host, so a host-side negative attach closes it.

---

## 7. How to run it — commands

### 7.1 One-time install on a fresh server

```bash
git clone <repo> && cd control_cntc
./scripts/bootstrap_cpbench.sh        # system deps + Python deps + UERANSIM (built from source)
```

Then **deploy the 5G core you want to test** (docker-compose or Kubernetes). The bootstrap
installs the *tester*, never the core — the core is the thing under test (bring your own).

### 7.2 Configure

```bash
make cp-configure                     # interactive wizard; auto-detects docker vs k8s
```

It writes a **new** file named after the campaign id, e.g. `configs/free5gc-k8s-001.yaml`.
It does **not** overwrite the shipped examples (`configs/free5gc-cp.yaml`,
`configs/free5gc-k8s.yaml`) — those are templates.

Variants:
```bash
./scripts/cpbench-configure.py --non-interactive          # accept every detected value (CI)
./scripts/cpbench-configure.py --out configs/mine.yaml    # choose the filename / overwrite
vim configs/free5gc-k8s-001.yaml                          # hand-edit anything; the file wins
```

### 7.3 Preflight, then run

```bash
make cp-doctor CONFIG=configs/free5gc-k8s-001.yaml        # must print READY
make cp-run    CONFIG=configs/free5gc-k8s-001.yaml NF=all # or NF=amf|smf|nrf|ausf|udm
```

### 7.4 Grade, certify, view

```bash
make verdict CAMPAIGN=FREE5GC-K8S-001     # (re)grade a past run -> scorecard
make certify CAMPAIGN=FREE5GC-K8S-001     # issue a certificate iff the verdict is PASS
make dashboard                            # live web UI over campaigns/
```

### 7.5 Grading against a specific level

```bash
# Level 1 (the shipped certification)
cntc verdict campaigns/<id>/results.json --profile amf-conformance
cntc certify campaigns/<id>/results.json --profile amf-conformance

# Level 2 (roadmap — will read INCOMPLETE until v2.0)
cntc verdict campaigns/<id>/results.json --profile amf-adversarial

cntc profiles                             # list every catalog/level
```

### 7.6 Without `make` (identical)

```bash
python3 -m cpbench.cli doctor --config configs/free5gc-k8s-001.yaml
python3 -m cpbench.cli run    --config configs/free5gc-k8s-001.yaml --nf all
python3 -m cpbench.cli list                       # NFs + catalogs + test counts
```

---

## 8. How addressing works (you do not hand-write NF IPs)

**NF IP addresses are resolved live at run time**, not stored in the config:

- **docker** → `docker inspect` → e.g. `AMF 10.100.200.16:8000`, `NRF 10.100.200.4:8000`
- **Kubernetes** → `kubectl get svc` → e.g. `AMF 10.152.183.222:8080`, `NRF 10.152.183.47:8000`

So redeploying your core (new container/pod IPs) needs **zero config edits**.

The config holds only what genuinely cannot be derived — and the wizard auto-detects even these:

| Setting | Why it must be configured |
|---|---|
| `amf_n2_addr` / `amf_n2_port` | the N2/SCTP endpoint the gNB dials (a **NodePort** on k8s) |
| `gnb_link_ip` | the host IP your gNB binds to (a property of your machine) |
| `n2_iface` | which interface to packet-capture on (`br-free5gc` vs `any`) |
| `webui` | subscriber-provisioning URL (`:5000` docker, `:30500` NodePort) |
| `subscribers` | SIM credentials (SUPI / Ki / OPc) — must match your UE |

**Override is always available.** A non-empty entry wins and skips live discovery:

```yaml
core:
  endpoints:
    amf: "10.20.30.40:8000"      # pin it explicitly (remote core, proxy, odd port)
```

The `campaign:` field drives both the generated config filename **and** the results directory
(`campaigns/<campaign>/` — where `results.json`, scorecards and certificates land). Re-running
the same campaign id overwrites; bump it (`-002`) to keep a previous run for comparison.

---

## 9. Which config file for which deployment

| Config | Use when | Key differences |
|---|---|---|
| `configs/free5gc-cp.yaml` | your core runs in **docker-compose** | `adapter: free5gc` — NFs via `docker inspect`, capture on `br-free5gc`, WebUI on `localhost:5000` |
| `configs/free5gc-k8s.yaml` | your core runs in **Kubernetes** | `adapter: free5gc_k8s` — NFs via `kubectl get svc`, AMF via **NodePort**, capture on `any`, WebUI on a NodePort |

You never run both — pick the one matching your deployment, or just let `make cp-configure`
detect it. *(Careful: `configs/free5gc.yaml` is a **UPF** config for `upfbench`, not a
control-plane one.)*

---

*All figures in this document come from actual runs against a live free5GC — every PASS/FAIL is
a real observation on the wire or over SBI, and every unimplemented requirement is honestly
marked `na`.*
