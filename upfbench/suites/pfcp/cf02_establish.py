"""CF-02 Session establishment (PDR/FAR/QER accepted) — Suite 3 (PFCP conformance)."""
from upfbench.suites.base import TestCase, RunContext
from upfbench.results import TestResult
from upfbench.suites.pfcp.cf01_association import needs_pfcpsim

_BASE = 10


class Cf02Establish(TestCase):
    id, name = "CF-02", "Session establishment (PDR/FAR/QER accepted)"

    def run(self, ctx: RunContext) -> TestResult:
        if needs_pfcpsim(ctx):
            return TestResult(self.id, self.name, "error", notes="needs the pfcpsim control")
        ctx.control.ensure_associated()
        r = ctx.control.create_sessions(count=1, base_id=_BASE)
        ctx.control.delete_sessions_raw(count=1, base_id=_BASE)   # clean up
        return TestResult(self.id, self.name, "pass" if r["ok"] else "fail",
                          metrics={"establish_ok": r["ok"]},
                          notes="Session Establishment Request with UL+DL PDR/FAR/QER; "
                                "UPF accepts and returns success (TS 29.244 §7.5.2).")


TESTS = [Cf02Establish]
