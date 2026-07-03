"""The CNTC verdict engine: grade engine results against a requirement catalog."""
from __future__ import annotations

from cntc.verdict.evaluate import evaluate, grade_one  # noqa: F401

__all__ = ["evaluate", "grade_one"]
