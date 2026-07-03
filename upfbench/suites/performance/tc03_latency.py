"""TC-03 Latency / jitter — Suite 1 (performance).

Measures the in-pipeline latency of the uplink segment pktParse->executeFAR (the
PDR/FAR/QER lookup cost) by splicing a BESS Timestamp+Measure pair into that path
(adapter.latency_probe_*), pushing steady low-rate traffic, then reading latency and
jitter percentiles. Measured at a low offered rate (<= NDR) so the number reflects the
pipeline cost, not queue build-up at saturation (RFC 8219 latency methodology).
"""
from __future__ import annotations

from upfbench.suites.base import TestCase, RunContext
from upfbench.results import TestResult


class Tc03Latency(TestCase):
    id, name = "TC-03", "Latency / jitter (in-pipeline pktParse->executeFAR)"

    def run(self, ctx: RunContext) -> TestResult:
        if ctx.traffic is None or ctx.control is None:
            return TestResult(self.id, self.name, "error",
                              notes="needs a control + traffic plugin (pybess + tcpreplay)")
        if not hasattr(ctx.upf, "latency_probe_install"):
            # In-pipeline latency needs a white-box probe spliced into the datapath
            # (BESS Timestamp+Measure). UPFs without one (e.g. OAI-UPF simpleswitch)
            # require a black-box RTT method instead — tracked as separate work.
            return TestResult(self.id, self.name, "skipped",
                              notes="no in-pipeline latency probe on this UPF "
                                    "(needs a black-box RTT measurement; not yet implemented).")
        k = ctx.knobs
        fs = int(k.get("latency_frame_size", 512))
        load = float(k.get("latency_load_mpps", 0.01))    # low load: minimal queueing
        dur = int(k.get("latency_duration_s", 8))
        pcts = k.get("latency_percentiles", [50, 90, 99, 99.9])
        jpcts = k.get("jitter_percentiles", [50, 99])

        ctx.control.install_sessions()
        ctx.upf.latency_probe_install()
        try:
            ctx.upf.latency_read(pcts, jpcts, clear=True)      # reset counters
            t = ctx.traffic.run_trial(frame_size=fs, offered_mpps=load, duration_s=dur)
            s = ctx.upf.latency_read(pcts, jpcts, clear=False)
        finally:
            ctx.upf.latency_probe_remove()

        def us(ns) -> float:
            return round(ns / 1000.0, 3)

        pct_us = {f"p{p}_us": us(v) for p, v in zip(pcts, s.get("pct_ns", []))}
        row = {"segment": "pktParse->executeFAR", "offered_Mpps": t.offered_mpps,
               "samples": s["packets"], "min_us": us(s["min_ns"]),
               "avg_us": us(s["avg_ns"]), **pct_us, "max_us": us(s["max_ns"]),
               "jitter_avg_us": us(s["jitter_avg_ns"])}
        p99 = pct_us.get("p99_us", "-")
        ctx.store.set_kpi("lat", f"{us(s['avg_ns'])} / {p99}")
        return TestResult(self.id, self.name, status="measured",
                          metrics={"avg_us": us(s["avg_ns"]), "p99_us": p99,
                                   "samples": s["packets"]},
                          tables={"Latency (in-pipeline, microseconds)": [row]},
                          notes=f"segment pktParse->executeFAR via inserted BESS "
                                f"Timestamp/Measure; {dur}s at ~{t.offered_mpps} Mpps "
                                f"(low load, <= NDR).")


TESTS = [Tc03Latency]
