"""CF-05 Error handling (unknown SEID) — Suite 3 (PFCP conformance, 3GPP TS 29.244).

Negative tests: operations on a session that was never established must be REJECTED
(the UPF/stack must not silently accept an unknown SEID). We assert that delete and
modify of a non-existent session both fail.

Scope note: pfcpctl drives well-formed messages only, so this checks unknown-SEID
rejection, not malformed-IE handling. Deep "missing mandatory IE -> specific cause code"
assertions would need raw PFCP injection (a future raw-message harness).
"""
from upfbench.suites.base import TestCase, RunContext
from upfbench.results import TestResult
from upfbench.suites.pfcp.cf01_association import needs_pfcpsim

_UNKNOWN_A, _UNKNOWN_B = 991, 992


class Cf05ErrorHandling(TestCase):
    id, name = "CF-05", "Error handling (unknown SEID / missing IE -> cause)"

    def run(self, ctx: RunContext) -> TestResult:
        if needs_pfcpsim(ctx):
            return TestResult(self.id, self.name, "error", notes="needs the pfcpsim control")
        ctx.control.ensure_associated()
        d = ctx.control.delete_sessions_raw(count=1, base_id=_UNKNOWN_A)   # never created
        m = ctx.control.modify_sessions(count=1, base_id=_UNKNOWN_B)       # never created
        # Correct behaviour is REJECTION (ok == False) for both.
        ok = (not d["ok"]) and (not m["ok"])
        return TestResult(self.id, self.name, "pass" if ok else "fail",
                          metrics={"delete_unknown_rejected": not d["ok"],
                                   "modify_unknown_rejected": not m["ok"]},
                          notes="Unknown-SEID ops (delete/modify of a session never "
                                "established) are correctly rejected. Malformed-IE cause "
                                "codes need a raw-PFCP harness (not exposed by pfcpctl).")


TESTS = [Cf05ErrorHandling]
