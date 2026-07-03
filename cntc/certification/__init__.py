"""Certification outputs — turn a verdict block into human-facing scorecards."""
from __future__ import annotations

from cntc.certification.scorecard import (  # noqa: F401
    render_console, render_markdown, render_html, write_scorecard,
)
from cntc.certification import certificate  # noqa: F401

__all__ = ["render_console", "render_markdown", "render_html", "write_scorecard",
           "certificate"]
