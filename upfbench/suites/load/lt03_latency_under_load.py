"""LT-03 Latency / jitter vs UE count & offered load — Suite 2 (multi-UE load).

Reuses the in-pipeline latency probe (pktParse->executeFAR) from TC-03, but drives
N-UE GTP-U load (real pfcpsim sessions, matched TEIDs/UE-IPs) and reports how latency
degrades as the UE count rises. The probe is inserted once and removed once (outside
the session activity) to avoid pausing the pipeline while sessions are active.
"""
from __future__ import annotations

from upfbench.suites.base import TestCase, RunContext
from upfbench.results import TestResult
from upfbench.suites.load._flows import session_flows

# Distinct baseID per LT test -> distinct TEID ranges (avoids cross-test residual state).
_BASE = 200001


def _needs(ctx) -> bool:
    return (ctx.control is None or ctx.traffic is None
            or not hasattr(ctx.control, "create_sessions"))


class Lt03LatencyUnderLoad(TestCase):
    id, name = "LT-03", "Latency / jitter vs UE count & offered load"

    def run(self, ctx: RunContext) -> TestResult:
        if _needs(ctx):
            return TestResult(self.id, self.name, "error",
                              notes="needs pfcpsim control + a traffic generator")
        if not hasattr(ctx.upf, "latency_probe_install"):
            # Same in-pipeline-probe requirement as TC-03; UPFs without one
            # (e.g. OAI-UPF) need a black-box RTT method (separate work).
            return TestResult(self.id, self.name, "skipped",
                              notes="no in-pipeline latency probe on this UPF "
                                    "(needs a black-box RTT measurement; not yet implemented).")
        k = ctx.knobs
        ue_counts = k.get("lt03_ue_counts", [1, 10, 100])
        fs = int(k.get("lt03_frame_size", 512))
        load = float(k.get("lt03_load_mpps", 0.03))
        dur = int(k.get("lt03_duration_s", 6))
        pcts = [50, 90, 99, 99.9]

        ctx.control.ensure_associated()
        ctx.upf.latency_probe_install()
        rows = []
        try:
            for n in ue_counts:
                est = ctx.control.create_sessions(count=n, base_id=_BASE)
                if not est["ok"]:
                    rows.append({"ues": n, "error": "session install failed"})
                    continue
                teids, ue_ips = session_flows(_BASE, n, ctx.control.ue_pool)
                # warm-up so forwarding is established before measuring (the first
                # iteration can be cold after prior heavy session churn in the suite)
                ctx.traffic.run_trial(frame_size=fs, offered_mpps=load, duration_s=2,
                                      teids=teids, ue_ips=ue_ips)
                ctx.upf.latency_read(pcts, clear=True)          # reset before this load
                ctx.traffic.run_trial(frame_size=fs, offered_mpps=load, duration_s=dur,
                                      teids=teids, ue_ips=ue_ips)
                s = ctx.upf.latency_read(pcts)
                ctx.control.delete_sessions_raw(count=n, base_id=_BASE)
                us = lambda ns: round(ns / 1000.0, 3)
                pv = s.get("pct_ns", [])
                rows.append({"ues": n, "samples": s["packets"],
                             "avg_us": us(s["avg_ns"]),
                             "p50_us": us(pv[0]) if len(pv) > 0 else None,
                             "p99_us": us(pv[2]) if len(pv) > 2 else None})
        finally:
            ctx.upf.latency_probe_remove()

        return TestResult(self.id, self.name, status="measured",
                          tables={"Latency vs UE count (in-pipeline, us)": rows},
                          notes=f"in-pipeline pktParse->executeFAR latency at ~{load} Mpps "
                                f"offered, UE counts {ue_counts}; shows degradation with load.")


TESTS = [Lt03LatencyUnderLoad]
