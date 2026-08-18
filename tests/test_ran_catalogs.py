"""Stage-3 RAN catalogs + grading semantics.

Proves the per-product-class certification gate is real for the RAN domain exactly as it is for
the core: a synthetic all-pass run PASSES, a stub run is INCOMPLETE (never a silent pass), and a
single essential FAIL is FAIL.

Note on scope: unlike the control plane, the RAN Level 1 suites are NOT yet implemented, the
engine ships with StubCase fillers until each phase verifies its cases against a live stack. So
there is deliberately no "Level 1 is fully implemented" assertion here yet; the test that locks
that in lands with P4, when the last suite is verified.
"""
from __future__ import annotations

import pytest

from cntc.standards import load_catalog, lint_catalog
from cntc.verdict import evaluate

L1_PROFILES = ["cucp-conformance", "cuup-conformance", "du-conformance"]
L2_PROFILES = ["cucp-adversarial", "cuup-adversarial", "du-adversarial"]
RAN_PROFILES = L1_PROFILES + L2_PROFILES


@pytest.mark.parametrize("profile", RAN_PROFILES)
def test_catalog_lints_clean(profile):
    assert lint_catalog(load_catalog(profile)) == []


@pytest.mark.parametrize("profile", RAN_PROFILES)
def test_catalog_has_essential_to_gate_on(profile):
    cat = load_catalog(profile)
    assert cat["tests"], f"{profile} has no tests"
    assert [t for t in cat["tests"] if t.get("class") == "essential"], \
        f"{profile} has no essential test to gate on"


def _suite_from_catalog(profile, status):
    cat = load_catalog(profile)
    return [{"suite": profile, "tests": [
        {"id": t["id"], "name": t["name"], "status": status, "metrics": {}}
        for t in cat["tests"]]}]


def test_all_pass_yields_pass():
    cat = load_catalog("cucp-conformance")
    v = evaluate(_suite_from_catalog("cucp-conformance", "pass"), cat)
    assert v["result"] == "PASS"
    assert v["essential"]["failed"] == 0 and v["essential"]["na"] == 0


def test_stub_run_is_incomplete_never_pass():
    cat = load_catalog("cucp-conformance")
    v = evaluate(_suite_from_catalog("cucp-conformance", "not_implemented"), cat)
    assert v["result"] == "INCOMPLETE"          # never silently PASS
    assert v["essential"]["passed"] == 0


def test_single_essential_fail_is_fail():
    cat = load_catalog("cuup-conformance")
    suites = _suite_from_catalog("cuup-conformance", "pass")
    # flip one essential (UP ciphering per the E1 security policy) to fail
    for t in suites[0]["tests"]:
        if t["id"] == "CUUP-SEC-01":
            t["status"] = "fail"
    v = evaluate(suites, cat)
    assert v["result"] == "FAIL"
    assert "CUUP-SEC-01" in v["failed_essentials"]


def test_essential_counts_match_design():
    """Locks the Level-1 certification bar per product class so a careless catalog edit is
    caught. Totals: 58 Level-1 tests, 30 essential."""
    expected = {"cucp-conformance": (30, 16), "cuup-conformance": (14, 7),
                "du-conformance": (14, 7)}
    total = ess_total = 0
    for profile, (n_tests, n_ess) in expected.items():
        cat = load_catalog(profile)
        ess = sum(1 for t in cat["tests"] if t.get("class") == "essential")
        assert len(cat["tests"]) == n_tests, f"{profile}: expected {n_tests} tests, got {len(cat['tests'])}"
        assert ess == n_ess, f"{profile}: expected {n_ess} essentials, got {ess}"
        total += len(cat["tests"]); ess_total += ess
    assert (total, ess_total) == (58, 30)


def test_level2_is_disjoint_from_level1():
    """No requirement may appear in both levels, an unimplemented L2 test must never be able
    to drag an L1 verdict around."""
    for l1_profile, l2_profile in zip(L1_PROFILES, L2_PROFILES):
        l1 = {t["id"] for t in load_catalog(l1_profile)["tests"]}
        l2 = {t["id"] for t in load_catalog(l2_profile)["tests"]}
        assert not (l1 & l2), f"{l1_profile}/{l2_profile}: tests in both levels: {l1 & l2}"


def test_level2_catalogs_are_marked_roadmap():
    """A Level 2 catalog must advertise that it is unimplemented, so nobody reads its 'na'
    results as a deployment problem."""
    for profile in L2_PROFILES:
        assert "roadmap" in str(load_catalog(profile)["version"]), \
            f"{profile} must carry a roadmap version"


def test_registry_covers_every_catalog_requirement():
    """The suite the engine builds always spans the full published standard, real cases where
    implemented, StubCase everywhere else. A requirement can never be silently dropped."""
    from ranbench import config as cfgmod
    from ranbench.suites.registry import build_suite
    for target in cfgmod.TARGETS:
        built = [c.id for c in build_suite(target)]
        catalog = [t["id"] for t in load_catalog(f"{target}-conformance")["tests"]]
        assert built == catalog, f"{target}: suite does not match its catalog"
