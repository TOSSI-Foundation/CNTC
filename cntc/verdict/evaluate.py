"""Pure grading engine — the heart of CNTC's verdict layer.

``evaluate()`` takes the *serialized* results (the ``suites`` list from a ``results.json``)
plus a requirement catalog and returns a verdict block. It imports nothing from the engine,
so it is trivially unit-testable and can re-grade any past ``results.json``.

Outcome of a single test against its requirement is one of:
    "pass" | "fail" | "na"
where "na" means "could not be judged" — the test did not run, errored, was skipped, or the
metric it needs is absent. **"na" is never silently promoted to "pass".**

Verdict rules supported (the ``verdict:`` field of a catalog entry):
    status_pass                                   -> pass iff TestResult.status == "pass"
    {kind: metric,       metric, op, value}       -> compare metrics[metric] to an absolute value
    {kind: baseline_rel, metric, op, pct_of_baseline}
                                                  -> compare metrics[metric] to pct% of the
                                                     baseline run's same metric (needs baseline)
"""
from __future__ import annotations

import operator
from typing import Any

_OPS = {
    ">=": operator.ge, "<=": operator.le, ">": operator.gt,
    "<": operator.lt, "==": operator.eq, "!=": operator.ne,
}

# Engine statuses that mean "we could not judge this test", not "it failed".
_NOT_JUDGED = {"error", "skipped", "na", "not_implemented", "not implemented", ""}


def _cmp(value: Any, op: str, target: Any) -> bool:
    fn = _OPS.get(op)
    if fn is None:
        raise ValueError(f"unknown comparison operator {op!r}")
    return bool(fn(value, target))


def grade_one(res: dict | None, req: dict, baseline_by_id: dict | None = None) -> tuple[str, str]:
    """Grade one test result against one requirement -> (outcome, human detail)."""
    if res is None:
        return "na", "not run (test absent from results)"
    status = str(res.get("status", "")).lower()
    if status in _NOT_JUDGED:
        note = (res.get("notes") or status or "not judged")[:100]
        return "na", f"{status or 'na'}: {note}"

    rule = req.get("verdict", "status_pass")

    # status_pass — expressed as the bare string or {kind: status_pass}
    if rule == "status_pass" or (isinstance(rule, dict) and rule.get("kind") == "status_pass"):
        return ("pass", "status == pass") if status == "pass" else ("fail", f"status == {status}")

    if not isinstance(rule, dict):
        return "na", f"unrecognized verdict rule {rule!r}"

    kind = rule.get("kind")
    metrics = res.get("metrics") or {}

    if kind == "metric":
        key = rule["metric"]
        if key not in metrics or metrics[key] is None:
            return "na", f"metric {key!r} absent"
        val = _num(metrics[key])
        if val is None:
            return "na", f"metric {key!r} not numeric ({metrics[key]!r})"
        ok = _cmp(val, rule["op"], rule["value"])
        return ("pass" if ok else "fail", f"{key}={val} {rule['op']} {rule['value']}")

    if kind == "baseline_rel":
        key = rule["metric"]
        if not baseline_by_id:
            return "na", "no baseline supplied (relative grading requires one)"
        base_res = baseline_by_id.get(req["id"]) or {}
        base_val = _num((base_res.get("metrics") or {}).get(key))
        cur_val = _num(metrics.get(key))
        if base_val is None or cur_val is None or base_val == 0:
            return "na", f"metric {key!r} missing in current or baseline"
        pct = 100.0 * cur_val / base_val
        ok = _cmp(pct, rule["op"], rule["pct_of_baseline"])
        return ("pass" if ok else "fail",
                f"{key}={cur_val} = {pct:.0f}% of baseline {base_val} "
                f"(need {rule['op']} {rule['pct_of_baseline']}%)")

    return "na", f"unknown verdict kind {kind!r}"


def _num(v: Any) -> float | None:
    if isinstance(v, bool):
        return float(v)
    if isinstance(v, (int, float)):
        return float(v)
    try:
        return float(str(v).strip())
    except (ValueError, AttributeError):
        return None


