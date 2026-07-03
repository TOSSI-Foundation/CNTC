"""TC-02 Bidirectional throughput (UL+DL) — Suite 1 (performance). RFC 2544 §3.10.

Drives uplink and downlink simultaneously and measures each direction:
  * UPLINK   — GTP-U on N3 (access VF), the UPF decaps -> N6/core TX.
  * DOWNLINK — plain IP (dst = UE-IP) on N6 (a 2nd gen VF on the core PF), the UPF runs the
               full DL fast-path (coreMetadata -> pktParse -> pdrLookup -> QER -> FAR) -> N3/
               access TX.

How the datapath is closed (validated empirically, see the adapter):
  * A real pfcpsim session installs the UL PDR (so UL matches and decaps).
  * ``egress_shortcircuit_install`` forces the UL FAR egress to core (no real N6 next-hop).
  * ``downlink_enable`` forces the DL egress to access. On this BESS build a synthetic DL
    packet has no forwarding DL FAR, so it lands on executeFAR's farDrop gate AFTER the
    whole DL fast-path has run; we redirect that gate to the access port and count it there.
    Caveat reported in the result: DL therefore excludes the final GTP *encapsulation* push
    (the FAR said drop), so DL is the DL ingress+lookup+QER cost, slightly under full encap.

Needs pfcpsim (UL session) + the TRex generator's two ports. Skips cleanly if absent.
"""
from __future__ import annotations

from upfbench.suites.base import TestCase, RunContext
from upfbench.results import TestResult

_DEFAULT_FRAMES = [128, 256, 512, 1024, 1518]


class Tc02Bidirectional(TestCase):
    id, name = "TC-02", "Bidirectional throughput (UL+DL)"

    def run(self, ctx: RunContext) -> TestResult:
        gen = ctx.traffic
        if gen is None or not hasattr(gen, "run_bidir"):
            return TestResult(self.id, self.name, "skipped",
                              notes="bidirectional needs the TRex 2-port generator (DPDK/XDP/CNDP "
                                    "modes); not available with this UPF's generator (e.g. OAI "
                                    "simpleswitch / tcpreplay).")
        if not (hasattr(ctx.upf, "downlink_enable") and hasattr(ctx.upf, "egress_shortcircuit_install")):
            return TestResult(self.id, self.name, "skipped",
                              notes="adapter has no downlink short-circuit (non-BESS UPF)")
        # Bidirectional needs a real UL session (pfcpsim), independent of the perf suite's
        # pybess control (whose forward-all wildcard would send DL out core, not access).
        try:
            from upfbench.control.base import load_control
            ctl = load_control("pfcpsim", ctx.cfg.upf, ctx.store)
        except Exception as e:
            return TestResult(self.id, self.name, "skipped",
                              notes=f"pfcpsim unavailable: {e}")

        k = ctx.knobs
        frames = k.get("tc02_frame_sizes", k.get("frame_sizes", _DEFAULT_FRAMES))
        ul_mpps = float(k.get("tc02_ul_mpps", 3.0))
        dl_mpps = float(k.get("tc02_dl_mpps", 3.0))
        dur = int(k.get("tc02_duration_s", 6))
        ue_count = int(k.get("tc02_ue_count", 1))
        base_id = int(k.get("tc02_base_id", 400001))

        # Bidirectional needs a clean, pybess-free pipeline: the perf suite's pybess
        # forward-all wildcard (TC-01) would match DL packets and send them out core, and the
        # other tests' WildcardMatch tuples can ENOSPC under churn. This test is ordered LAST
        # in the suite (file tc09_) and resets to a fresh bessd, so TC-01/03/04/08 run fully
        # undisturbed before it and nothing runs after it.
        ctx.upf.reset()
        n3 = self._port(ctx, ctx.cfg.upf.n3_iface or "access")
        n6 = self._port(ctx, ctx.cfg.upf.n6_iface or "core")
        ff = ctx.upf.fwd_field()

        ctl.setup(); ctl.ensure_associated(); ctl.create_sessions(count=ue_count, base_id=base_id)
        teids, ue_ips = ctl.aligned_flows(count=ue_count, base_id=base_id)
        ctx.upf.egress_shortcircuit_install()
        ctx.upf.downlink_enable()

        rows = []
        peak_aggr = 0.0
        try:
            for fs in frames:
                before = ctx.upf.port_counters()
                r = gen.run_bidir(frame_size=fs, ul_mpps=ul_mpps, dl_mpps=dl_mpps,
                                  duration_s=dur, teids=teids, ue_ips=ue_ips)
                after = ctx.upf.port_counters()
                secs = r["secs"] or dur
                ul_fwd = after[n6][ff] - before[n6][ff]
                dl_fwd = after[n3][ff] - before[n3][ff]
                ul_mp = round(ul_fwd / secs / 1e6, 3)
                dl_mp = round(dl_fwd / secs / 1e6, 3)
                aggr = round(ul_mp + dl_mp, 3)
                peak_aggr = max(peak_aggr, aggr)
                rows.append({
                    "frame_B": fs,
                    "UL_off_Mpps": round(r["ul_sent"] / secs / 1e6, 3),
                    "UL_fwd_Mpps": ul_mp, "UL_loss_%": self._loss(r["ul_sent"], ul_fwd),
                    "DL_off_Mpps": round(r["dl_sent"] / secs / 1e6, 3),
                    "DL_fwd_Mpps": dl_mp, "DL_loss_%": self._loss(r["dl_sent"], dl_fwd),
                    "aggregate_Mpps": aggr,
                })
        finally:
            try: ctx.upf.downlink_disable()
            except Exception: pass
            try: ctx.upf.egress_shortcircuit_remove()
            except Exception: pass
            try: ctl.delete_sessions_raw(count=ue_count, base_id=base_id)
            except Exception: pass

        ctx.store.set_kpi("bidir_aggregate_mpps", peak_aggr)
        return TestResult(self.id, self.name, status="measured",
            metrics={"peak_aggregate_mpps": peak_aggr, "ul_offered_mpps": ul_mpps,
                     "dl_offered_mpps": dl_mpps},
            tables={"Bidirectional UL+DL throughput": rows},
            notes=f"Simultaneous UL (GTP-U N3->core) + DL (plain N6->access) at {ul_mpps}+"
                  f"{dl_mpps} Mpps offered, {dur}s/frame. UL = real decap forward; DL = full "
                  f"DL fast-path (parse+PDR+QER+FAR) egress-forced at the FAR, so DL excludes "
                  f"the final GTP encap push. Aggregate peak {peak_aggr} Mpps.")

    @staticmethod
    def _loss(sent, fwd):
        return round(max(0.0, (sent - fwd) / sent) * 100, 3) if sent else 100.0

    @staticmethod
    def _port(ctx: RunContext, prefix: str) -> str:
        for key in ctx.upf.port_counters():
            if key.startswith(prefix):
                return key
        raise RuntimeError(f"no UPF port matching {prefix!r}")


TESTS = [Tc02Bidirectional]
