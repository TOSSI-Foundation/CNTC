# ranbench rig: bringing up an OCUDU split gNB for certification

How to stand up the RAN under test and the two peers it needs (a UE below it, a 5G core above
it), and what had to be reconciled to make an attach work. Everything here was verified live;
the observed evidence is listed at the end.

Topology:

```
  OAI nr-UE ──ZMQ──► O-DU ──F1-C (F1AP)──► O-CU-CP ──N2 (NGAP)──► free5GC AMF (Kubernetes)
                       └────F1-U (GTP-U)──► O-CU-UP ──N3 (GTP-U)──► free5GC UPF ──► internet
                                                 ▲
                                                 └── E1 (E1AP) from the O-CU-CP
```

## 1. The RAN under test: OCUDU

OCUDU (Linux Foundation, srsRAN lineage, BSD-3-Clause-Open-MPI) builds the three split-gNB
product classes as separate applications: `ocucp`, `ocuup`, `odu`.

```bash
cd ~/ocudu && mkdir -p build && cd build
cmake .. -DCMAKE_BUILD_TYPE=Release -DENABLE_ZEROMQ=ON -DENABLE_UHD=OFF -DENABLE_DPDK=OFF -DBUILD_TESTS=OFF
make -j$(nproc) ocucp ocuup odu
```

UHD and DPDK are off because the ZeroMQ virtual radio needs neither; that keeps the build to
about 530 MB. Deps: `cmake g++ libfftw3-dev libmbedtls-dev libsctp-dev libyaml-cpp-dev
libgtest-dev libzmq3-dev`.

Rig configs live in `configs/ocudu/` and are what `ranbench` parses for endpoints and pcaps.

## 2. The UE: OAI nr-UE over ZeroMQ

```bash
git clone --depth 1 https://gitlab.eurecom.fr/oai/openairinterface5g.git
cd openairinterface5g/cmake_targets && sudo ./build_oai -I --nrUE -w SIMU --ninja
cd ran_build/build && sudo cmake . -DOAI_ZMQ=ON && sudo cmake --build . --target oai_zmqdevif
```

**`-DOAI_ZMQ=ON` is required.** OAI gates its ZeroMQ radio behind that option and it defaults
OFF, so `--target oai_zmqdevif` fails with "unknown target" until cmake is reconfigured.
`nr-softmodem` is OAI's gNB and is not needed, OCUDU is the gNB here.

