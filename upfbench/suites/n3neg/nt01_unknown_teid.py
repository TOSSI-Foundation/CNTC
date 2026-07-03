"""NT-01 Unknown TEID — N3 negative suite. A GTP-U packet whose TEID has no matching PDR
must be dropped (not forwarded) and must not crash the UPF; valid traffic on the known
TEID must still forward."""
from upfbench.suites.base import TestCase, RunContext
from upfbench.results import TestResult
from upfbench.suites.n3neg._common import needs, setup, teardown, fwd

_BASE = 200001


class Nt01UnknownTeid(TestCase):
    id, name = "NT-01", "Unknown TEID dropped (no matching PDR)"

    def run(self, ctx: RunContext) -> TestResult:
        if needs(ctx):
            return TestResult(self.id, self.name, "error", notes="needs pfcpsim + trex (send_burst)")
        k = ctx.knobs
        n = int(k.get("burst_pkts", 20000)); fs = int(k.get("frame_size", 256))
        s, pkts = setup(ctx, _BASE, fs)
        try:
            valid_fwd, _ = fwd(ctx, s, pkts["valid"], n)
            bad_fwd, bad_sent = fwd(ctx, s, pkts["unknown_teid"], n)
            alive = ctx.upf.healthy()
        finally:
            teardown(ctx, _BASE)
        ok = valid_fwd > 0 and bad_fwd == 0 and alive
        return TestResult(self.id, self.name, "pass" if ok else "fail",
            metrics={"valid_forwarded": valid_fwd, "unknown_teid_forwarded": bad_fwd,
                     "unknown_teid_sent": bad_sent, "upf_alive": alive},
            notes=f"{bad_sent} GTP-U on an unknown TEID; {bad_fwd} forwarded (expect 0). "
                  f"Valid TEID forwarded {valid_fwd} (expect >0). UPF alive={alive}.")


TESTS = [Nt01UnknownTeid]
