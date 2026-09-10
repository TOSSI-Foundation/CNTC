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
# The L1/L2 split over nFAPI. Separate from L1_PROFILES because these do NOT anchor to
# TS 33.523: 3GPP defines no product class for an L1 and no SCAS for this split, so they
# anchor to SCF222/SCF225 instead. See the header of each catalog.
FAPI_PROFILES = ["pnf-conformance", "vnf-conformance"]
RAN_PROFILES = L1_PROFILES + L2_PROFILES + FAPI_PROFILES


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
    for target in cfgmod.ALL_TARGETS:
        built = [c.id for c in build_suite(target)]
        catalog = [t["id"] for t in load_catalog(f"{target}-conformance")["tests"]]
        assert built == catalog, f"{target}: suite does not match its catalog"


# --- the nFAPI split (SCF222 / SCF225) -------------------------------------------------
@pytest.mark.parametrize("profile", FAPI_PROFILES)
def test_fapi_catalog_anchors_to_scf_not_3gpp(profile):
    """These two catalogs are the one place the "catalogs anchor to TS 33.523" rule does not
    hold, because 3GPP defines no product class for an L1 and no SCAS for the L1/L2 split.

    That is a deliberate exception, so it is asserted rather than left to a reader: the anchors
    must be the Small Cell Forum's, and the certificate must not be mistakable for a 3GPP SCAS
    product class certificate. A later edit that quietly adds a TS 33.5xx anchor here would be
    claiming a coverage the evidence model does not have.
    """
    cat = load_catalog(profile)
    standards = " ".join(cat["standards"])
    assert "SCF222" in standards and "SCF225" in standards, \
        f"{profile} must anchor to SCF222 and SCF225"
    assert "33.5" not in standards, \
        f"{profile} must not claim a 3GPP SCAS anchor: there is no SCAS for this split"
    # The disclaimer travels with the verdict, because the scorecard renders the title.
    assert "not a 3GPP SCAS product class" in cat["title"], \
        f"{profile} title must say it is not a 3GPP SCAS product class certificate"


@pytest.mark.parametrize("profile", FAPI_PROFILES)
def test_fapi_catalog_claims_no_physical_layer_conformance(profile):
    """The nFAPI wire carries what the PHY *reports*, not what it transmitted, so TS 38.211 to
    38.214 cannot be certified from it. Anchoring them would overclaim."""
    standards = " ".join(load_catalog(profile)["standards"])
    for spec in ("38.211", "38.212", "38.213", "38.214"):
        assert spec not in standards, \
            f"{profile} cannot anchor {spec}: the physical layer is not observable on nFAPI"


def test_fapi_essential_counts_match_design():
    """Locks the Level-1 bar for the nFAPI split so a careless catalog edit is caught."""
    expected = {"pnf-conformance": (26, 14), "vnf-conformance": (15, 9)}
    for profile, (n_tests, n_ess) in expected.items():
        cat = load_catalog(profile)
        ess = sum(1 for t in cat["tests"] if t.get("class") == "essential")
        assert len(cat["tests"]) == n_tests, \
            f"{profile}: expected {n_tests} tests, got {len(cat['tests'])}"
        assert ess == n_ess, f"{profile}: expected {n_ess} essentials, got {ess}"


@pytest.mark.parametrize("profile", FAPI_PROFILES)
def test_fapi_gate_is_real(profile):
    """Same gate semantics as every other CNTC profile: a stub run is INCOMPLETE, never PASS."""
    cat = load_catalog(profile)
    stub = _suite_from_catalog(profile, "not_implemented")
    assert evaluate(stub, cat)["result"] == "INCOMPLETE"
    assert evaluate(_suite_from_catalog(profile, "pass"), cat)["result"] == "PASS"


def test_fapi_suites_are_fully_implemented():
    """Unlike a roadmap catalog, these two ship with a real case for every requirement. A
    StubCase here would grade 'na' and quietly shrink the certification bar."""
    from ranbench.suites.base import StubCase
    from ranbench.suites.registry import build_suite
    for target in ("pnf", "vnf"):
        stubs = [c.id for c in build_suite(target) if isinstance(c, StubCase)]
        assert not stubs, f"{target}: unimplemented requirements {stubs}"


def test_the_two_splits_do_not_share_target_names():
    """'all' means every class of one deployment. Overlapping names would make a CU/DU campaign
    expand into a class that rig does not contain."""
    from ranbench import config as cfgmod
    assert not set(cfgmod.CUDU_TARGETS) & set(cfgmod.FAPI_TARGETS)
    for split, targets in cfgmod.SPLITS.items():
        assert set(targets) <= set(cfgmod.ALL_TARGETS), split
