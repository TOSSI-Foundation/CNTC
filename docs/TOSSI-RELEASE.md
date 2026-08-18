# CNTC: 5G Core Control-Plane Certification

**Cloud-Native Telecom Certification (CNTC)** is an open, reproducible framework that turns 3GPP
specifications into an automated **pass / fail certificate** for a 5G core. This release adds the
**control-plane engine**: `cpbench`, which certifies the core's network functions:
**AMF, SMF, NRF, AUSF, UDM**.

> The model in one line: **a published standard → automated tests → a pass/fail gate → a
> per-NF certificate**, run against *your* live core, with every result a real observation on the
> wire or over the Service-Based Interface.

---

## What it is

Most of the industry can certify a **device** or a **base station**, but there is no open,
reproducible way to certify a **5G core network function** against its own 3GPP spec. Commercial
tools that do this (Spirent, Keysight, Valid8, Emblasoft) are closed and expensive.

CNTC fills that gap with two decoupled layers:

| Layer | Role | Ships in this release |
|---|---|---|
| **`cpbench`** engine | *Measures*, drives each NF over its real interfaces and records results | control plane (AMF/SMF/NRF/AUSF/UDM) |
| **`cntc`** umbrella | *Judges*, grades results against a per-NF requirement catalog and issues a certificate | reused, unchanged |

The umbrella is **pure and data-driven**: it grades whatever test IDs appear in a `results.json`
against a YAML catalog. The same umbrella already certifies the **user plane** (the UPF, via the
sibling `upfbench` engine), so one grading core certifies the whole core.

## How it tests: per-NF, standard-anchored

Every test maps to a clause of that NF's own 3GPP specification, both its **stage-3 protocol**
spec and its **SCAS** security-assurance spec:

| NF | Protocol | SCAS | Driven via |
|---|---|---|---|
| **AMF** | TS 24.501 (NAS) · TS 38.413 (NGAP) · TS 33.501 (5G-AKA) | TS 33.512 | UERANSIM over N1/N2 + on-the-wire capture |
| **SMF** | TS 29.502 (Nsmf) · TS 29.244 (N4/PFCP) | TS 33.515 | PDU sessions via UERANSIM + N4 capture |
| **NRF** | TS 29.510 (Nnrf) | TS 33.518 | SBI client (HTTP/2 + TLS + OAuth2) |
| **AUSF** | TS 29.509 (Nausf) | TS 33.516 | SBI client + transitive via registration |
| **UDM** | TS 29.503 (Nudm) | TS 33.514 | SBI client + transitive via registration |

It drives each NF with a **spec-compliant UE and SBI client**, then observes the result, including
**capturing NAS on N2 and PFCP on N4** and decoding them, so security properties (NAS ciphering,
control→dataplane binding, no auth-bypass) are proven on the wire, not assumed.

## Two certification levels

| Level | Proves | Tests | Status |
|---|---|---|---|
| **Level 1, Conformance & Observable Security** | everything provable with a **spec-compliant peer** + observation | **44** (26 essential) | **shipped, v1.0** |
| **Level 2, Adversarial Robustness & Privileged Interop** | everything needing a **non-compliant or privileged peer** (forged/replayed/malformed state, registered-NF PKI) | 20 | roadmap |

