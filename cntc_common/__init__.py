"""cntc_common: primitives shared by every CNTC measurement engine.

Both ``upfbench`` (Stage 1, user plane) and ``cpbench`` (Stage 2, control plane) emit the
**same** ``results.json`` schema so the one CNTC verdict/certification/dashboard stack grades
them identically. That schema lives here: :class:`TestResult`, :class:`SuiteResult`,
:class:`Store`. ``upfbench.results`` re-exports these for backward compatibility.
"""
from __future__ import annotations

from cntc_common.results import Store, SuiteResult, TestResult, _flatten

__all__ = ["Store", "SuiteResult", "TestResult", "_flatten"]
