# UERANSIM end-to-end testing + SD-Core downlink datapath fix

Real gNB+UE (UERANSIM) attached to **OAI** and **SD-Core (BESS)**, plus the fix that
makes SD-Core's downlink work with an *external* gNB. Configs live in
`~/UERANSIM/config/{oai,sdcore}-{gnb,ue}.yaml`; the fix is
`scripts/sdcore_ueransim_datapath_fix.sh`.

## 1. SD-Core downlink fix (run `scripts/sdcore_ueransim_datapath_fix.sh`)

Control plane (register + PDU session) works out of the box on both cores. SD-Core's
**downlink** (internet → UE) does not, because aether's BESS-UPF (DPDK fast path +
macvlan) is wired for its in-cluster gnbsim, not an external host gNB. Three things
must be taught/refreshed — and they go stale every time `upf-0` is recreated:

1. **No route to the external gNB.** BESS encapsulates the downlink GTP-U, then looks
   up the gNB's N3 IP (192.168.252.1) in its `accessRoutes` (IPLookup) — which has no
   entry → packets go to gate 8191 → `accessbad_route` Sink (dropped). Fix: add a
   `/32` route to gate 0 via the pybess gRPC API.
2. **Stale downlink next-hop MAC.** BESS sends downlink to a *static* MAC
   (`accessDstMAC<MAC>` module) captured at UPF init. After a macvlan/pod recreate the
   host's access MAC differs → frames go nowhere. Fix: set the host access iface MAC to
   BESS's static next-hop MAC (or update the BESS module to the host MAC).
3. **Stale ARP for the UPF N6.** After a pod recreate the host's neighbour entry for
   the UPF core (192.168.250.3) points at the *old* MAC → downlink replies from the DN
   never reach the UPF. Fix: `ip neigh replace` with the current coreFast MAC.

Plus host NAT (`ip_forward` + `MASQUERADE` for the UE pool → uplink) and per-netns DNS.

Symptom when broken: control plane OK, **uplink OK, downlink 100% loss, BESS
`accessFast TX` stays 0**. Diagnose with `bessctl show pipeline` (look for packets
piling up at `accessbad_route`).

## 2. Performance comparison (this VM)

| Test | What it measures | SD-Core (BESS af_packet) | OAI (simpleswitch) |
|---|---|---|---|
| **NDR (lossless PPS)** | UPF raw forwarding engine | **~18.7 kpps** | ~3.7–7.5 kpps |
| **Saturation PPS** | UPF max forward w/ loss | **~38 kpps** | ~15 kpps |
| **iperf3 uplink** (UE→net) | e2e single-stream UL | **146 Mbps** | 90 Mbps |
| **iperf3 downlink** (net→UE) | e2e single-stream DL | 1.76 Mbps | **108 Mbps** |
| **speedtest download** | e2e multi-stream DL (real internet) | 18.7 Mbps | **44 Mbps** |
| **speedtest upload** | e2e multi-stream UL | 51 Mbps | 52 Mbps |

(PPS numbers are from the framework's raw-max profile, both UPFs driven identically via
pfcpsim. e2e numbers are through the real UERANSIM gNB+UE.)

## 3. Why SD-Core is faster in PPS but weaker in the real internet (e2e) test

**They measure different things.**

- **PPS = the UPF's forwarding engine.** BESS is a DPDK poll-mode datapath → high PPS.
  OAI "simpleswitch" forwards through a Linux TUN in userspace → low PPS. BESS wins by
  ~2.5–5×. This is a real, intrinsic UPF-capability difference.
- **The e2e internet test = the whole path**: external gNB ↔ UPF *datapath integration*
  ↔ DN/NAT ↔ internet. Here the bottleneck is **not** BESS's forwarding engine — it's
  the **downlink delivery from BESS's DPDK+macvlan datapath to an external, host-resident
  gNB**. aether's BESS-UPF is built for an in-cluster gnbsim (pod↔pod on the access
  net). Bolting on an external UERANSIM gNB means downlink frames cross a DPDK→macvlan→
  kernel→gNB boundary, which drops packets under bursty load. OAI uses a plain Docker
  bridge + kernel datapath, which an external gNB integrates with cleanly → clean
  downlink.

The proof it's the downlink *integration*, not the engine: SD-Core **uplink** hits
146 Mbps (faster than OAI), while its **downlink** is lossy. The asymmetry points
straight at the BESS→external-gNB downlink direction, not at BESS forwarding in general.
In aether's intended topology (in-cluster gnbsim) BESS downlink is clean and fast.

**Bottom line:** BESS has the stronger forwarding engine (PPS); OAI has the friendlier
datapath for an *external* gNB in this lab topology. Different layers, different winners.

## 4. Why speedtest = 18.7 Mbps but iperf = 1.76 Mbps on the *same* SD-Core path

**Single-stream vs multi-stream TCP over a path that has packet loss.**

- `iperf3` (default) uses **one** TCP stream. A single TCP flow's throughput is roughly
  `MSS / (RTT × √loss)` (Mathis equation). With even a little downlink loss, the one
  stream halves its window on every loss and ramps back slowly — it can't keep the pipe
  full, so it collapses to ~1.76 Mbps. (Local-RTT iperf was still ~1.76 Mbps, so it's
  loss-driven, not latency-driven.)
- Ookla **speedtest** opens **multiple parallel** TCP streams. Loss is spread across
  them; when one backs off the others keep flowing, so collectively they keep far more
  data in flight → ~18.7 Mbps aggregate.

So the **path can carry ~18–20 Mbps**; a *single* TCP stream just can't exploit it
because of the loss. Real apps, browsers, and production speed tests use many
connections → they see the ~20 Mbps. That's also why our speedtest (18.7) matches the
live ~20–25 Mbps while single-stream iperf does not.

Contrast OAI: its downlink is ~lossless (clean bridge), so even a **single** iperf stream
reaches 108 Mbps. The loss on SD-Core's external-gNB downlink is the whole reason its
single-stream number collapses.
