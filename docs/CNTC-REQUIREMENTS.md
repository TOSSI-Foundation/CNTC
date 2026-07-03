# CNTC Requirements — the rulebook behind the catalogs

This is the human-readable standard that the machine-checkable catalogs in
[`cntc/standards/`](../cntc/standards/) encode. For each requirement: **what** it checks,
**why** it matters, and **how to remediate** a failure.

A UPF is graded against a **profile**. The verdict per test is `pass` / `fail` / `na`
(*not judged* — did not run, errored, or a needed metric was absent; **never** silently a
pass). A profile's **essential** tests form its certification gate.

- **Result `PASS`** — every essential test passed.
- **Result `FAIL`** — an essential test failed.
- **Result `INCOMPLETE`** — an essential test did not run (run the missing suite).

---

## Profile: `conformance` (hardware-independent)

Certifiable on any rig — these outcomes don't depend on packets-per-second capacity. Run
with `--suite conformance` (= `pfcp` + `n3neg`) for a complete verdict.

### PFCP / N4 conformance — 3GPP TS 29.244

| ID | Requirement | Essential |
|----|-------------|:---------:|
| CF-01 | PFCP association setup / release | ✔ |
| CF-02 | PFCP session establishment | ✔ |
| CF-03 | PFCP session modification | ✔ |
| CF-04 | PFCP session deletion | ✔ |
| CF-05 | PFCP error handling (unknown SEID / missing mandatory IE → correct cause) | ✔ |

**Overview.** The N4 interface is how the SMF programs the UPF. The suite (via the vendored
`pfcpsim`) drives each PFCP procedure and asserts the UPF answers correctly.
**Rationale.** A UPF that mishandles association or session lifecycle can't be driven by a
standards-compliant core — it may leak sessions, drop rules, or crash the control plane.
**Remediation.** Compare the UPF's PFCP responses against TS 29.244 §7; check cause codes on
error paths; confirm the agent accepts modification (CF-03) and cleans up on deletion (CF-04).
Verdict rule: `status_pass` (the test's own pass/fail).

### N3 GTP-U robustness / security

| ID | Requirement | Essential |
|----|-------------|:---------:|
| NT-01 | Unknown-TEID robustness (packet for a TEID with no rule) | ✔ |
| NT-02 | Malformed GTP-U robustness — no crash | ✔ |
| NT-03 | PDU-Session-Container (0x85) extension-header robustness | ✔ |

**Overview.** Adversarial/garbage packets are injected on N3; the UPF must drop them cleanly
and keep forwarding valid traffic — no crash, no restart.
**Rationale.** A single malformed N3 packet that segfaults the user plane is a **remote DoS**.
This suite already found a real SIGSEGV in SD-Core BESS-UPF (`GtpuDecap::ProcessBatch`).
**Remediation.** Fix bounds-checking in the GTP-U parse path; treat unknown TEIDs and
malformed headers as drop, not fault.
Verdict rule: `status_pass` (the test's `ok` flag = no crash **and** valid traffic forwarded
**and** the UPF recovered).
**Honesty note.** Crash detection needs an adapter that can observe a UPF process restart
(SD-Core BESS today). Where it can't, the test returns `error` → graded **`na`**, *never*
`pass`. Do not weaken this.

---

## Profile: `performance` (hardware-dependent)

A raw Mpps number is meaningless without the rig it was earned on (af_packet on a shared VM
is ≈⅕ of dedicated DPDK, and af_packet throughput is even non-monotonic). So this profile
grades throughput/latency **relative to a trusted baseline** on the **same rig class**, and
requires `sut.rig_class` + a `baseline:`. Without them, the relative tests are `na` and the
verdict carries a loud warning.

| ID | Requirement | Rule | Essential |
|----|-------------|------|:---------:|
| TC-01 | Throughput (NDR) ≥ 90% of baseline | `baseline_rel peak_ndr_mpps >= 90%` | |
| TC-03 | Latency p99 ≤ 110% of baseline | `baseline_rel p99_us <= 110%` | ✔ |
| LT-01 | Session-capacity floor (absolute, capacity is portable) | `metric capacity_sessions >= 1000` | |

**Overview.** Regression-style grading: has this build/rig kept pace with a known-good run?
**Rationale.** Absolute performance thresholds would be arbitrary and rig-specific; relative
grading is fair, reproducible, and catches regressions.
**Remediation.** If a run fails, compare rig class, worker/core pinning, and dataplane mode
against the baseline before blaming the UPF (see the engine's `doctor` + config reference).

---

## Adding or changing a requirement

The bar is **data**, not code: edit the YAML in [`cntc/standards/`](../cntc/standards/) — no
engine changes. Add a profile by dropping a new `<name>.yaml`. Every catalog is validated by
`cntc lint`. See [CNTC-GOVERNANCE.md](CNTC-GOVERNANCE.md) for who may change a threshold and
how versioning works.
