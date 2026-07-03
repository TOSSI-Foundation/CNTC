"""Render a campaign's results.json into the per-suite report.

Picks the template by suite (performance.tex.j2 / load.tex.j2 / pfcp.tex.j2), fills it
with the stored metrics, and compiles to PDF if a LaTeX toolchain (pdflatex) is present.
Falls back to writing the filled .tex so nothing is lost when LaTeX is absent.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from upfbench.results import Store

TEMPLATES = Path(__file__).parent / "templates"

# Jinja delimiters that don't collide with LaTeX's {{ }} and { }.
_env = Environment(
    loader=FileSystemLoader(str(TEMPLATES)),
    block_start_string="((*", block_end_string="*))",
    variable_start_string="(((", variable_end_string=")))",
    comment_start_string="((#", comment_end_string="#))",
    autoescape=select_autoescape(enabled_extensions=()),
    trim_blocks=True, lstrip_blocks=True,
)

# LaTeX special-character escaping for free-text values (image tags with '_', etc.).
_TEX_SPECIAL = {
    "\\": r"\textbackslash{}", "&": r"\&", "%": r"\%", "$": r"\$", "#": r"\#",
    "_": r"\_", "{": r"\{", "}": r"\}", "~": r"\textasciitilde{}",
    "^": r"\textasciicircum{}",
}


def texesc(value) -> str:
    return "".join(_TEX_SPECIAL.get(ch, ch) for ch in str(value))


_env.filters["tex"] = texesc

_TEMPLATE_FOR = {
    "performance": "performance.tex.j2",
    "load": "load.tex.j2",
    "pfcp": "pfcp.tex.j2",
    "n3neg": "n3neg.tex.j2",
}


def build(results_path: str | Path, out_dir: Path, cfg) -> list[Path]:
    """Render one report per suite present (report-<suite>.pdf, or .tex if no LaTeX).

    If ``cfg.baseline`` points at a prior campaign's results.json, its KPIs are passed
    to the templates for a comparison section. Returns the rendered file paths.
    """
    data = Store.load(results_path)
    baseline = _load_baseline(cfg)
    suites = data.get("suites", [])
    if not suites:                       # nothing ran; still emit a stub for the 1st suite
        suites = [{"suite": cfg.suites[0], "tests": []}]

    outputs: list[Path] = []
    for suite in suites:
        name = suite["suite"]
        template = _env.get_template(_TEMPLATE_FOR.get(name, "performance.tex.j2"))
        tex = template.render(c=data, cfg=cfg, suite=suite, baseline=baseline)
        tex_path = out_dir / f"report-{name}.tex"
        tex_path.write_text(tex)
        outputs.append(_compile(tex_path, out_dir))

    # Combined "all suites in one document" report (cover + summary + every suite).
    # Emitted whenever more than one suite ran, so a single hand-off PDF exists.
    if len(suites) > 1:
        tex = _env.get_template("all.tex.j2").render(c=data, cfg=cfg, baseline=baseline)
        tex_path = out_dir / "report-all.tex"
        tex_path.write_text(tex)
        outputs.append(_compile(tex_path, out_dir))
    return outputs


def _load_baseline(cfg):
    path = getattr(cfg, "baseline", None)
    if path and Path(path).exists():
        return Store.load(path)
    return None


def _compile(tex_path: Path, out_dir: Path) -> Path:
    """Compile tex -> pdf if pdflatex is present; else return the .tex path."""
    if not shutil.which("pdflatex"):
        return tex_path
    try:
        for _ in range(2):     # run twice so longtable column widths settle
            subprocess.run(["pdflatex", "-interaction=nonstopmode", tex_path.name],
                           cwd=out_dir, check=True,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return tex_path.with_suffix(".pdf")
    except subprocess.CalledProcessError:
        return tex_path
