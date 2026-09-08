# The OAI rig for RANCert

How to build and run an OAI split gNB (CU-CP / CU-UP / DU) as a product under test, against
free5GC. Every command here was run on 2026-09-08 against OAI `develop` tag `2026.w33`
(commit `2b69bde`) and is reported as it actually behaved, including where the upstream
documentation is out of date.

## Build

Only `nr-uesoftmodem` was built on this box. The gNB needs two more binaries. `--gNB` in
`build_oai` maps to the targets `nr-softmodem nr-cuup`, and the existing build tree is already
configured for Ninja against the right source, so building the targets directly reuses
everything already compiled:

```
cd ~/openairinterface5g/cmake_targets/ran_build/build
sudo ninja nr-softmodem nr-cuup
```

1436 steps. The `5gdefault` CMake preset does **not** include `nr-cuup`, so a preset build
alone will not give you a CU-UP.

## Run, in this order

The rig uses the same address plan as the OCUDU rig, so one set of capture filters covers both
stacks. From `~/openairinterface5g/cmake_targets/ran_build/build`:

```
sudo ./nr-softmodem -O ~/cntc_ran/configs/oai/cucp.conf                 # CU-CP
sudo ./nr-cuup      -O ~/cntc_ran/configs/oai/cuup.conf                 # CU-UP
sudo ./nr-softmodem -O ~/cntc_ran/configs/oai/du.conf --rfsim           # DU
```

Wait for each: the CU-CP must log `Received NGSetupResponse`, then `Accepting new CU-UP`, then
`cell PLMN 208.93 Cell ID 12345678 is in service`.

Then the UE:

```
sudo ./nr-uesoftmodem -C 3619200000 -r 106 --numerology 1 --band 78 --ssb 516 \
  --rfsim --rfsimulator.[0].serveraddr 127.0.0.1 \
  --uicc0.imsi 208930000000001 \
  --uicc0.key 8baf473f2f8fd09487cccbd7097c6862 \
  --uicc0.opc 8e27b6af0e692e750f32667a3b14605d \
  --uicc0.pdu_sessions.[0].dnn internet \
  --uicc0.pdu_sessions.[0].nssai_sst 1 \
  --uicc0.pdu_sessions.[0].nssai_sd 0x010203 \
  --uecap_file ~/openairinterface5g/targets/PROJECTS/GENERIC-NR-5GC/CONF/uecap_ports1.xml
```

## Address plan

| Link | Address | Port |
|---|---|---|
| N2, CU-CP to AMF | 192.168.6.90 to 10.152.183.216 | SCTP 38412 |
| E1, CU-CP listener | 127.0.20.1 | SCTP 38462 |
| E1, CU-UP | 127.0.20.2 | |
| F1-C, CU-CP listener | 127.0.10.1 | SCTP 38472 |
| F1-C, DU | 127.0.10.2 | |
| F1-U, CU-UP to DU | 127.0.10.1 to 127.0.10.2 | UDP 2152 |
| N3, CU-UP to UPF | 192.168.6.90 to the UPF pod IP | UDP 2152 |

## Four things upstream does not tell you

**`min_rxtxtime` on the CU-CP is rejected.** `doc/E1AP/E1-design.md` says to start the CU-CP
with `--gNBs.[0].min_rxtxtime 6`. On `2026.w33` that aborts:

```
Option "gNBs.[0].servingCellConfigCommon.min_rxtxtime" is not allowed in this config
```

The DU config already carries `min_rxtxtime = 6`, which is where it belongs. Drop the flag.

**Take `--ssb` from the DU, do not guess it.** The DU prints the UE command line it expects:

```
[NR_MAC] Command line parameters for OAI UE: -C 3619200000 -r 106 --numerology 1 --band 78 --ssb 516
```

Passing `--ssb 0` yields `SSB Freq: 0.000000` and the UE never synchronises. Omitting `--ssb`
altogether also failed here. Read the value out of the DU log.

**F1-U ports must match across CU-UP and DU.** The shipped CI configs disagree: the CU-UP
template uses `local_s_portd = 2153` and the DU template uses `local_n_portd = 2153`, but the
CU-CP template uses 2152. Setting only some of them to 2152 produces a split where the control
plane comes up perfectly, the UE registers, gets an IP and brings its TUN up, and **no user
traffic flows at all**, because the CU-UP sends F1-U to port 2152 while the DU listens on 2153.
Nothing logs an error. Set all of them to 2152, as `doc/F1AP/F1-design.md` recommends.

**Reset the AMF before the run, never during.** Killing the UE abruptly leaves UE context in
the AMF, and the next Registration Request is then silently dropped: the UE reaches
RRC_CONNECTED and stops. Restarting the AMF fixes it, but it also breaks the CU-CP's N2
association, so the whole RAN has to be restarted after. Reset the core first, then bring the
RAN up.

## What a working run looks like

```
UE IPv4: 10.60.0.2      TUN oaitun_ue1 up
ping -I oaitun_ue1 8.8.8.8   ->  5 transmitted, 5 received, 0% loss, avg 46 ms
```

## Evidence: OAI writes no control-plane pcaps

OAI's only pcap support is MAC-layer OPT. There is no NGAP/F1AP/E1AP capture, so RANCert
captures the wire itself. Verified working with plain tcpdump plus tshark, with **no
`decode_as` needed** on the standard ports:

```
tcpdump -i any -w cp.pcap sctp

  31  F1AP/NR RRC
  10  NGAP/NAS-5GS
  10  F1AP/NR RRC/NAS-5GS
   6  E1AP
   6  F1AP
   5  NGAP
```

The procedures the catalog asserts on are all present: `UEContextSetupRequest/Response`,
`UEContextModificationRequest/Response`, `UEContextReleaseCommand/Complete`,
`ULRRCMessageTransfer`, `DLRRCMessageTransfer`, `UplinkNASTransport`, `DownlinkNASTransport`,
`UERadioCapabilityInfoIndication`, `Security mode command`, `Security Mode Complete`.

Most importantly the PDCP integrity MAC is exposed, which is what makes AS-security activation
observable rather than merely logged:

```
Registration request       MAC=0x00000000     <- before AS security
Security mode command      MAC=0x00000000
Security Mode Command      MAC=0x0cc12555     <- after
Security Mode Complete     MAC=0x66f56d08
```

## Note on the security configuration

`configs/oai/cucp.conf` keeps OAI's shipped `security` block exactly as it ships it, including
`ciphering_algorithms = ( "nea0" )`. Changing it would be configuring the product under test to
pass a security requirement. Whatever it selects gets measured and reported.
