"""XDP-08: the XDP program stays attached after a malformed-N3 burst (no silent detach).

A robustness check specific to the fast path: a burst of malformed GTP-U must not knock the XDP
program off the interface (a silent detach would drop the whole datapath to the slow path or
black-hole it). After the burst, the program must still be attached and the UPF responsive.
Normal (the harder crash gate is NT-02 in the conformance suite)."""
import time

from upfbench.suites.base import TestCase, RunContext
from upfbench.results import TestResult
from upfbench.suites.ebpf._common import not_ebpf, intro, na, inject_gtpu


class Xdp08StaysAttached(TestCase):
    id, name = "XDP-08", "XDP program stays attached after a malformed-N3 burst (no silent detach to slow path)"

    def run(self, ctx: RunContext) -> TestResult:
        if not_ebpf(ctx):
            return na(self.id, self.name, "not an eBPF/XDP UPF")
        before = intro(ctx)
        if not before:
            return na(self.id, self.name, "adapter exposes no BPF introspection")
        n = int(ctx.knobs.get("malformed_pkts", 2000))
        sent = inject_gtpu(ctx, 0xDEADBEEF, "10.250.0.1", n, malformed=True)
        time.sleep(1)
        after = intro(ctx)
        if not after:
            return TestResult(self.id, self.name, "fail", metrics={"sent": sent},
                notes="adapter/API unreachable after the malformed burst, possible datapath crash.")
        attached = any(v.get("attached") for v in (after.get("xdp") or {}).values())
        alive = ctx.upf.healthy()
        ok = attached and alive
        return TestResult(self.id, self.name, "pass" if ok else "fail",
            metrics={"sent": sent, "still_attached": attached, "alive": alive},
            notes=(f"sent {sent} malformed GTP-U; still attached={attached}, alive={alive}." if ok
                   else f"program detached or UPF unhealthy after malformed burst "
                        f"(attached={attached}, alive={alive})."))


TESTS = [Xdp08StaysAttached]