**The Level-1 guarantee:** every L1 test is *implemented*, so a verdict is never `INCOMPLETE`
because the tester didn't build something. When a case genuinely can't be judged on a deployment
(the core enforces no OAuth2, or there's no UE to drive registration) the result is a transparent
`INCOMPLETE` **about that deployment**: a reported gap, never a silent pass. An NF earns its
certificate only when **all its essential tests pass**.

## What a real run shows

Verified against a live **free5GC** on both deployment styles, *same tests, same NFs, different
result*, because the difference is the deployment's hardening (and what the rig can exercise), not
the tests:

| NF | Docker free5GC | Kubernetes free5GC (Helm + in-cluster UE) |
|---|---|---|
| **AMF** | **PASS** ✅ (9/9) | **PASS** ✅ (9/9) |
| **AUSF** | **PASS** ✅ | INCOMPLETE¹ |
| **UDM** | **PASS** ✅ | INCOMPLETE¹ |
| SMF | FAIL : no TLS on SBI | FAIL² |
| NRF | FAIL : no TLS on SBI | FAIL : serves discovery to an **unauthenticated** client (HTTP 200) + no TLS |

**Docker: 3 of 5 certifiable. Kubernetes: AMF certifies**: the full attach cycle (registration ·
5G-AKA · NAS ciphering/integrity · **wrong-key negative attach**) driven by an *in-cluster* UE.
Same harness, and it still surfaces every gap, the Kubernetes NRF serves the network topology to
an unauthenticated client, a real authorization finding the (OAuth2-enforcing) docker core does not
have. **The framework reports the gap; it does not rubber-stamp, and never fakes a pass.**

¹ AUSF/UDM core auth/subscription pass (transitively, via registration); their SBI-authorization
case is `na` because the default Helm free5GC doesn't enforce OAuth2, a token-less call returns
`400`, not `401/403`, so there's no authorization *decision* to grade.
² the in-cluster single-node UPF has no N6 route, so the PDU-session data-path case can't complete.

---

## Quick start

```bash
# 1. install the tester (system deps + Python deps + UERANSIM built from source)
git clone <repo> && cd control_cntc
./scripts/bootstrap_cpbench.sh

# 2. deploy the 5G core you want to certify (bring-your-own: free5GC / Open5GS / OAI)

# 3. build a config (auto-detects docker vs Kubernetes), then preflight
make cp-configure
make cp-doctor CONFIG=configs/<your>.yaml       # must print READY

# 4. run + grade + issue certificates
make cp-run    CONFIG=configs/<your>.yaml NF=all
cntc verdict   campaigns/<id>/results.json --profile amf-conformance
cntc certify   campaigns/<id>/results.json --profile amf-conformance

# 5. see it all in the dashboard
make dashboard
```

**Addresses are resolved live** every run (`docker inspect` / `kubectl get svc`), so redeploying
the core needs zero config edits. The config holds only what can't be derived (the N2 endpoint,
the capture interface, subscriber credentials), and the wizard auto-detects even those.

## What's in the box

```
cpbench/          the control-plane engine (adapters · drivers · observers · suites)
cntc/             the grading umbrella (verdict engine · certificate issuer · standards catalogs)
cntc_common/      the shared results schema
upfbench/         the sibling user-plane (UPF) engine
dashboard/        the live web dashboard over all campaigns
scripts/          bootstrap · config wizard · run helpers
docs/             architecture, the levels guide, the implementation report, diagrams
```

## The tool stack (open, arm's-length)

- **UERANSIM**: gNB + UE simulator (N1/N2). *AGPL-3.0; fetched and built by the bootstrap, not
  bundled, CNTC invokes it as a separate process, so the two licenses never mix.*
- **SBI client**: our own HTTP/2 + TLS + OAuth2 client for Nnrf/Nausf/Nudm/Namf/Nsmf.
- **tcpdump + tshark**: capture & decode N2 (NAS) and N4 (PFCP) on the wire.
- **pfcpsim**: N4/PFCP driving & observation.

## Honest scope

This is an open, reproducible **conformance + observable-security** harness, not a
NESAS-accredited security lab. It automates the **protocol-facing** SCAS cases (verify RES*, NAS
ciphering, SBI TLS/OAuth2, malformed → reject). OS-hardening and lifecycle-audit cases are out of
scope and are marked `na`, **never a silent pass**.

## License

**Apache-2.0.** External drivers keep their own licenses and are fetched separately (UERANSIM is
AGPL-3.0). See `NOTICE` / the bootstrap script for details.

---

*TOSSI Foundation · CNTC, certifying the cloud-native telecom stack, one standard at a time.*
