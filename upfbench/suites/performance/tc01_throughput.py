"""TC-01 Throughput sweep (NDR/PDR per frame size) — RFC 2544 26.1 / ETSI TST009.

For each frame size we binary-search the offered packet rate to find:
  * NDR  — highest offered rate with zero loss,
  * PDR  — highest offered rate with loss <= tolerance (default 0.1%).

Loss is measured against *offered* traffic (what the generator sent) vs *forwarded*
(the UPF's N6/core TX counter), so it captures af_packet RX drops as well as pipeline
drops — the honest black-box throughput of the UPF.
"""
from __future__ import annotations

from upfbench.suites.base import TestCase, RunContext
from upfbench.results import TestResult
from upfbench.search import find_rates

_DEFAULT_FRAMES = [128, 256, 512, 1024, 1518]


class TC01Throughput(TestCase):
    id, name = "TC-01", "Throughput vs frame size (NDR/PDR)"

    def run(self, ctx: RunContext) -> TestResult:
        if ctx.traffic is None or ctx.control is None:
            return TestResult(self.id, self.name, "error",
                              notes="needs a control + traffic plugin (pybess + tcpreplay)")
        k = ctx.knobs
        frames = k.get("frame_sizes", _DEFAULT_FRAMES)
        dur = int(k.get("trial_duration_s", 5))
        max_rate = float(k.get("max_rate_mpps", 2.0))
        tol = float(k.get("pdr_tolerance", 0.001))
        resolution = float(k.get("search_resolution_mpps", 0.05))
        max_iters = int(k.get("search_max_iters", 8))

        # Make the UPF forward everything (idempotent wildcard PDR + UL FAR + bypass QER).
        ctx.control.install_sessions()
        n3 = self._port(ctx, ctx.cfg.upf.n3_iface or "access")
        n6 = self._port(ctx, ctx.cfg.upf.n6_iface or "core")
        ff = ctx.upf.fwd_field()   # 'tx_pkts' (BESS) or 'rx_pkts' (OAI tun N6)
        # Traffic must hit the per-session PDR the control installed. pybess installs
        # a forward-all wildcard (defaults match -> None), but pfcpsim installs a
        # specific F-TEID/UE-IP PDR, so align the single flow to it.
        teids, ue_ips = ctx.control.aligned_flows()

        rows = []
        peak_ndr = 0.0
        # Largest rate the generator can actually offer (sender ceiling); if NDR lands
        # at this, the result is generator-limited, not UPF-limited.
        gen_ceiling = 0.0
        for fs in frames:
            # forwarded + achieved-offered rate seen at each target rate (for the report).
            fwd_at: dict[float, float] = {}
            off_at: dict[float, float] = {}

            def trial(load_mpps: float) -> float:
                before = ctx.upf.port_counters()
                t = ctx.traffic.run_trial(frame_size=fs, offered_mpps=load_mpps,
                                          duration_s=dur, teids=teids, ue_ips=ue_ips)
                after = ctx.upf.port_counters()
                fwd = after[n6][ff] - before[n6][ff]
                loss = max(0.0, (t.sent_pkts - fwd) / t.sent_pkts) if t.sent_pkts else 1.0
                # key by the *target* rate (what the search samples + returns).
                key = round(load_mpps, 4)
                off_at[key] = round(t.offered_mpps, 4)
                if t.duration_s:
                    fwd_at[key] = round(fwd / t.duration_s / 1e6, 4)
                return loss

            # ONE search; NDR (0 loss) and PDR (<=tol) derived from the shared samples.
            res = find_rates(trial, max_load=max_rate,
                             thresholds={"NDR": 0.0, "PDR": tol},
                             resolution=resolution, max_iters=max_iters)
            ndr_rate = res["rates"]["NDR"]
            pdr_rate = res["rates"]["PDR"]
            gen_ceiling = max(gen_ceiling, max(off_at.values(), default=0.0))
            rows.append({
                "frame_B": fs,
                "NDR_Mpps": ndr_rate,
                "PDR_Mpps": pdr_rate,
                "offered_Mpps@PDR": off_at.get(pdr_rate, 0.0),
                "fwd_Mpps@PDR": fwd_at.get(pdr_rate, 0.0),
            })
            peak_ndr = max(peak_ndr, ndr_rate)

        ctx.store.set_kpi("ndr", peak_ndr)
        ctx.store.set_kpi("pdr", max((r["PDR_Mpps"] for r in rows), default=0.0))
        return TestResult(self.id, self.name, status="measured",
                          metrics={"peak_ndr_mpps": peak_ndr,
                                   "generator_ceiling_mpps": round(gen_ceiling, 4)},
                          tables={"NDR/PDR per frame size": rows},
                          notes=f"af_packet path; {dur}s trials; loss vs offered "
                                f"(forwarded from UPF {n6} TX). Generator ceiling "
                                f"~{round(gen_ceiling, 3)} Mpps (NDR near this = generator-, "
                                f"not UPF-, limited). af_packet throughput is non-monotonic: "
                                f"the no-drop rate can exceed the heavy-overload rate (TC-04) "
                                f"as the kernel socket livelocks under saturation; raise "
                                f"trial_duration_s for steadier numbers.")

    @staticmethod
    def _port(ctx: RunContext, prefix: str) -> str:
        """Resolve a configured iface prefix (access/core) to its BESS port-counter
        key (accessFast/coreFast)."""
        keys = list(ctx.upf.port_counters().keys())
        for key in keys:
            if key.startswith(prefix):
                return key
        raise RuntimeError(f"no UPF port matching {prefix!r}; have {keys}")


TESTS = [TC01Throughput]
