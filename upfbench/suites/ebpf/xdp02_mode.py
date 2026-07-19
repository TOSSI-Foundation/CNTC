"""XDP-02 — XDP attach mode (native/driver vs the generic/SKB fallback). Informational, normal.

Generic (SKB) XDP works but runs the program after the skb is built, so it is far slower than
native/driver XDP; it is often forced by the VM/NIC (here a Xen 'vif' NIC + a Calico veth, which
have no native-XDP support). So this records the mode and warns rather than failing — it does not
gate the certificate."""
from upfbench.suites.base import TestCase, RunContext
from upfbench.results import TestResult
from upfbench.suites.ebpf._common import not_ebpf, intro, na


class Xdp02Mode(TestCase):
    id, name = "XDP-02", "XDP attach mode is native/driver, not the generic/SKB fallback (informational)"

    def run(self, ctx: RunContext) -> TestResult:
        if not_ebpf(ctx):
            return na(self.id, self.name, "not an eBPF/XDP UPF")
        bi = intro(ctx)
        if not bi:
            return na(self.id, self.name, "adapter exposes no BPF introspection")
        modes = {i: v.get("mode", "") for i, v in (bi.get("xdp") or {}).items()}
        vals = set(modes.values())
        any_xdp = bool(vals) and vals <= {"native", "driver", "offload", "generic"}
        native = bool(vals) and vals <= {"native", "driver", "offload"}
        note = ("native/driver XDP." if native else
                f"GENERIC (SKB) XDP in use: {modes}. Functional, but native/driver XDP is "
                "recommended for production throughput (generic is often forced by the VM/NIC).")
        # Normal/informational: pass as long as a real XDP mode is attached; the mode is recorded
        # in metrics (native=false here) so the scorecard shows it was certified on generic.
        return TestResult(self.id, self.name, "pass" if any_xdp else "fail",
            metrics={"modes": modes, "native": native}, notes=note)


TESTS = [Xdp02Mode]
