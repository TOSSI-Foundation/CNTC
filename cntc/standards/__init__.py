"""Requirement catalogs — the machine-checkable standard a UPF is graded against.

Each ``<profile>.yaml`` here is a versioned requirement catalog: it lists, per test
ID, the category, the weight class (essential / normal / bonus), and the *verdict rule* that
turns a test's output into pass/fail. The catalogs are data, not code, so the bar can be
reviewed and tuned without touching the engine.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

_DIR = Path(__file__).resolve().parent


def catalog_path(profile: str) -> Path:
    return _DIR / f"{profile}.yaml"


def list_profiles() -> list[str]:
    return sorted(p.stem for p in _DIR.glob("*.yaml"))


_VALID_OPS = {">=", "<=", ">", "<", "==", "!="}
_VALID_CLASSES = {"essential", "normal", "bonus"}
_VALID_KINDS = {"status_pass", "metric", "baseline_rel"}


def lint_catalog(cat: dict[str, Any]) -> list[str]:
    """Structural lint of a requirement catalog — returns a list of human-readable issues
    (empty == clean). Guards against the most common drift/typo bugs: bad verdict kinds,
    unknown operators, mis-spelled classes, and missing rule fields."""
    issues: list[str] = []
    for t in cat.get("tests", []):
        tid = t.get("id", "<no-id>")
        clazz = t.get("class", cat.get("defaults", {}).get("class", "normal"))
        if clazz not in _VALID_CLASSES:
            issues.append(f"{tid}: class {clazz!r} not in {sorted(_VALID_CLASSES)}")
        rule = t.get("verdict", "status_pass")
        if rule == "status_pass":
            continue
        if not isinstance(rule, dict):
            issues.append(f"{tid}: verdict must be 'status_pass' or a mapping, got {rule!r}")
            continue
        kind = rule.get("kind")
        if kind not in _VALID_KINDS:
            issues.append(f"{tid}: unknown verdict kind {kind!r} (expected {sorted(_VALID_KINDS)})")
            continue
        if kind in ("metric", "baseline_rel"):
            if not rule.get("metric"):
                issues.append(f"{tid}: {kind} verdict needs a 'metric' key")
            if rule.get("op") not in _VALID_OPS:
                issues.append(f"{tid}: op {rule.get('op')!r} not in {sorted(_VALID_OPS)}")
            if kind == "metric" and "value" not in rule:
                issues.append(f"{tid}: metric verdict needs a 'value'")
            if kind == "baseline_rel" and "pct_of_baseline" not in rule:
                issues.append(f"{tid}: baseline_rel verdict needs 'pct_of_baseline'")
    return issues


def load_catalog(profile: str) -> dict[str, Any]:
    """Load and lightly validate a requirement catalog by profile name."""
    path = catalog_path(profile)
    if not path.exists():
        raise FileNotFoundError(
            f"unknown CNTC profile {profile!r}; available: {list_profiles()}")
    cat = yaml.safe_load(path.read_text()) or {}
    cat.setdefault("profile", profile)
    cat.setdefault("version", "0")
    cat.setdefault("defaults", {"class": "normal"})
    tests = cat.get("tests") or []
    seen = set()
    for t in tests:
        if "id" not in t:
            raise ValueError(f"{path.name}: every test entry needs an 'id'")
        if t["id"] in seen:
            raise ValueError(f"{path.name}: duplicate test id {t['id']!r}")
        seen.add(t["id"])
    cat["tests"] = tests
    return cat
