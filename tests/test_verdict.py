"""Unit tests for the CNTC verdict engine (cntc.verdict.evaluate).

Pure-function tests: synthetic results in, verdict out. No engine, no cluster.
Run:  python3 -m pytest tests/test_verdict.py -q     (or: python3 tests/test_verdict.py)
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from cntc.verdict import evaluate, grade_one  # noqa: E402
from cntc.standards import load_catalog, list_profiles, lint_catalog  # noqa: E402


def _suite(*tests):
    return [{"suite": "s", "tests": list(tests)}]


def _t(tid, status, **metrics):
    return {"id": tid, "name": tid, "status": status, "metrics": metrics}


# ---- grade_one: the three verdict kinds + na semantics --------------------------------

def test_status_pass():
    assert grade_one(_t("CF-01", "pass"), {"id": "CF-01", "verdict": "status_pass"})[0] == "pass"
    assert grade_one(_t("CF-01", "fail"), {"id": "CF-01", "verdict": "status_pass"})[0] == "fail"


def test_not_judged_is_na_not_fail():
    req = {"id": "NT-01", "verdict": "status_pass"}
    assert grade_one(_t("NT-01", "error"), req)[0] == "na"
    assert grade_one(_t("NT-01", "skipped"), req)[0] == "na"
    assert grade_one(None, req)[0] == "na"                      # test absent from results


def test_metric_rule():
    req = {"id": "LT-01", "verdict": {"kind": "metric", "metric": "capacity_sessions",
                                      "op": ">=", "value": 1000}}
    assert grade_one(_t("LT-01", "measured", capacity_sessions=1500), req)[0] == "pass"
    assert grade_one(_t("LT-01", "measured", capacity_sessions=500), req)[0] == "fail"
    # missing metric -> na, never a silent pass/fail
    assert grade_one(_t("LT-01", "measured"), req)[0] == "na"


def test_baseline_rel_rule():
    req = {"id": "TC-01", "verdict": {"kind": "baseline_rel", "metric": "peak_ndr_mpps",
                                      "op": ">=", "pct_of_baseline": 90}}
    base = {"TC-01": _t("TC-01", "measured", peak_ndr_mpps=2.0)}
    assert grade_one(_t("TC-01", "measured", peak_ndr_mpps=1.9), req, base)[0] == "pass"  # 95%
    assert grade_one(_t("TC-01", "measured", peak_ndr_mpps=1.5), req, base)[0] == "fail"  # 75%
    assert grade_one(_t("TC-01", "measured", peak_ndr_mpps=1.9), req, None)[0] == "na"    # no baseline


# ---- evaluate: the essential gate -----------------------------------------------------

CATALOG = {
    "profile": "conformance", "version": "t", "gate": {},
    "tests": [
        {"id": "CF-01", "category": "conformance", "class": "essential", "verdict": "status_pass"},
        {"id": "CF-02", "category": "conformance", "class": "essential", "verdict": "status_pass"},
        {"id": "NT-01", "category": "robustness", "class": "essential", "verdict": "status_pass"},
    ],
}


def test_gate_pass_when_all_essential_pass():
    v = evaluate(_suite(_t("CF-01", "pass"), _t("CF-02", "pass"), _t("NT-01", "pass")), CATALOG)
    assert v["result"] == "PASS"
    assert v["essential"] == {"passed": 3, "failed": 0, "na": 0, "total": 3}


def test_gate_fail_on_any_essential_fail():
    v = evaluate(_suite(_t("CF-01", "pass"), _t("CF-02", "fail"), _t("NT-01", "pass")), CATALOG)
    assert v["result"] == "FAIL"
    assert v["failed_essentials"] == ["CF-02"]


def test_gate_incomplete_when_essential_not_run():
    # NT-01 didn't run -> na -> INCOMPLETE, never a silent PASS
    v = evaluate(_suite(_t("CF-01", "pass"), _t("CF-02", "pass")), CATALOG)
    assert v["result"] == "INCOMPLETE"
    assert v["not_run_essentials"] == ["NT-01"]


def test_min_essential_threshold():
    cat = {**CATALOG, "gate": {"min_essential": 2}}
    v = evaluate(_suite(_t("CF-01", "pass"), _t("CF-02", "pass")), cat)  # 2 pass, 1 na
    assert v["result"] == "PASS"     # threshold of 2 met despite NT-01 na


def test_category_scores():
    v = evaluate(_suite(_t("CF-01", "pass"), _t("CF-02", "fail"), _t("NT-01", "pass")), CATALOG)
    assert v["categories"]["conformance"] == {"passed": 1, "failed": 1, "na": 0, "score": 50}
    assert v["categories"]["robustness"]["score"] == 100


# ---- shipped catalogs: lint clean + expected shape ------------------------------------

def test_shipped_catalogs_lint_clean():
    assert set(list_profiles()) >= {"conformance", "performance"}
    for p in list_profiles():
        assert lint_catalog(load_catalog(p)) == [], f"{p} catalog has lint issues"


def test_lint_catches_bad_rules():
    bad = {"tests": [
        {"id": "X-1", "class": "criticl", "verdict": "status_pass"},          # typo'd class
        {"id": "X-2", "verdict": {"kind": "metric", "metric": "m", "op": "=>", "value": 1}},  # bad op
        {"id": "X-3", "verdict": {"kind": "bogus"}},                          # unknown kind
        {"id": "X-4", "verdict": {"kind": "metric", "op": ">=", "value": 1}},  # missing metric
    ]}
    issues = lint_catalog(bad)
    assert len(issues) >= 4


# ---- performance profile: baseline-relative grading + warnings ------------------------

def test_performance_profile_needs_baseline():
    cat = load_catalog("performance")
    perf = _suite(_t("TC-01", "measured", peak_ndr_mpps=1.9),
                  _t("TC-03", "measured", p99_us=200),
                  _t("LT-01", "measured", capacity_sessions=1500))
    # no baseline -> baseline_rel tests (TC-01, TC-03) are na, and a warning is emitted
    v = evaluate(perf, cat, baseline=None, rig={"rig_class": "bare-metal"})
    outcomes = {r["id"]: r["outcome"] for r in v["tests"]}
    assert outcomes["TC-01"] == "na" and outcomes["TC-03"] == "na"
    assert outcomes["LT-01"] == "pass"          # absolute metric still grades
    assert any("baseline" in w for w in v["warnings"])
    assert v["result"] == "INCOMPLETE"          # TC-03 essential is na


def test_performance_profile_with_baseline_passes():
    cat = load_catalog("performance")
    base = {"suites": _suite(_t("TC-01", "measured", peak_ndr_mpps=2.0),
                             _t("TC-03", "measured", p99_us=200),
                             _t("LT-01", "measured", capacity_sessions=1000))}
    cur = _suite(_t("TC-01", "measured", peak_ndr_mpps=1.9),   # 95% of baseline >= 90% -> pass
                 _t("TC-03", "measured", p99_us=210),          # 105% of baseline <= 110% -> pass
                 _t("LT-01", "measured", capacity_sessions=1500))
    v = evaluate(cur, cat, baseline=base, rig={"rig_class": "bare-metal"})
    outcomes = {r["id"]: r["outcome"] for r in v["tests"]}
    assert outcomes == {"TC-01": "pass", "TC-03": "pass", "LT-01": "pass"}
    assert v["result"] == "PASS"


# ---- engine: the `conformance` suite alias (M2) ---------------------------------------

def test_conformance_suite_alias_expands_to_pfcp_and_n3neg():
    from upfbench.config import Campaign, UpfConfig
    c = Campaign(campaign="X", suite="conformance", sut={}, upf=UpfConfig(adapter="sdcore_bess"))
    assert c.suites == ["pfcp", "n3neg"]


if __name__ == "__main__":
    fns = [f for name, f in sorted(globals().items()) if name.startswith("test_")]
    for fn in fns:
        fn()
        print(f"ok  {fn.__name__}")
    print(f"\n{len(fns)} tests passed")
