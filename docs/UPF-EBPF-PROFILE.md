---
title: "CNTC UPF eBPF/XDP Dataplane Assurance — a second, optional certificate"
author: "TOSSI Foundation"
date: "July 2026"
geometry: margin=2cm
---

# CNTC UPF eBPF/XDP Dataplane Assurance

## 1. Two certificates, cleanly separated

An eBPF/XDP-based UPF (e.g. **free5GC + eUPF**) can earn **two** distinct certificates:

| Certificate | Profile | Who can earn it | What it means |
|---|---|---|---|
| **UPF Conformance** (universal) | `conformance` | **every** UPF (af_packet, af_xdp, dpdk, cndp, eBPF) | obeys N4/PFCP (TS 29.244) + survives malformed N3 (CF-01..05, NT-01/02) — **standards-anchored, implementation-agnostic** |
| **UPF eBPF/XDP Dataplane Assurance** (optional) | `upf-ebpf` | **only** eBPF/XDP UPFs | the XDP fast path is real and engaged, and the N4 rules are correctly reflected in the BPF maps — **white-box** |

**Why two, not one merged gate:** the conformance certificate must mean the *same thing* for
every UPF so it is comparable. Folding "must have pinned BPF maps" into it would (a) make a
perfectly conformant DPDK UPF fail a meaningless check, and (b) let an eUPF with pretty maps but
a GTP-U crash still look good. So implementation-specific assurance lives in its **own optional
profile** — exactly like the `performance` profile and the control-plane Level 2.

The eBPF profile **never gates** the conformance certificate. It is additive, opt-in, and only
applicable to eBPF UPFs — on any non-eBPF UPF every test grades `na` → `INCOMPLETE` → no eBPF
certificate (which is correct: a DPDK UPF is not an eBPF UPF).

## 2. The tests (8 tests, 4 essential)

`E` = essential (gates the eBPF certificate) · `n` = normal (scored, non-gating)

| | Test | Checks | How it is observed |
|:--:|---|---|---|
| E | XDP-01 | XDP program attached to the N3 (access) iface | `bpf_introspect().xdp[iface].attached` |
| n | XDP-02 | attach mode is native/driver, not generic/SKB | `.xdp[iface].mode` == "native" (informational — generic is a slow fallback, often forced by the VM/NIC, so it warns rather than fails) |
| E | XDP-07 | fast path engaged — stats map increments under N3 traffic | read a counter map before/after injecting N3 traffic; it must increase |
| n | XDP-08 | program stays attached after a malformed-N3 burst | re-read `.xdp[iface].attached` after the NT-02 burst; must still be attached (no silent detach to slow path) |
| n | XDP-03 | required BPF maps present + pinned (PDR/FAR/QER/session) | `.maps[*].pinned` |
| E | XDP-04 | installed PFCP session reflected in the maps (TS 29.244 §5.2) | install a session via pfcpsim → the PDR/FAR TEID appears in the map |
| E | XDP-05 | deleted session removes its map entries (no stale state) | delete the session via pfcpsim → the entry disappears |
| n | XDP-06 | map entry count matches installed session count | `.maps[session].entries` == number of pfcpsim-installed sessions |

**Essential gate:** `XDP-01` (attached), `XDP-04` (control→data binding), `XDP-05` (unbinding),
`XDP-07` (fast path actually forwards). A tight, meaningful bar: the program is attached, rules
bind and unbind correctly, and packets really traverse XDP.

## 3. What the adapter must provide (the contract)

The tests are UPF-agnostic; they read one new **optional** method on the UPF adapter. Only the
eBPF adapter implements it — others return `None`, and every XDP test then grades `na`.

```python
class UPFAdapter:
    def dataplane_kind(self) -> str:
        """'ebpf' | 'dpdk' | 'af_packet' | 'af_xdp' | 'cndp' | 'gtp5g' | ...
        Default: derive from describe()['mode']. The upf-ebpf profile applies only when 'ebpf'."""

    def bpf_introspect(self) -> dict | None:
        """White-box view of the eBPF/XDP dataplane. None on non-eBPF UPFs (-> XDP tests 'na').
        {
          "xdp": { "<iface>": {"attached": bool, "mode": "native"|"generic"|"offload",
                               "prog_id": int, "prog_name": str} },
          "maps": { "pdr":     {"pinned": bool, "entries": int, "max": int},
                    "far":     {"pinned": bool, "entries": int, "max": int},
                    "qer":     {"pinned": bool, "entries": int, "max": int},
                    "session": {"pinned": bool, "entries": int, "max": int} },
          "stats": { "rx_packets": int, "tx_packets": int, "xdp_redirect": int, ... },
          # helper: does a TEID currently have a dataplane rule? (for XDP-04/05)
          "teids": [ int, ... ]
        }
        Sources for eUPF: its REST API (:8080), `bpftool prog show` / `bpftool map dump`,
        and /sys/fs/bpf. Prefer the REST API where it exposes this; fall back to bpftool.
        Whichever you use, VERIFY the fields live — do not assume the API shape."""
```

`bpf_introspect()` MUST reflect **live** state (re-read per call), because `XDP-04/05/06` compare
it before/after pfcpsim installs and deletes a session, and `XDP-07` compares stats before/after
traffic.

## 4. Where the test code goes

A new suite package mirroring the existing ones:

```
upfbench/suites/ebpf/
  xdp01_attached.py     ...  xdp08_stays_attached.py
```

Each test:
1. `if ctx.upf.dataplane_kind() != "ebpf": return na("not an eBPF/XDP UPF")`
2. read `ctx.upf.bpf_introspect()`; if `None` → `na`
3. for binding tests, drive pfcpsim (already wired for the pfcp/n3neg suites) to install/delete
   a session, then re-introspect
4. return pass/fail with the observed evidence in `metrics`/`notes`

Register the suite so it can be selected: `upfbench run --config <eupf>.yaml --suite ebpf`, and
grade with `cntc verdict <results> --profile upf-ebpf` / `cntc certify ... --profile upf-ebpf`.

## 5. How it runs (once implemented)

```bash
# universal conformance certificate (same as every UPF)
upfbench run    --config configs/eupf.yaml --suite conformance
cntc     certify campaigns/<id>/results.json --profile conformance

# additional eBPF/XDP dataplane certificate (eBPF UPFs only)
upfbench run    --config configs/eupf.yaml --suite ebpf
cntc     certify campaigns/<id>/results.json --profile upf-ebpf
```

An eUPF that passes both gets **two** certificates; a DPDK UPF gets the conformance one and an
`INCOMPLETE` (no certificate) on `upf-ebpf`, which is the correct, honest outcome.

## 6. Status

- **Catalog `cntc/standards/upf-ebpf.yaml`: done** — lints clean; grading verified (all-pass →
  PASS → certificate `CNTC-UPF-…`; non-eBPF → INCOMPLETE → no certificate).
- **Test code + adapter `bpf_introspect()`: to implement** alongside the eUPF adapter (that work
  needs the live free5GC + eUPF deployment). The catalog is the standard; the tests fill it in,
  exactly as the control-plane catalogs preceded their test code.
