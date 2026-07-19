"""XDP-01 — an XDP/eBPF program is attached to the datapath (N3/access) interface.

The foundational check: if nothing is on the XDP hook, the UPF is not an eBPF/XDP dataplane and
cannot earn this certificate. Essential."""
from upfbench.suites.base import TestCase, RunContext
from upfbench.results import TestResult
from upfbench.suites.ebpf._common import not_ebpf, intro, na


class Xdp01Attached(TestCase):
    id, name = "XDP-01", "XDP program attached to the N3 (access) interface"

    def run(self, ctx: RunContext) -> TestResult:
        if not_ebpf(ctx):
            return na(self.id, self.name, "not an eBPF/XDP UPF")
        bi = intro(ctx)
        if not bi:
            return na(self.id, self.name, "adapter exposes no BPF introspection")
        xdp = bi.get("xdp") or {}
        attached = [i for i, v in xdp.items() if v.get("attached")]
        return TestResult(self.id, self.name, "pass" if attached else "fail",
            metrics={"ifaces": list(xdp), "attached_on": attached,
                     "pinned_objects": bi.get("pinned_objects", [])},
            notes=f"XDP program attached on {attached or 'NO'} interface(s); "
                  f"pinned objects: {bi.get('pinned_objects', [])}.")


TESTS = [Xdp01Attached]
