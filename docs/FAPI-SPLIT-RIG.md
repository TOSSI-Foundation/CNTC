# Testing the L1/L2 split (PNF / VNF over nFAPI) with CNTC

A guide for running a CNTC campaign against an L1/L2 split gNB you already have built and
working. It covers what to configure, what to run, and the failures that look like product bugs
but are not. It does not cover building the stack.

If you have never brought the stack up by hand, do that first (section 6). Everything else
assumes it attaches a UE on its own.

## Two stacks: cross-vendor (xFAPI) and pure OAI

This guide's body covers the **cross-vendor** stack: an OAI PNF, an xFAPI VNF, and an OCUDU L2
and CU. There is a second, simpler stack that the same catalogs, observer and suites certify
without change, the **pure-OAI** stack, where both ends of nFAPI are OAI `nr-softmodem`:

|  | cross-vendor (`configs/fapi-split.yaml`) | pure OAI (`configs/fapi-oai.yaml`) |
|---|---|---|
| PNF (L1) | OAI `nr-softmodem --nfapi PNF` | OAI `nr-softmodem --nfapi PNF` (same binary) |
| VNF | xFAPI, in front of an OCUDU L2 | OAI `nr-softmodem --nfapi VNF` (carries L2 and L3) |
| CU | OCUDU CU over F1 | none: the VNF terminates N2/N3 itself |
| processes | four (CU, VNF, PNF, L2) | two (VNF, PNF) |
| DPDK / xSM | yes, between VNF and L2 | none |
| adapter | `fapi_split` | `fapi_oai` (subclasses `fapi_split`) |
| build | `FAPI=1 ./scripts/bootstrap_ranbench.sh` (fork UE) | `OAI_GNB=1 ./scripts/bootstrap_ranbench.sh` |

For the pure-OAI stack, run:

```bash
make ran-oai-fapi-doctor                                 # must print READY
make ran-oai-fapi-run TARGET=all CAMPAIGN=FAPI-OAI-001
make ran-certify CAMPAIGN=FAPI-OAI-001 TARGET=vnf        # or TARGET=pnf
```

The rest of this guide (the ports, the rig failures, reading the result, the by-hand appendix)
applies to both, except that the pure-OAI config has only `vnf` and `pnf` under `ran.procs` and
no `cu` or `l2`. Because the PNF is the same OAI L1 in both, its verdicts should match across the
two, which is a useful cross-check. One caveat specific to a shared host: the VNF slot-timing
requirements (VNF-P7-01/02/05) grade `na` when the SLOT.indication stream shows the host
descheduled the stack, because lateness then cannot be attributed to the VNF. They are judgeable
on a CPU-isolated, pinned rig or a real radio.

## What you need before you start

- The four RAN components built and runnable on this host: an nFAPI **PNF** (L1), an nFAPI
  **VNF** (the bridge that terminates nFAPI), an **L2** (MAC/RLC), and a **CU** (RRC/PDCP). The
  reference rig is an OAI PNF, xFAPI, and an OCUDU L2 and CU.
- An OAI **nr-UE** built with the rfsimulator, and a UE config file holding the SIM.
- A **5G core** running and reachable (free5GC compose on the reference rig), with the
  subscriber provisioned.
- `tcpdump`, `tshark`, and passwordless `sudo`.
- CNTC installed: `pip install -e .`

## The one file you edit: `configs/fapi-split.yaml`

Copy it and change the paths and cell parameters for your host. Nothing else in CNTC needs
editing.

### Component commands (`ran.procs`)

Each of the four processes is one block. Set `bin` to the executable, `args` to its arguments
(config-file paths included), and `proc` to the process name `pgrep -x` will find (a launcher
script runs a differently named binary, so `proc` is separate from `bin`).

```yaml
ran:
  procs:
    cu:
      bin:  <path>/ocu
      args: ["-c", "<path>/cu.yml"]
      proc: ocu
    vnf:
      bin:  <path>/run_xfapi.sh
      cwd:  <path>/xFAPI          # a launcher that resolves conf/ and bin/ relative to itself
      proc: xfapi_main
    pnf:
      bin:  <path>/nr-softmodem
      args: ["-O", "<path>/gnb-pnf...conf", "--nfapi", "PNF", "--rfsim", "--thread-pool", "0,1,2,3"]
      proc: nr-softmodem
      cpus: "0-6"                 # see pinning below
    l2:
      bin:  <path>/odu_high
      args: ["-c", "<path>/odu_high_xfapi_rfsim.yaml"]
      proc: odu_high
      cpus: "9-13"
```

`cpus` pins a process with `taskset`. It is not tuning: on a host that isolates cores for the
real-time threads, an unpinned L1 or L2 lands beside the OS and misses slot deadlines. The
reference layout is PNF 0-6, VNF 7-8, L2 9-13, OS and UE 14-17. If your host has a different
core count or isolation, change `cpus` here **and** the affinity settings inside the L2 and PNF
configs to match.

Set `ran.procs.*.release` on each block to the component's version string. It goes onto the
certificate, which otherwise cannot say what was tested (xFAPI has no `--version`).

### nFAPI ports (`ran.nfapi_ports`)

Only change these if your PNF and VNF configs do not use the defaults. The VNF listens on
`p5_vnf` (SCTP) and the PNF dials it; P7 is UDP.

