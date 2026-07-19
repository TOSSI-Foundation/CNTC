"""XDP-06 — the eBPF session-map entry count matches the installed session count (no drift).

Installs N sessions over N4 and checks the session map holds exactly N — catching leaks (entries
never freed) or drops (rules silently rejected). Normal."""
import time

from upfbench.suites.base import TestCase, RunContext
from upfbench.results import TestResult
from upfbench.suites.ebpf._common import not_ebpf, needs_control, intro, na, install_sessions, delete_sessions

_BASE = 700901


class Xdp06Count(TestCase):
    id, name = "XDP-06", "eBPF session-map entry count matches the installed session count (no drift)"

    def run(self, ctx: RunContext) -> TestResult:
        if not_ebpf(ctx):
            return na(self.id, self.name, "not an eBPF/XDP UPF")
        if needs_control(ctx):
            return TestResult(self.id, self.name, "error", notes="needs the pfcpsim control")
        if not intro(ctx):
            return na(self.id, self.name, "adapter exposes no BPF introspection")
        n = int(ctx.knobs.get("count_sessions", 5))
        base0 = int(intro(ctx)["maps"]["session"]["entries"])
        teids, _ = install_sessions(ctx, n, _BASE)
        time.sleep(1)
        after = intro(ctx)
        try:
            sess = int(after["maps"]["session"]["entries"]) - base0
            installed_teids = sum(1 for t in (after.get("teids") or []) if int(t) >= _BASE)
        finally:
            delete_sessions(ctx, n, _BASE)
        ok = sess == n
        return TestResult(self.id, self.name, "pass" if ok else "fail",
            metrics={"installed": n, "session_entries_delta": sess, "matched_teids": installed_teids},
            notes=f"installed {n} sessions; session-map grew by {sess} (expect {n}); "
                  f"{installed_teids} matching TEIDs in the PDR map.")


TESTS = [Xdp06Count]
