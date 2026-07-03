"""NT-02 Malformed GTP-U robustness — the UPF must not CRASH on malformed N3 input and
must keep serving valid traffic. Each variant (control/reserved message type, wrong
version, three truncation cases) is sent as its own burst with per-variant crash
detection: if the BESS data plane segfaults (k8s restarts bessd), we record which packet
did it, wait for recovery, re-establish the session, and continue. A crash on any variant
is a robustness FAIL — a single malformed packet that drops the user plane is a remote DoS.
"""
from upfbench.suites.base import TestCase, RunContext
from upfbench.results import TestResult
from upfbench.suites.n3neg._common import (needs, setup, teardown, core_tx,
                                           restarts, settle_and_check, crash_reason, recover)

_BASE = 210001
# ordered least->most likely to break a fixed-offset decapsulator
_VARIANTS = ("echo_request", "reserved_type", "bad_version",
             "truncated_hdr", "gpdu_no_inner", "len_overflow")


class Nt02Malformed(TestCase):
    id, name = "NT-02", "Malformed GTP-U robustness (no crash)"

    def run(self, ctx: RunContext) -> TestResult:
        if needs(ctx):
            return TestResult(self.id, self.name, "error", notes="needs pfcpsim + trex (send_burst)")
        k = ctx.knobs
        n = int(k.get("burst_pkts", 20000)); fs = int(k.get("frame_size", 256))
        s, pkts = setup(ctx, _BASE, fs)
        rows, crashers = [], []
        try:
            base = restarts(ctx)
            for label in _VARIANTS:
                b = core_tx(ctx, s) if ctx.upf.healthy() else 0
                sent = ctx.traffic.send_burst(pkts[label], n)
                crashed = settle_and_check(ctx, base)
                fwd = (core_tx(ctx, s) - b) if not crashed else 0
                rows.append({"variant": label, "sent": sent,
                             "forwarded": fwd, "crashed_bessd": "YES" if crashed else "no"})
                if crashed:
                    base = recover(ctx, _BASE)        # wait + re-establish; new baseline
                    reason = crash_reason(ctx)        # populated now the new container is up
                    crashers.append(f"{label} ({reason})" if reason else label)
            # Final drain: the GtpuDecap segfault can lag the burst that triggers it (the
            # malformed packet sits in the BESS RX ring and crashes a later batch). Probe
            # once more so a delayed crash is caught HERE and attributed to this test,
            # rather than bleeding into the next one's setup.
            if not crashers and settle_and_check(ctx, base, 20):
                recover(ctx, _BASE)
                crashers.append(f"delayed/post-burst ({crash_reason(ctx)})")
            b = core_tx(ctx, s) if ctx.upf.healthy() else 0
            ctx.traffic.send_burst(pkts["valid"], n)
            valid_fwd = (core_tx(ctx, s) - b) if ctx.upf.healthy() else 0
            recovered = ctx.upf.healthy()
        finally:
            teardown(ctx, _BASE)
        ok = not crashers and valid_fwd > 0 and recovered
        crash_note = (f"CRASHED on: {', '.join(crashers)}. " if crashers
                      else "no crash on any variant. ")
        return TestResult(self.id, self.name, "pass" if ok else "fail",
            metrics={"crash_inducing_variants": crashers, "num_crashes": len(crashers),
                     "valid_forwarded_after": valid_fwd, "upf_recovered": recovered},
            tables={"Malformed GTP-U variants": rows},
            notes=f"{len(_VARIANTS)} malformed variants. {crash_note}"
                  f"Valid traffic forwards after recovery ({valid_fwd}); UPF up={recovered}. "
                  f"A crash = a single malformed N3 packet drops the user plane (remote DoS).")


TESTS = [Nt02Malformed]
