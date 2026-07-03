"""TC-08 Multi-flow (RSS) — Suite 1 (performance).

Saturating blast with N distinct flows (one GTP-U TEID + UE source IP per flow) to see
whether flow diversity changes the sustained rate. For af_packet it should not — the path
is a single kernel/socket funnel (pps-bound), so multi-flow ~= single-flow (the reference
saw the same). For a NIC with RSS this is where multiple queues would help.
"""
from __future__ import annotations

from upfbench.suites.base import TestCase, RunContext
from upfbench.results import TestResult


class Tc08Multiflow(TestCase):
    id, name = "TC-08", "Multi-flow (RSS)"

    def run(self, ctx: RunContext) -> TestResult:
        if ctx.traffic is None or ctx.control is None:
            return TestResult(self.id, self.name, "error",
                              notes="needs a control + traffic plugin (pybess + tcpreplay)")
        k = ctx.knobs
        fs = int(k.get("multiflow_frame_size", 512))
        flows = int(k.get("multiflow_flows", 16))
        load = float(k.get("multiflow_load_mpps", 0.15))   # saturate
        dur = int(k.get("multiflow_duration_s", 5))
        n3 = self._port(ctx, ctx.cfg.upf.n3_iface or "access")
        n6 = self._port(ctx, ctx.cfg.upf.n6_iface or "core")
        ff = ctx.upf.fwd_field()   # 'tx_pkts' (BESS) or 'rx_pkts' (OAI tun N6)

        # A control with per-session PDRs (pfcpsim) needs `flows` real sessions and
        # their exact (TEID, UE-IP) pairs; a wildcard control (pybess) installs one
        # rule and we synthesise distinct TEIDs to exercise flow diversity.
        teids, ue_ips = ctx.control.aligned_flows(count=flows, base_id=1)
        if teids is not None:
            ctx.control.install_sessions(count=flows, base_id=1)
        else:
            ctx.control.install_sessions()
            teids, ue_ips = list(range(1000, 1000 + flows)), None
        before = ctx.upf.port_counters()
        t = ctx.traffic.run_trial(frame_size=fs, offered_mpps=load, duration_s=dur,
                                  teids=teids, ue_ips=ue_ips)
        after = ctx.upf.port_counters()
        absorbed = after[n3]["rx_pkts"] - before[n3]["rx_pkts"]
        forwarded = after[n6][ff] - before[n6][ff]
        secs = t.duration_s or 1.0
        row = {"frame_B": fs, "flows": flows, "offered_Mpps": t.offered_mpps,
               "absorbed_Mpps": round(absorbed / secs / 1e6, 4),
               "forwarded_Mpps": round(forwarded / secs / 1e6, 4),
               "pipeline_drops": max(0, absorbed - forwarded)}
        return TestResult(self.id, self.name, status="measured",
                          metrics={"forwarded_mpps": row["forwarded_Mpps"], "flows": flows},
                          tables={"Multi-flow (saturation)": [row]},
                          notes=f"{flows} flows (distinct TEID+UE IP), {dur}s saturating "
                                f"blast; compare forwarded_Mpps to single-flow TC-04.")

    @staticmethod
    def _port(ctx: RunContext, prefix: str) -> str:
        for key in ctx.upf.port_counters():
            if key.startswith(prefix):
                return key
        raise RuntimeError(f"no UPF port matching {prefix!r}")


TESTS = [Tc08Multiflow]
