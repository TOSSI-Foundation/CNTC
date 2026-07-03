"""CNTC — Cloud Native Telecom Certification Framework.

CNTC is the umbrella layer that turns raw test output from the ``upfbench`` engine (and,
in future, other network-function test engines) into a *graded, standards-aligned verdict*:

    engine results  ->  requirement catalog (standards/)  ->  verdict.evaluate()  ->  scorecard

It is deliberately decoupled from the engine: the evaluator works on the serialized results
structure (plain dicts), so it never imports ``upfbench`` and can re-grade any past
``results.json``. See ``cntc.verdict.evaluate`` and ``cntc/standards/*.yaml``.
"""
from __future__ import annotations

__version__ = "0.1.0"
FRAMEWORK = "CNTC"
FRAMEWORK_LONG = "Cloud Native Telecom Certification Framework"

from cntc.verdict.evaluate import evaluate  # noqa: E402,F401
from cntc.standards import load_catalog, list_profiles  # noqa: E402,F401

__all__ = ["evaluate", "load_catalog", "list_profiles", "__version__", "FRAMEWORK"]