def evaluate(suites: list[dict], catalog: dict,
             baseline: dict | None = None, rig: dict | None = None) -> dict:
    """Grade all catalog tests against the run's results -> a verdict block.

    Args:
        suites:   the ``suites`` list from results.json (each {"suite","tests":[...]}).
        catalog:  a loaded requirement catalog (see cntc.standards.load_catalog).
        baseline: an optional prior results.json dict (for baseline_rel rules).
        rig:      environment facts stamped onto the verdict (mode, adapter, cpu, nic...).
    """
    by_id = _index_results(suites)
    base_by_id = _index_results(baseline.get("suites", [])) if baseline else None

    rows: list[dict] = []
    defaults = catalog.get("defaults", {})
    for req in catalog.get("tests", []):
        tid = req["id"]
        res = by_id.get(tid)
        outcome, detail = grade_one(res, req, base_by_id)
        rows.append({
            "id": tid,
            "name": req.get("name") or (res or {}).get("name", ""),
            "category": req.get("category", "uncategorized"),
            "class": req.get("class", defaults.get("class", "normal")),
            "outcome": outcome,     # pass | fail | na
            "detail": detail,
        })

    warnings = _requirement_warnings(catalog, baseline, rig or {})
    return _aggregate(rows, catalog, rig or {}, warnings)


def _requirement_warnings(catalog: dict, baseline: dict | None, rig: dict) -> list[str]:
    """Loud, honest warnings when a profile's preconditions aren't met — so a verdict is
    never mistaken for something it isn't (esp. the hardware-dependent performance profile)."""
    w: list[str] = []
    req = catalog.get("requires", {}) or {}
    if req.get("baseline") and not baseline:
        w.append("this profile grades RELATIVE to a baseline, but none was supplied — "
                 "baseline_rel tests were graded 'na'. Pass a baseline results.json from the "
                 "same rig class (e.g. `--baseline campaigns/<ref>/results.json`).")
    if req.get("rig_class") and not rig.get("rig_class"):
        w.append("no rig_class recorded — a performance verdict is only comparable within the "
                 "same rig class. Set `sut.rig_class` in the campaign config.")
    return w


def _index_results(suites: list[dict]) -> dict[str, dict]:
    idx: dict[str, dict] = {}
    for s in suites or []:
        for t in s.get("tests", []):
            if "id" in t:
                idx[t["id"]] = t
    return idx


def _aggregate(rows: list[dict], catalog: dict, rig: dict, warnings: list[str] | None = None) -> dict:
    categories: dict[str, dict] = {}
    for r in rows:
        c = categories.setdefault(r["category"], {"passed": 0, "failed": 0, "na": 0})
        c[{"pass": "passed", "fail": "failed", "na": "na"}[r["outcome"]]] += 1
    for c in categories.values():
        judged = c["passed"] + c["failed"]
        c["score"] = round(100 * c["passed"] / judged) if judged else None

    ess = [r for r in rows if r["class"] == "essential"]
    ess_pass = [r for r in ess if r["outcome"] == "pass"]
    ess_fail = [r for r in ess if r["outcome"] == "fail"]
    ess_na = [r for r in ess if r["outcome"] == "na"]

    gate = catalog.get("gate", {}) or {}
    min_ess = gate.get("min_essential")   # None => require all essential to pass

    if ess_fail:
        result = "FAIL"
    elif min_ess is not None:
        result = "PASS" if len(ess_pass) >= int(min_ess) else "INCOMPLETE"
    elif ess_na:
        result = "INCOMPLETE"             # some essentials never ran; not a full pass
    else:
        result = "PASS"

    return {
        "framework": "CNTC",
        "profile": catalog.get("profile", "?"),
        "catalog_version": catalog.get("version", "?"),
        "title": catalog.get("title", ""),
        "standards": catalog.get("standards", []),
        "rig": rig,
        "result": result,
        "gate": {"policy": "all" if min_ess is None else f"min_essential={min_ess}"},
        "essential": {
            "passed": len(ess_pass), "failed": len(ess_fail),
            "na": len(ess_na), "total": len(ess),
        },
        "failed_essentials": [r["id"] for r in ess_fail],
        "not_run_essentials": [r["id"] for r in ess_na],
        "categories": categories,
        "warnings": warnings or [],
        "tests": rows,
    }
