"""NT-03 PSC (PDU Session Container, ext-header type 0x85) robustness — a valid 5G GTP-U
PSC packet should be handled; a malformed PSC ext-header must be dropped without crashing
the data plane. Pass = no crash on either PSC packet, malformed PSC not forwarded, and
valid traffic still forwards after."""
from upfbench.suites.base import TestCase, RunContext
from upfbench.results import TestResult
from upfbench.suites.n3neg._common import (needs, setup, teardown, core_tx,
                                           restarts, settle_and_check, crash_reason, recover)

_BASE = 220001


class Nt03Psc(TestCase):
    id, name = "NT-03", "PSC (0x85) ext-header robustness"

    def run(self, ctx: RunContext) -> TestResult:
        if needs(ctx):
            return TestResult(self.id, self.name, "error", notes="needs pfcpsim + trex (send_burst)")
        k = ctx.knobs
        n = int(k.get("burst_pkts", 20000)); fs = int(k.get("frame_size", 256))
        s, pkts = setup(ctx, _BASE, fs)
        crashers = []
        try:
            base = restarts(ctx)
            results = {}
            for label in ("psc_valid", "psc_malformed"):
                b = core_tx(ctx, s) if ctx.upf.healthy() else 0
                ctx.traffic.send_burst(pkts[label], n)
                crashed = settle_and_check(ctx, base)
                results[label] = (core_tx(ctx, s) - b) if not crashed else 0
                if crashed:
                    base = recover(ctx, _BASE)
                    crashers.append(f"{label} ({crash_reason(ctx)})")
            b = core_tx(ctx, s); ctx.traffic.send_burst(pkts["valid"], n)
            live_fwd = core_tx(ctx, s) - b
            recovered = ctx.upf.healthy()
        finally:
            teardown(ctx, _BASE)
        bad_psc_fwd = results.get("psc_malformed", 0)
        ok = not crashers and bad_psc_fwd == 0 and live_fwd > 0 and recovered
        crash_note = f"CRASHED on: {', '.join(crashers)}. " if crashers else "no crash. "
        return TestResult(self.id, self.name, "pass" if ok else "fail",
            metrics={"valid_psc_forwarded": results.get("psc_valid", 0),
                     "malformed_psc_forwarded": bad_psc_fwd,
                     "crash_inducing_variants": crashers,
                     "valid_forwarded_after": live_fwd, "upf_recovered": recovered},
            notes=f"{crash_note}Valid PSC forwarded {results.get('psc_valid', 0)}; malformed PSC "
                  f"forwarded {bad_psc_fwd} (expect 0); valid after {live_fwd}; UPF up={recovered}.")


TESTS = [Nt03Psc]
