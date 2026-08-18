"""cpbench engine smoke tests, config parsing, suite assembly, composite verdict.

No live core needed: these exercise the plugin wiring and the composite grader directly.
"""
from __future__ import annotations

from cpbench import config as cfgmod
from cpbench.suites.registry import build_suite, NF_REQUIRES
from cpbench.suites.base import RunContext, StubCase
from cpbench.runner import _composite

from cntc.standards import load_catalog
from cntc.verdict import evaluate


def test_sample_config_loads():
    cfg = cfgmod.load("configs/free5gc-cp.yaml")
    assert cfg.core.adapter == "free5gc"
    assert cfg.target_nfs == list(cfgmod.NFS)      # 'all' expands to every NF
    assert cfg.subscribers and "supi" in cfg.subscribers[0]


def test_build_suite_covers_full_catalog():
    # Every catalog requirement is covered by a case (real or stub) in catalog order, so the
    # scorecard always lists the whole standard and never silently drops a requirement.
    for nf in cfgmod.NFS:
        cases = build_suite(nf)
        catalog = load_catalog(cfgmod.Campaign.profile_for(nf))
        assert [c.id for c in cases] == [t["id"] for t in catalog["tests"]]


def test_every_nf_has_real_cases():
    # All five NFs now have real, live-driven cases (not just stubs). Locks the key
    # spec-anchored tests in place so a refactor can't silently drop them back to stubs.
    expected = {
        "amf": {"AMF-REG-01", "AMF-AUTH-01", "AMF-AUTH-02", "AMF-NGAP-01", "AMF-NGAP-02",
                "AMF-NGAP-03", "AMF-NGAP-04", "AMF-DEREG-01", "AMF-NEG-01", "AMF-SEC-01",
                "AMF-SEC-02", "AMF-SEC-04"},
        "smf": {"SMF-SESS-01", "SMF-SESS-03", "SMF-SESS-04", "SMF-N4-01", "SMF-N4-03",
                "SMF-DP-01", "SMF-SEC-01", "SMF-SEC-02", "SMF-SEC-03", "SMF-NEG-01"},
        "nrf": {"NRF-SEC-01", "NRF-SEC-02", "NRF-SEC-03", "NRF-NEG-01"},
        "ausf": {"AUSF-AUTH-01", "AUSF-AUTH-02", "AUSF-SEC-02", "AUSF-NEG-01"},
        "udm": {"UDM-SDM-01", "UDM-SDM-02", "UDM-UECM-01", "UDM-AUTH-01", "UDM-SEC-01",
                "UDM-SEC-02", "UDM-NEG-01"},
    }
    for nf, ids in expected.items():
        real = {c.id for c in build_suite(nf) if not isinstance(c, StubCase)}
        assert ids <= real, f"{nf}: missing real cases {ids - real}"


def test_stubcase_grades_na_not_pass():
    case = StubCase("AMF-REG-01", "x", "amf")
    ctx = RunContext(cfg=None, core=None, driver=None, sbi=None, observer=None,
                     store=None, nf="amf", endpoint="", knobs={})
    res = case.run(ctx)
    assert res.status == "not_implemented"          # -> graded 'na' by the verdict engine


def test_nf_requires_has_every_nf():
    assert set(NF_REQUIRES) == set(cfgmod.NFS)


def test_composite_fail_dominates():
    rig = {"adapter": "free5gc", "mode": "control-plane"}
    # amf all-pass, nrf one essential fail -> composite FAIL
    def suites(profile, status):
        cat = load_catalog(profile)
        return [{"suite": profile, "tests": [
            {"id": t["id"], "name": t["name"], "status": status, "metrics": {}}
            for t in cat["tests"]]}]
    amf = evaluate(suites("amf-conformance", "pass"), load_catalog("amf-conformance"), rig=rig)
    nrf_suites = suites("nrf-conformance", "pass")
    for t in nrf_suites[0]["tests"]:
        if t["id"] == "NRF-SEC-03":
            t["status"] = "fail"
    nrf = evaluate(nrf_suites, load_catalog("nrf-conformance"), rig=rig)
    comp = _composite({"amf": amf, "nrf": nrf}, rig)
    assert comp["result"] == "FAIL"
    assert comp["per_nf"] == {"amf": "PASS", "nrf": "FAIL"}


def test_composite_all_pass():
    rig = {"adapter": "free5gc", "mode": "control-plane"}
    def vpass(profile):
        cat = load_catalog(profile)
        s = [{"suite": profile, "tests": [
            {"id": t["id"], "name": t["name"], "status": "pass", "metrics": {}}
            for t in cat["tests"]]}]
        return evaluate(s, cat, rig=rig)
    comp = _composite({nf: vpass(f"{nf}-conformance") for nf in cfgmod.NFS}, rig)
    assert comp["result"] == "PASS"