Run (the flags are not interchangeable with the ones in OAI's own tutorials, see §3):

```bash
sudo ./nr-uesoftmodem -r 51 --numerology 1 --band 78 -C 3489420000 --ssb 0 \
  --uicc0.imsi 208930000000001 \
  --uicc0.key 8baf473f2f8fd09487cccbd7097c6862 \
  --uicc0.opc 8e27b6af0e692e750f32667a3b14605d \
  --uicc0.pdu_sessions.[0].dnn internet \
  --uicc0.pdu_sessions.[0].nssai_sst 1 \
  --uicc0.pdu_sessions.[0].nssai_sd 0x010203 \
  --zmq.[0].tx_channels tcp://127.0.0.1:4557 \
  --zmq.[0].rx_channels tcp://127.0.0.1:4556 \
  --device.name oai_zmqdevif \
  --uecap_file <oai>/targets/PROJECTS/GENERIC-NR-5GC/CONF/uecap_ports1.xml
```

## 3. What has to line up between the DU and the UE

| Parameter | Value | Why it matters |
|---|---|---|
| Centre frequency | DU `dl_arfcn: 632628` = UE `-C 3489420000` | ARFCN 632628 is 3489.42 MHz in band 78 |
| Bandwidth / PRBs | DU 20 MHz @ SCS 30 = UE `-r 51` | 20 MHz at 30 kHz is 51 PRB; 106 PRB is a 40 MHz cell |
| **Sample rate** | DU `srate: 30.72` + `base_srate=30.72e6` = UE default for this cell | The OAI UE runs this cell natively at 30.72 Msps while the OCUDU examples use 23.04. Mismatched, the UE finds the SSB but **never decodes the PBCH**. The `-E` three-quarter-sampling path that would instead drop the UE to 23.04 left its PHY silent, so match upward |
| SSB position | UE `--ssb 0` | The DU reports `SSB offset pointA: 0, k_SSB: 0, SSB arfcn: 632256`. `--ssb` is a *subcarrier* offset; the widely-quoted `--ssb 42` belongs to a 106-PRB cell |
| ZMQ ports | DU tx 4556 / rx 4557, UE tx 4557 / rx 4556 | tx binds, rx connects, they cross |
| PLMN / TAC / S-NSSAI | 20893 / 1 / sst 1 sd 010203 | Must match what the AMF advertises or NG Setup and registration are rejected |
| UE capabilities | `--uecap_file …/uecap_ports1.xml` | Without it OAI asserts `(max_mimo_layers > 0)` immediately after RRC Setup |
| PDU session flags | `--uicc0.pdu_sessions.[0].*` | `--uicc0.dnn` is silently ignored: the UE registers but never requests a session |
| CU-CP timer | `request_pdu_session_timeout: 20` | Default 3 s is shorter than the OAI UE takes; the CU-CP releases the UE first |

**The ZMQ link is stateful.** If either end dies mid-stream the other stalls on "Waiting for
reading samples", so the DU and UE must be started as a pair, and a dead UE means restarting
the DU.

**OCUDU flushes pcaps only at shutdown** ("Closing PCAP files..." on SIGINT), so evidence is
incomplete while the stack runs, teardown is part of the measurement, not just cleanup.

## 4. Core prerequisites (free5GC on Kubernetes)

Two things had to be fixed before any PDU session could succeed. Both are core-side, not RAN.

**gtp5g kernel module.** The UPF pod crash-loops with `open Gtp5g: open link: create: operation
not supported` when the module is missing or built for a different kernel. It had been built
for 5.15.0-185 while the host runs 5.15.0-187:

```bash
cd ~/gtp5g && make clean && make && sudo make install   # depmod + modprobe + /etc/modules-load.d
kubectl -n free5gc delete pod -l app.kubernetes.io/name=free5gc-upf
```

**SMF userplane topology.** The SMF was provisioned for a three-UPF ULCL topology
(`iupf1` + `psaupf1` + `psaupf2`) that is not deployed. Its wrapper retried DNS, gave up, and
substituted the literal placeholder hostnames, so it held no PFCP association and every
`CreateSmContext` returned HTTP 500. Reconfigured to the single UPF that is actually deployed:
`userplaneInformation.upNodes` = `gNB1` (AN) + `UPF1`, one `gNB1 -> UPF1` link, `ueRoutingInfo`
emptied (its ULCL paths named the removed UPFs), and `wrapper.sh` resolving
`free5gc-helm-free5gc-upf-upf-service`.

Take care editing that config as text, not by YAML round-trip: `sd: 010203` is **octal in
YAML 1.1**, so a parse-and-redump turns it into `4227` and the SMF rejects it with
`Invalid sNssai.Sd`.

**UDM losing its connection to the UDR.** After a long period of uptime the UDM can be left
holding a dead HTTP/2 connection to the UDR, with neither pod having restarted. The SMF's
`Get SessionManagementSubscriptionData` then returns 500 and every PDU Session Establishment is
refused. It is an expensive failure to diagnose from the RAN side, because registration still
succeeds and only the data path is missing:

```
[ERRO][UDM][Proc] QuerySmData Error: Get "http://...udr-service:8080/nudr-dr/v2/..."
                  : http2: client conn could not be established
kubectl -n free5gc rollout restart deploy/<udr> deploy/<udm>
```

`ranbench doctor` now makes the same request the SMF will make and reports it as
`subscriber data`, so this is caught in a second rather than after a full campaign.

**Stale UE context in the AMF.** free5GC's AMF keeps registration state in memory. When a
campaign ends abruptly, the UE can be left in `DeregistrationInitiated`, and the AMF then
rejects the next Registration Request. The attach never completes, every requirement judged on
that attach records `na`, and the run looks like a failing RAN when in fact nothing was
measured. This was the largest single source of run-to-run variance.

`ranbench` therefore restarts the AMF before each campaign and waits for N2 to accept again, so
every run starts from a defined registration state. It costs about a minute. Disable it with
`core.reset_amf: false` in the campaign config if you manage core state yourself.

## 5. Verified result

A full attach with a working data path, observed end to end:

```
UE   synchronized (PBCH) -> SIB1 decoded -> PRACH/RAR/Msg3 -> RRC Setup -> RRC_CONNECTED
     Registration Request -> Authentication Request -> Registration Accept -> Registration Complete
     PDU Session Establishment Accept, UE IPv4 10.60.0.1 -> TUN oaitun_ue1 up
ping -I oaitun_ue1 8.8.8.8   ->   4 packets transmitted, 4 received, 0% packet loss
```

Decoded from the pcaps the stack writes itself:

| Interface | Evidence |
|---|---|
| NGAP (N2) | NGSetup · InitialUEMessage(Registration request) · Authentication request/response · Security mode command · InitialContextSetup · **PDUSessionResourceSetup Request/Response** · UEContextRelease |
| F1AP (F1-C) | F1Setup(**MIB, SIB1**) · InitialULRRCMessageTransfer(RRC Setup Request) · RRC Setup · RRC Setup Complete · UEContextSetup Request/Response · UEContextRelease |
| E1AP (E1) | GNB-CU-UP-E1Setup · **BearerContextSetup Request/Response** · BearerContextModification Request/Response |
| F1-U | GTP T-PDUs |
| N3 | `10.60.0.1 -> 8.8.8.8 GTP <ICMP> Echo request` |

**AS security is directly observable in the F1AP decode.** Every message up to and including
Security Mode Command carries `MAC=0x00000000`; Security Mode Complete and everything after it
carries a real MAC (`0x0b8cc0c8`, `0xb9246970`, …) and the NAS content is no longer decodable.
Integrity activation and ciphering are therefore provable without any radio-side capture or key
material, which is the reason this stage is scoped to a CU/DU split rather than a monolithic
gNB.

---

*TOSSI Foundation · CNTC Stage 3*
