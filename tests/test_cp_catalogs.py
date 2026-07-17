"""Stage-2 control-plane catalogs + grading semantics.

Proves the per-NF certification gate is real (not just that stubs grade 'na'): a synthetic
all-pass run PASSES, a stub run is INCOMPLETE, and a single essential FAIL is FAIL — the same
`status_pass` discipline the UPF conformance profile uses, now per NF.
"""
from __future__ import annotations

import pytest

from cntc.standards import load_catalog, lint_catalog
from cntc.verdict import evaluate

NF_PROFILES = ["amf-conformance", "smf-conformance", "nrf-conformance",
               "ausf-conformance", "udm-conformance", "cp-performance"]


@pytest.mark.parametrize("profile", NF_PROFILES)
def test_catalog_lints_clean(profile):
    assert lint_catalog(load_catalog(profile)) == []


@pytest.mark.parametrize("profile", NF_PROFILES)
def test_catalog_has_essential_or_requires_baseline(profile):
    cat = load_catalog(profile)
    # every conformance catalog must gate on at least one essential test
    ess = [t for t in cat["tests"] if t.get("class") == "essential"]
    assert cat["tests"], f"{profile} has no tests"
    assert ess, f"{profile} has no essential test to gate on"


def _suite_from_catalog(profile, status):
    cat = load_catalog(profile)
    return [{"suite": profile, "tests": [
        {"id": t["id"], "name": t["name"], "status": status, "metrics": {}}
        for t in cat["tests"]]}]


def test_all_pass_yields_pass():
    cat = load_catalog("amf-conformance")
    suites = _suite_from_catalog("amf-conformance", "pass")
    v = evaluate(suites, cat)
    assert v["result"] == "PASS"
    assert v["essential"]["failed"] == 0 and v["essential"]["na"] == 0


def test_stub_run_is_incomplete_never_pass():
    cat = load_catalog("amf-conformance")
    suites = _suite_from_catalog("amf-conformance", "not_implemented")
    v = evaluate(suites, cat)
    assert v["result"] == "INCOMPLETE"          # never silently PASS
    assert v["essential"]["passed"] == 0


def test_single_essential_fail_is_fail():
    cat = load_catalog("nrf-conformance")
    suites = _suite_from_catalog("nrf-conformance", "pass")
    # flip one essential (NRF-SEC-03 = SBI must require TLS) to fail
    for t in suites[0]["tests"]:
        if t["id"] == "NRF-SEC-03":
            t["status"] = "fail"
    v = evaluate(suites, cat)
    assert v["result"] == "FAIL"
    assert "NRF-SEC-03" in v["failed_essentials"]


def test_essential_counts_match_design():
    # locks the certification bar per NF so a careless catalog edit is caught
    expected = {"amf-conformance": 9, "smf-conformance": 7, "nrf-conformance": 5,
                "ausf-conformance": 4, "udm-conformance": 4}
    for profile, n in expected.items():
        cat = load_catalog(profile)
        ess = sum(1 for t in cat["tests"] if t.get("class") == "essential")
        assert ess == n, f"{profile}: expected {n} essentials, got {ess}"
