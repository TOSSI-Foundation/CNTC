"""Stage-2 control-plane catalogs + grading semantics.

Proves the per-NF certification gate is real (not just that stubs grade 'na'): a synthetic
all-pass run PASSES, a stub run is INCOMPLETE, and a single essential FAIL is FAIL, the same
`status_pass` discipline the UPF conformance profile uses, now per NF.
"""
from __future__ import annotations

import pytest

from cntc.standards import load_catalog, lint_catalog
from cntc.verdict import evaluate

# Level 1 (shipped, every test implemented) + Level 2 (adversarial roadmap) + performance
NF_PROFILES = ["amf-conformance", "smf-conformance", "nrf-conformance",
               "ausf-conformance", "udm-conformance", "udr-conformance",
               "pcf-conformance", "cp-performance",
               "amf-adversarial", "smf-adversarial", "nrf-adversarial", "ausf-adversarial"]


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
    # locks the Level-1 certification bar per NF so a careless catalog edit is caught
    expected = {"amf-conformance": 9, "smf-conformance": 7, "nrf-conformance": 3,
                "ausf-conformance": 3, "udm-conformance": 4, "udr-conformance": 4,
                "pcf-conformance": 4}
    for profile, n in expected.items():
        cat = load_catalog(profile)
        ess = sum(1 for t in cat["tests"] if t.get("class") == "essential")
        assert ess == n, f"{profile}: expected {n} essentials, got {ess}"


def test_level1_is_fully_implemented():
    """The core promise of Level 1: every test in an L1 catalog has a real implementation,
    so an L1 verdict is always a clean PASS/FAIL about the deployment, never INCOMPLETE
    because the tester didn't build something."""
    from cpbench import config as cfgmod
    from cpbench.suites.registry import build_suite
    from cpbench.suites.base import StubCase
    for nf in cfgmod.NFS:
        impl = {x.id for x in build_suite(nf) if not isinstance(x, StubCase)}
        cat = load_catalog(f"{nf}-conformance")
        missing = [t["id"] for t in cat["tests"] if t["id"] not in impl]
        assert not missing, f"{nf} Level 1 has unimplemented tests: {missing}"


def test_level2_is_disjoint_from_level1():
    """No test may appear in both levels."""
    from cpbench import config as cfgmod
    for nf in cfgmod.NFS:
        l1 = {t["id"] for t in load_catalog(f"{nf}-conformance")["tests"]}
        try:
            l2 = {t["id"] for t in load_catalog(f"{nf}-adversarial")["tests"]}
        except FileNotFoundError:
            continue
        assert not (l1 & l2), f"{nf}: tests in both levels: {l1 & l2}"


def test_no_catalog_renders_smart_punctuation():
    """No catalog may render an em dash, in any encoding.

    A grep for the literal character is not enough. YAML decodes ``\\u2014`` inside a
    double-quoted scalar back into an em dash, so a catalog can read as clean on disk and
    still print one in the CLI, the scorecard, the certificate and the dashboard. This
    loads every catalog and inspects the decoded strings, which is what a reader sees.
    """
    from pathlib import Path

    from cntc import standards

    banned = {"—": "em dash", "–": "en dash",
              "‘": "left single quote", "’": "right single quote",
              "“": "left double quote", "”": "right double quote",
              "…": "ellipsis"}

    def walk(node, where):
        if isinstance(node, str):
            for ch, what in banned.items():
                assert ch not in node, f"{where}: {what} in {node!r}"
        elif isinstance(node, dict):
            for k, v in node.items():
                walk(v, f"{where}.{k}")
        elif isinstance(node, list):
            for i, v in enumerate(node):
                walk(v, f"{where}[{i}]")

    files = sorted(Path(standards.__file__).parent.glob("*.yaml"))
    assert files, "no catalogs found to check"
    for f in files:
        walk(load_catalog(f.stem), f.stem)
