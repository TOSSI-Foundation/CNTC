"""upfbench dashboard — a live, view-only Plotly Dash app over the campaigns/ results.

Run it:
    python3 -m dashboard.app                 # http://0.0.0.0:8050
    UPFBENCH_DASH_PORT=9000 python3 -m dashboard.app

It reads campaigns/ fresh on each page load, so new runs appear without a restart.
"""
from __future__ import annotations

import os
from pathlib import Path

import dash
from dash import Dash, Input, Output, State, clientside_callback, dcc, html

from . import theme as T  # noqa: F401  (registers the Plotly template on import)

PAGES = Path(__file__).resolve().parent / "pages"

app = Dash(__name__, use_pages=True, pages_folder=str(PAGES),
           title="CNTC · Cloud Native Telecom Certification",
           suppress_callback_exceptions=True)
server = app.server

# UPF-first IA (redesign v2): nav grouped into Results + Reference.
_NAV = [
    ("Results", [("/", "Overview"), ("/upfs", "UPFs"), ("/campaigns", "Runs"),
                 ("/compare", "Compare"), ("/findings", "Findings")]),
    ("Reference", [("/catalog", "Test catalog"), ("/methodology", "Methodology")]),
]


def _nav_link(href: str, label: str):
    # active state is applied client-side (matches the current path); render plain.
    return dcc.Link(label, href=href, className="nav-link")


def _sidebar() -> html.Div:
    groups = []
    for eyebrow, items in _NAV:
        groups.append(html.Div(eyebrow, className="nav-eyebrow"))
        groups.extend(_nav_link(h, l) for h, l in items)
    return html.Div(className="sidebar", children=[
        html.Div(className="brand", children=[
            html.Img(src="/assets/tossi.jpeg", className="brand-logo", alt="TOSSI"),
            html.Div(children=[
                html.Div("CNTC", className="brand-name"),
                html.Div("Cloud Native Telecom Certification", className="brand-sub"),
            ]),
        ]),
        html.Nav(className="nav", children=groups),
        html.Div(className="sidebar-foot", children=[
            html.Div("TOSSI", className="foot-org"),
            html.Div("Telecom · Open Source · System Integrator", className="foot-tag"),
        ]),
    ])


_LIVE_MS = int(os.environ.get("UPFBENCH_DASH_LIVE_MS", "15000"))   # 0 disables live refresh

app.layout = html.Div(className="app", children=[
    dcc.Location(id="url"),
    dcc.Interval(id="_live", interval=max(3000, _LIVE_MS), disabled=(_LIVE_MS <= 0)),
    _sidebar(),
    html.Main(className="content", children=[dash.page_container]),
    html.Div(id="_nav_sink", style={"display": "none"}),
    html.Div(id="_live_sink", style={"display": "none"}),
])

# LIVE dashboard: auto-refresh the runs list / overview so new campaigns appear as tests
# finish, without a manual reload. Skips detail pages so it won't interrupt reading a run.
clientside_callback(
    """
    function(n, path){
        if (n && (path === '/' || path === '/campaigns')) { window.location.reload(); }
        return '';
    }
    """,
    Output("_live_sink", "children"),
    Input("_live", "n_intervals"),
    State("url", "pathname"),
)

# LIVE (detail): a running campaign's detail page carries an enabled dcc.Interval
# (#_detail_live); reload it every tick so tests appear as they finish. The interval is
# disabled on completed campaigns, so this only fires while a run is in progress.
clientside_callback(
    "function(n){ if(n){ window.location.reload(); } return ''; }",
    Output("_live_sink", "title"),
    Input("_detail_live", "n_intervals"),
)

# Highlight the active nav link on every navigation (the sidebar is static, so do it
# client-side). Also re-run app.js's progressive enhancements after Dash swaps the page.
clientside_callback(
    """
    function(path){
        try {
            document.querySelectorAll('.nav-link').forEach(function(a){
                var href = a.getAttribute('data-href') || a.getAttribute('href') || '';
                var on = href === '/' ? path === '/' : (path && path.indexOf(href) === 0);
                a.classList.toggle('active', !!on);
                if (on) { a.setAttribute('aria-current','page'); }
                else { a.removeAttribute('aria-current'); }
            });
            if (window.upfbenchInit) { setTimeout(window.upfbenchInit, 60); }
        } catch(e){}
        return '';
    }
    """,
    Output("_nav_sink", "children"),
    Input("url", "pathname"),
)


if __name__ == "__main__":
    port = int(os.environ.get("UPFBENCH_DASH_PORT", "8050"))
    host = os.environ.get("UPFBENCH_DASH_HOST", "0.0.0.0")
    debug = os.environ.get("UPFBENCH_DASH_DEBUG", "0") == "1"
    app.run(host=host, port=port, debug=debug)
