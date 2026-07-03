"""Reusable UI building blocks (Dash html/dcc) shared across pages.

Pure presentation: every function returns a Dash component built from already-normalized
data (see data.py). Keeping these here keeps the pages short and the look consistent.
"""
from __future__ import annotations

from dash import dcc, html

from . import theme as T
from .charts import charts_for
from .data import Campaign, Suite, Test


# Map the legacy color args to the redesign's semantic pill classes (see COMPONENTS.md).
_PILL_VARIANT = {
    T.GOOD: "pass", T.BAD: "fail", T.WARN: "warn", T.ACCENT: "mode",
    T.ACCENT2: "violet", T.MUTE: "neutral",
}
_KPI_VARIANT = {T.ACCENT: "accent", T.ACCENT2: "violet", T.GOOD: "good",
                T.WARN: "warn", T.BAD: "bad"}


def pill(text: str, color: str = None, variant: str = None, dot: bool = False) -> html.Span:
    """Status/label badge. Pass a semantic ``variant`` ("pass"/"fail"/"warn"/"mode"/
    "violet"/"neutral"/"crit"/"ghost") or a legacy theme color (mapped to a variant)."""
    v = variant or _PILL_VARIANT.get(color, "neutral")
    cls = f"pill pill--{v}" + (" dot" if dot else "")
    return html.Span(text, className=cls)


def status_pill(status: str) -> html.Span:
    return pill(status.upper() or "—", T.status_color(status))


def kpi_tile(label: str, value, sub: str = "", accent: str = T.ACCENT) -> html.Div:
    rail = _KPI_VARIANT.get(accent, "accent")
    return html.Div(className=f"kpi kpi--{rail}", children=[
        html.Div(str(value), className="kpi-val tnum"),
        html.Div(label, className="kpi-label"),
        html.Div(sub, className="kpi-sub") if sub else None,
    ])


def card(*args, title: str = None, sub: str = None, className: str = "",
         children=None) -> html.Div:
    """Panel with an optional title/subtitle. Body can be passed positionally
    (``card(a, b, title=...)``) or as ``children=`` (a single component or a list)."""
    head = []
    if title:
        head.append(html.Div(title, className="card-title"))
    if sub:
        head.append(html.Div(sub, className="card-sub"))
    body = list(args)
    if children is not None:
        body += children if isinstance(children, (list, tuple)) else [children]
    return html.Div(className=f"card {className}", children=[*head, *body])


def table(rows: list[dict], highlight_col: str = None) -> html.Table:
    """Render a list-of-dicts as a styled table. If highlight_col is given, cells whose
    value reads truthy/'YES' are tinted red (used for the crash column)."""
    if not rows:
        return html.Div("no rows", className="muted")
    cols = list(rows[0].keys())
    head = html.Thead(html.Tr([html.Th(c) for c in cols]))
    body = []
    for r in rows:
        cells = []
        for c in cols:
            v = r.get(c)
            style = {}
            if highlight_col and c == highlight_col and str(v).upper() in ("YES", "TRUE"):
                style = {"color": T.BAD, "fontWeight": "600"}
            cells.append(html.Td(_fmt(v), style=style))
        body.append(html.Tr(cells))
    return html.Table(className="data-table", **{"data-sortable": "true"},
                      children=[head, html.Tbody(body)])


def _fmt(v):
    if isinstance(v, float):
        return f"{v:.4g}"
    if isinstance(v, list):
        return ", ".join(str(x) for x in v) if v else "—"
    return str(v)


def metrics_grid(metrics: dict) -> html.Div:
    """Small key/value grid for a test's scalar metrics (skips list/dict values)."""
    items = [(k, v) for k, v in metrics.items() if not isinstance(v, (dict,))]
    if not items:
        return None
    return html.Div(className="metric-grid", children=[
        html.Div(className="metric", children=[
            html.Div(_fmt(v), className="metric-val"),
            html.Div(k.replace("_", " "), className="metric-key"),
        ]) for k, v in items
    ])


def test_panel(test: Test) -> html.Div:
    figs = charts_for(test)
    children = [
        html.Div(className="test-head", children=[
            html.Span(test.id, className="test-id"),
            html.Span(test.name, className="test-name"),
            status_pill(test.status),
        ]),
    ]
    grid = metrics_grid(test.metrics)
    if grid:
        children.append(grid)
    for title, fig in figs:
        children.append(dcc.Graph(figure=fig, config={"displaylogo": False,
                                  "toImageButtonOptions": {"format": "png", "scale": 3}}))
    for tname, rows in test.tables.items():
        children.append(html.Div(tname, className="table-cap"))
        children.append(table(rows, highlight_col="crashed_bessd"))
    if test.notes:
        children.append(html.Div(test.notes, className="test-notes"))
    return html.Div(className="test-panel", children=children)


def suite_section(suite: Suite, idx: int = 0) -> html.Div:
    c = suite.counts
    summary = []
    if c["good"]:
        summary.append(pill(f"{c['good']} ok", variant="pass"))
    if c["bad"]:
        summary.append(pill(f"{c['bad']} fail", variant="fail"))
    if c["other"]:
        summary.append(pill(f"{c['other']} other", variant="warn"))
    return html.Div(className="suite-section", id=f"suite-{idx}", children=[
        html.Div(className="suite-head", children=[
            html.H3(suite.label), html.Div(summary, className="suite-summary")]),
        *[test_panel(t) for t in suite.tests],
    ])


def sut_card(sut: dict) -> html.Div:
    fields = [("Mode", (sut.get("mode") or "?").upper()), ("UPF", sut.get("upf", "—")),
              ("Image", sut.get("upf_image", "—")), ("NIC", sut.get("nic", "—")),
              ("CPU", sut.get("cpu", "—")), ("Platform", sut.get("platform", "—")),
              ("N3 / N6", f"{sut.get('n3_iface','?')} / {sut.get('n6_iface','?')}"),
              ("UE pool", sut.get("ue_ip_pool", "—"))]
    return card(title="System under test",
                children=html.Div(className="sut-grid", children=[
                    html.Div(className="sut-row", children=[
                        html.Span(k, className="sut-k"), html.Span(str(v), className="sut-v")])
                    for k, v in fields]))


def campaign_row(c: Campaign):
    """A campaign list row. Uses html.A (not dcc.Link) so the data-* attributes the
    client-side filter (app.js) reads can be attached."""
    t = c.totals
    badges = []
    if c.is_running:   # LIVE: pulsing indicator on a campaign that's still executing
        badges.append(html.Span(className="running-pill", children=[
            html.Span(className="running-dot"), html.Span("RUNNING")]))
    if t["good"]:
        badges.append(pill(f"{t['good']} ok", variant="pass"))
    if t["bad"]:
        badges.append(pill(f"{t['bad']} fail", variant="fail"))
    suites = " · ".join(s.label for s in c.suites) or "no suites"
    return html.A(href=f"/campaign/{c.key}", className="camp-row",
                  **{"data-camp": c.campaign_id, "data-suites": suites,
                     "data-mode": (c.mode or "").upper()},
                  children=[
        html.Div(className="camp-main", children=[
            html.Div(c.campaign_id, className="camp-name"),
            html.Div(suites, className="camp-suites")]),
        html.Div(className="camp-meta", children=[
            pill((c.mode or "?").upper(), variant="mode"),
            html.Span(c.date, className="camp-date"), *badges]),
    ])