```yaml
  nfapi_ports: { p5_pnf: 50000, p5_vnf: 50001, p7_pnf: 50010, p7_vnf: 50011 }
  dpdk_file_prefix: gnb0          # the VNF's DPDK hugepage prefix, cleared before each run
  rfsim_port: 4043                # checked for conflicts before the PNF starts
```

### UE and cell (`drivers`)

These must match the cell the L2 broadcasts, or the UE never finds it. The L2 prints the exact
UE command line it expects on startup; read the values from there.

```yaml
drivers:
  ue: ue_oai_fapi
  ue_bin:  <path>/nr-uesoftmodem
  ue_conf: <path>/nrue.rfsim.conf   # holds the SIM; the campaign does not restate it
  prb: 273                          # 100 MHz at SCS 30 kHz on the reference cell
  band: 78
  centre_freq_hz: 3450720000
  ssb: 1518
```

### Knobs (`knobs`)

```yaml
knobs:
  pnf:
    radio: simulated                # slot cadence and jitter grade 'na'; set "hardware" to grade them
    ra_response_window_slots: 20     # the L2's ra_resp_window, needed by PNF-TIME-02
  vnf:
    radio: simulated
```

`core.adapter: manual` is correct for any core CNTC does not manage. It means CNTC will not probe
or provision the core; you are responsible for it being up with the SIM provisioned.

## Run it

```bash
# reset the AMF before every run (see below), then:
make ran-doctor CONFIG=configs/fapi-split.yaml            # must print READY
make ran-run    CONFIG=configs/fapi-split.yaml TARGET=all CAMPAIGN=FAPI-001
sudo chown -R $USER campaigns/                            # the run is root; certify is you
make ran-certify CAMPAIGN=FAPI-001 TARGET=vnf             # or TARGET=pnf
```

`split: fapi` in the config makes `--target all` expand to `pnf` and `vnf` only. `ran-doctor`
checks the binaries, the rfsim port, and the subscriber, and prints `NOT READY` with the reason
if any is wrong. The run starts and stops all four components itself, in the required order, so
**stop any stack you brought up by hand first.**

Results land in `campaigns/FAPI-001/`: `scorecard-pnf.md`, `scorecard-vnf.md`, and a
`certificate.md` for each class whose essential gate passed.

## Failures that are the rig, not the product

These all look like the PHY or the bridge misbehaving. They are not, and `ran-doctor` or the run
banner will name most of them.

- **`rfsim port ... HELD by ...` in the doctor.** Something already owns port 4043 (often a
  stray gNB from other work). The PNF cannot bind it, transmits void samples, and no UE
  synchronises. Free the port before running.

- **UE reaches RRC_CONNECTED then stops, or `Duplicated PDU session ID` in the core.** The core
  is holding stale state from a previous run. **Reset the AMF before each campaign**
  (`docker compose restart free5gc-amf`), and the SMF and UPF too if the UPF has been idle long
  enough to drop its PFCP association.

- **UE asserts on `mapped_drbs <= 1` before the PDU session.** The core signalled more than one
  QoS flow, so the CU built more than one DRB, which a UE refuses when SDAP headers are absent
  (TS 37.324). Provision the subscriber with a **single QoS flow**. Do not fix it by enabling
  SDAP headers in the CU: that changes the user-plane format and breaks N3 decapsulation.

- **`STIMULUS INCOMPLETE` in the run banner.** The UE did not complete the attach, so most tests
  record `na`. The banner names how far it got and the probable cause. The verdict is void; fix
  the rig and re-run.

## Reading the result

A PNF or VNF certificate asserts conformance to the SCF nFAPI interface in that role. It is not
a 3GPP SCAS product-class certificate, and the scorecard says so.

The reference run certifies the **VNF** (9 of 9 essential) and fails the **PNF** on `PNF-P5-07`,
the PHY emitting P7 slot traffic 49 ms before it answered `START.response`. The physical layer
is not judged (the nFAPI wire carries what the PHY reports, not what it transmitted), and slot
cadence and jitter record `na` under a radio simulator because the sample clock, not the PHY,
sets the rate. A test that cannot be judged records `na` with the reason, never a pass.

## Appendix: bring the stack up by hand

Do this once before trusting a campaign. The start order is enforced by the products: the CU
must be listening on F1-C before the L2 dials it (the L2 tries once), the VNF must create the
DPDK region before the L2 attaches, and the PNF must complete its P5 handshake before the L2
asks the PHY for its parameters (also once).

```bash
sudo pkill -9 -x nr-uesoftmodem nr-softmodem odu_high xfapi_main ocu
sudo rm -rf /dev/hugepages/gnb0* /var/run/dpdk/gnb0

cd <cu>/apps/cu       && sudo ./ocu -c cu.yml                                      # 1
cd <xfapi>            && sudo ./run_xfapi.sh                                       # 2
cd <pnf>              && sudo taskset -c 0-6 ./nr-softmodem -O gnb-pnf...conf --nfapi PNF --rfsim --thread-pool 0,1,2,3   # 3
cd <l2>/apps/fapi_split && sudo taskset -c 9-13 ./odu_high -c odu_high_xfapi_rfsim.yaml   # 4
cd <ue>               && sudo ./nr-uesoftmodem -O nrue.rfsim.conf --rfsim --rfsimulator.serveraddr 127.0.0.1 -r 273 --numerology 1 --band 78 -C 3450720000 --ssb 1518   # 5
```

A working attach reaches `PDU Session Establishment Accept, UE IPv4: ...`. Restart the VNF
whenever you restart the PNF: it does not repeat the P5 handshake.
