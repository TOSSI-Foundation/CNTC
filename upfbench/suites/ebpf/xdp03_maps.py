"""XDP-03: the forwarding-state BPF maps (PDR / FAR / QER / session) are present and pinned.

An eBPF UPF keeps its per-session forwarding rules in BPF maps; this checks they exist (non-zero
capacity) and are pinned (backed by the pinned program pipeline, so they survive an eUPF restart).
Normal, the binding tests (XDP-04/05) prove the maps are actually used."""
from upfbench.suites.base import TestCase, RunContext
from upfbench.results import TestResult
from upfbench.suites.ebpf._common import not_ebpf, intro, na

_REQUIRED = ("pdr", "far", "qer", "session")


class Xdp03Maps(TestCase):
    id, name = "XDP-03", "Required BPF maps present and pinned (PDR / FAR / QER / session)"

    def run(self, ctx: RunContext) -> TestResult:
        if not_ebpf(ctx):
            return na(self.id, self.name, "not an eBPF/XDP UPF")
        bi = intro(ctx)
        if not bi:
            return na(self.id, self.name, "adapter exposes no BPF introspection")
        maps = bi.get("maps") or {}
        present = [m for m in _REQUIRED if int((maps.get(m) or {}).get("max", 0)) > 0]
        pinned = [m for m in _REQUIRED if (maps.get(m) or {}).get("pinned")]
        ok = set(present) >= set(_REQUIRED) and set(pinned) >= set(_REQUIRED)
        missing = [m for m in _REQUIRED if m not in present]
        return TestResult(self.id, self.name, "pass" if ok else "fail",
            metrics={"maps": maps, "present": present, "pinned": pinned},
            notes=(f"maps present+pinned: {_REQUIRED}." if ok
                   else f"missing/unpinned maps: {missing or [m for m in _REQUIRED if m not in pinned]}."))


TESTS = [Xdp03Maps]
