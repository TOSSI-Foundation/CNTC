"""LT-02 Aggregate + per-UE throughput under N UEs — Suite 2 (multi-UE load).

Two measurements:
  1. Aggregate load: install N real PFCP sessions (pfcpsim), drive GTP-U on all N
     matching TEIDs/UE-IPs at saturation, read aggregate forwarded from core TX.
  2. Per-UE verification: drive a subset of UEs one-at-a-time at a low (no-drop) rate
     and read each one's forwarded count from core TX — confirms every UE's session
     actually forwards (not just the aggregate) and shows per-UE consistency.

True per-UE share *under simultaneous load* would need per-flow counters (BESS
FlowMeasure) — noted as a follow-up; the aggregate's per-UE figure is the fair-share
average.
"""
from __future__ import annotations

from upfbench.suites.base import TestCase, RunContext
from upfbench.results import TestResult
from upfbench.suites.load._flows import session_flows

# Distinct baseID per LT test -> distinct TEID ranges, so leftover state from one test's
# (imperfectly cleaned) sessions can't contaminate another's forwarding.
_BASE = 100001


def _needs(ctx) -> bool:
    return (ctx.control is None or ctx.traffic is None
            or not hasattr(ctx.control, "create_sessions"))


class Lt02ThroughputPerUe(TestCase):
    id, name = "LT-02", "Aggregate + per-UE throughput under N UEs"

    def run(self, ctx: RunContext) -> TestResult:
        if _needs(ctx):
            return TestResult(self.id, self.name, "error",
                              notes="needs pfcpsim control + a traffic generator")
        k = ctx.knobs
        n = int(k.get("lt02_ue_count", 100))
        fs = int(k.get("lt02_frame_size", 512))
        load = float(k.get("lt02_load_mpps", 0.15))
        dur = int(k.get("lt02_duration_s", 6))
        verify_ues = int(k.get("lt02_verify_ues", 8))
        verify_rate = float(k.get("lt02_verify_rate_mpps", 0.005))
        verify_dur = int(k.get("lt02_verify_dur_s", 2))
        n6 = self._port(ctx, ctx.cfg.upf.n6_iface or "core")

        ctx.control.ensure_associated()
        est = ctx.control.create_sessions(count=n, base_id=_BASE)
        if not est["ok"]:
            return TestResult(self.id, self.name, "error",
                              notes=f"could not install {n} sessions")
        teids, ue_ips = session_flows(_BASE, n, ctx.control.ue_pool)
        ff = ctx.upf.fwd_field()   # 'tx_pkts' (BESS) or 'rx_pkts' (OAI tun N6)
        tx = lambda: ctx.upf.port_counters()[n6][ff]
        # BESS forwards synthetic test traffic only with the egress short-circuit (the
        # made-up inner dst has no real N6 route); apply it ON TOP of pfcpsim's per-UE FARs
        # so forwarded packets reach core TX. No-op on adapters that don't expose it.
        sc = hasattr(ctx.upf, "egress_shortcircuit_install")
        if sc:
            ctx.upf.egress_shortcircuit_install()
        try:
            # 1) aggregate under simultaneous load
            b = tx()
            t = ctx.traffic.run_trial(frame_size=fs, offered_mpps=load, duration_s=dur,
                                      teids=teids, ue_ips=ue_ips)
            agg_fwd = tx() - b
            secs = t.duration_s or 1.0
            agg_mpps = agg_fwd / secs / 1e6

            # 2) per-UE verification (sequential, low rate so each should forward 100%)
            m = min(n, verify_ues)
            per_ue, all_fwd = [], True
            for i in range(m):
                bb = tx()
                vt = ctx.traffic.run_trial(frame_size=fs, offered_mpps=verify_rate,
                                           duration_s=verify_dur,
                                           teids=[teids[i]], ue_ips=[ue_ips[i]])
                f = tx() - bb
                all_fwd = all_fwd and f > 0
                per_ue.append({"ue": i + 1, "teid": teids[i], "ue_ip": ue_ips[i],
                               "sent": vt.sent_pkts, "forwarded": f})
        finally:
            ctx.control.delete_sessions_raw(count=n, base_id=_BASE)
            if sc:
                ctx.upf.egress_shortcircuit_remove()

        agg_gbps = agg_mpps * fs * 8 / 1e3
        agg = {"ues": n, "offered_Mpps": t.offered_mpps,
               "aggregate_Mpps": round(agg_mpps, 4), "aggregate_Gbps": round(agg_gbps, 4),
               "per_ue_avg_Mbps": round(agg_gbps * 1000.0 / n, 4) if n else 0.0}
        fwd_counts = [p["forwarded"] for p in per_ue]
        ctx.store.set_kpi("load_aggregate_mpps", round(agg_mpps, 4))
        return TestResult(self.id, self.name, status="measured",
                          metrics={"aggregate_mpps": round(agg_mpps, 4), "ues": n,
                                   "verified_ues": len(per_ue),
                                   "all_verified_ues_forwarded": all_fwd},
                          tables={"Aggregate load throughput": [agg],
                                  "Per-UE forwarding verification": per_ue},
                          notes=f"{n} UEs (pfcpsim sessions, matched TEID/UE-IP). Aggregate "
                                f"at saturation; per_ue_avg = fair-share. Verified the first "
                                f"{len(per_ue)} UEs forward individually (all_forwarded="
                                f"{all_fwd}); fwd counts {min(fwd_counts) if fwd_counts else 0}.."
                                f"{max(fwd_counts) if fwd_counts else 0}.")

    @staticmethod
    def _port(ctx: RunContext, prefix: str) -> str:
        for key in ctx.upf.port_counters():
            if key.startswith(prefix):
                return key
        raise RuntimeError(f"no UPF port matching {prefix!r}")


TESTS = [Lt02ThroughputPerUe]
