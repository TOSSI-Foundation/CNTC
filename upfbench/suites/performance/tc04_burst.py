"""TC-04 Burst / back-to-back (+ drain) — Suite 1 (performance).

Saturating short blast: offer well above capacity for a few seconds and measure the
sustained absorbed/forwarded rate, the pipeline drop count (absorbed - forwarded,
expected ~0 for af_packet — it forwards everything it manages to absorb), and the
drain tail (packets still leaving the core port in the second AFTER the blast stops;
~0 means no queue build-up). Mirrors the reference TC-04.
"""
from __future__ import annotations

import time

from upfbench.suites.base import TestCase, RunContext
from upfbench.results import TestResult


class Tc04Burst(TestCase):
    id, name = "TC-04", "Burst / back-to-back (+ drain)"

    def run(self, ctx: RunContext) -> TestResult:
        if ctx.traffic is None or ctx.control is None:
            return TestResult(self.id, self.name, "error",
                              notes="needs a control + traffic plugin (pybess + tcpreplay)")
        k = ctx.knobs
        fs = int(k.get("burst_frame_size", 512))
        load = float(k.get("burst_load_mpps", 0.15))     # >> capacity -> saturate
        dur = int(k.get("burst_duration_s", 5))
        n3 = self._port(ctx, ctx.cfg.upf.n3_iface or "access")
        n6 = self._port(ctx, ctx.cfg.upf.n6_iface or "core")
        ff = ctx.upf.fwd_field()   # 'tx_pkts' (BESS) or 'rx_pkts' (OAI tun N6)
        teids, ue_ips = ctx.control.aligned_flows()

        ctx.control.install_sessions()
        before = ctx.upf.port_counters()
        t = ctx.traffic.run_trial(frame_size=fs, offered_mpps=load, duration_s=dur,
                                  teids=teids, ue_ips=ue_ips)
        after = ctx.upf.port_counters()
        # drain window: measure what still leaves the core port after the blast stops.
        time.sleep(1.0)
        drained = ctx.upf.port_counters()

        absorbed = after[n3]["rx_pkts"] - before[n3]["rx_pkts"]
        forwarded = after[n6][ff] - before[n6][ff]
        drain_tail = drained[n6][ff] - after[n6][ff]
        secs = t.duration_s or 1.0
        row = {
            "frame_B": fs,
            "offered_Mpps": t.offered_mpps,
            "absorbed_Mpps": round(absorbed / secs / 1e6, 4),
            "forwarded_Mpps": round(forwarded / secs / 1e6, 4),
            "pipeline_drops": max(0, absorbed - forwarded),
            "drain_tail_pkts": drain_tail,
        }
        return TestResult(self.id, self.name, status="measured",
                          metrics={"sustained_fwd_mpps": row["forwarded_Mpps"],
                                   "pipeline_drops": row["pipeline_drops"]},
                          tables={"Burst (saturation)": [row]},
                          notes=f"{dur}s saturating blast at ~{t.offered_mpps} Mpps offered; "
                                f"pipeline_drops = absorbed-forwarded; drain_tail = core TX "
                                f"in the 1s after stop.")

    @staticmethod
    def _port(ctx: RunContext, prefix: str) -> str:
        for key in ctx.upf.port_counters():
            if key.startswith(prefix):
                return key
        raise RuntimeError(f"no UPF port matching {prefix!r}")


TESTS = [Tc04Burst]
