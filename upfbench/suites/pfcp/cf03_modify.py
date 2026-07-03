"""CF-03 Session modification — Suite 3 (PFCP conformance, 3GPP TS 29.244).

Previously thought blocked, but the vendored pfcpsim build *does* expose `session modify`
and the SD-Core UPF accepts it — so CF-03 is a real conformance test.
"""
from upfbench.suites.base import TestCase, RunContext
from upfbench.results import TestResult
from upfbench.suites.pfcp.cf01_association import needs_pfcpsim

_BASE = 20


class Cf03Modify(TestCase):
    id, name = "CF-03", "Session modification"

    def run(self, ctx: RunContext) -> TestResult:
        if needs_pfcpsim(ctx):
            return TestResult(self.id, self.name, "error", notes="needs the pfcpsim control")
        ctx.control.ensure_associated()
        est = ctx.control.create_sessions(count=1, base_id=_BASE)
        mod = ctx.control.modify_sessions(count=1, base_id=_BASE)
        ctx.control.delete_sessions_raw(count=1, base_id=_BASE)   # clean up
        ok = est["ok"] and mod["ok"]
        return TestResult(self.id, self.name, "pass" if ok else "fail",
                          metrics={"establish_ok": est["ok"], "modify_ok": mod["ok"]},
                          notes="Session Modification Request (update FAR/PDR on an "
                                "established session); UPF accepts (TS 29.244 §7.5.4).")


TESTS = [Cf03Modify]
