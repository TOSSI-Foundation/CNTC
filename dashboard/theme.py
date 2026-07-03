"""Shared visual language for the dashboard — colors, a Plotly template, status helpers.

Keeping this in one place means every chart and card looks like it belongs to the same
product (coRAN Labs / upfbench), which is the whole point of a "showcase" dashboard.
"""
from __future__ import annotations

import plotly.graph_objects as go
import plotly.io as pio

# --- palette (kept in sync with assets/style.css :root tokens — light enterprise) -------
BG = "#ffffff"          # page background (white)
PANEL = "#ffffff"       # card / panel
PANEL2 = "#f6f8fa"      # nested panel / table header
BORDER = "#d0d7de"
GRID = "#e6e9ec"        # chart gridlines (lighter than border)
INK = "#1f2328"         # primary text
MUTE = "#656d76"        # secondary text
ACCENT = "#0d9488"      # brand teal (signal — matches CSS --accent)
ACCENT2 = "#b13a77"     # brand magenta/lotus
GOOD = "#1a7f37"        # pass / measured
WARN = "#9a6700"        # skipped / other
BAD = "#cf222e"         # fail / error / crash

# series colors for multi-line / comparison charts (distinct + legible on white)
SERIES = ["#0d9488", "#be3d7a", "#1a7f37", "#bf8700", "#cf222e", "#0e7490", "#7c3aed"]
# per-mode color so a mode is the same hue everywhere
MODE_COLOR = {"dpdk": "#0d9488", "af_xdp": "#be3d7a", "cndp": "#1a7f37",
              "af_packet": "#bf8700", "afxdp": "#be3d7a", "afpacket": "#bf8700"}

STATUS_COLOR = {"pass": GOOD, "measured": GOOD, "fail": BAD, "error": BAD,
                "skipped": WARN}


def status_color(status: str) -> str:
    return STATUS_COLOR.get((status or "").lower(), MUTE)


def mode_color(mode: str, i: int = 0) -> str:
    return MODE_COLOR.get((mode or "").lower(), SERIES[i % len(SERIES)])


# --- Plotly template ------------------------------------------------------------
_template = go.layout.Template()
_template.layout = go.Layout(
    paper_bgcolor="rgba(0,0,0,0)",
    plot_bgcolor="rgba(0,0,0,0)",
    font=dict(family="Inter, Segoe UI, system-ui, sans-serif", color=INK, size=13),
    colorway=SERIES,
    margin=dict(l=56, r=24, t=48, b=48),
    xaxis=dict(gridcolor=GRID, zerolinecolor=BORDER, linecolor=BORDER, ticks="outside",
               tickcolor=BORDER),
    yaxis=dict(gridcolor=GRID, zerolinecolor=BORDER, linecolor=BORDER, ticks="outside",
               tickcolor=BORDER),
    legend=dict(bgcolor="rgba(0,0,0,0)", bordercolor=BORDER, borderwidth=0,
                orientation="h", yanchor="bottom", y=1.02, x=0),
    hoverlabel=dict(bgcolor=PANEL2, bordercolor=BORDER,
                    font=dict(color=INK, family="Inter, sans-serif")),
    title=dict(font=dict(size=15, color=INK), x=0.01, xanchor="left"),
)
pio.templates["upfbench"] = _template
pio.templates.default = "upfbench"


def apply(fig: go.Figure, height: int = 320, title: str | None = None) -> go.Figure:
    fig.update_layout(template="upfbench", height=height,
                      title=title if title else fig.layout.title.text)
    return fig
