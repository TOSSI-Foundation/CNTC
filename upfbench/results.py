"""Campaign result store, re-exported from :mod:`cntc_common`.

The result schema (``TestResult`` / ``SuiteResult`` / ``Store``) is shared by every CNTC
measurement engine, so it lives in ``cntc_common.results``. This module keeps the historical
``upfbench.results`` import path working unchanged (Stage-1 suites import from here) while the
single source of truth is ``cntc_common``.
"""
from __future__ import annotations

from cntc_common.results import Store, SuiteResult, TestResult, _flatten

__all__ = ["Store", "SuiteResult", "TestResult", "_flatten"]
