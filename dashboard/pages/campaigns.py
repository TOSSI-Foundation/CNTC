"""Runs — the results browser: every campaign on disk, newest first, with a search + mode
filter (client-side, via app.js)."""
from __future__ import annotations

import dash
from dash import dcc, html

from dashboard.components import campaign_row
from dashboard.data import load_campaigns

dash.register_page(__name__, path="/campaigns", name="Runs")


def _toolbar(modes, n):
    seg = [html.Button("All modes", className="on", **{"data-mode": "all"})]
    seg += [html.Button(m, **{"data-mode": m}) for m in modes]
    # data-filter-search sits on the .search wrapper (dcc.Input can't take data-* attrs);
    # app.js finds the <input> inside it.
    return html.Div(className="toolbar", children=[
        html.Div(className="search", **{"data-filter-search": "true"}, children=[
            html.Span("⌕", className="search-ico"),
            dcc.Input(type="text", placeholder="Search runs by name or suite…",
                      debounce=False)]),
        html.Div(seg, className="seg", **{"data-filter-mode": "true"}),
        html.Span(f"{n} / {n}", className="list-count", **{"data-filter-count": "true"}),
    ])


def layout(**_):
    camps = load_campaigns()
    measured = [c for c in camps if c.totals["tests"]]
    empty = [c for c in camps if not c.totals["tests"]]
    modes = sorted({(c.mode or "").upper() for c in measured if c.mode and c.mode != "?"})
    children = [
        html.Div(className="page-head", children=[
            html.H1("Runs"),
            html.Div(className="head-badges", children=[
                html.Span(f"{len(measured)} with results", className="pill pill-lg pill--pass"),
                html.Span(f"{len(empty)} empty", className="pill pill-lg pill--neutral")])]),
        _toolbar(modes, len(measured)),
        html.Div(className="camp-list", **{"data-filter-list": "true"},
                 children=[campaign_row(c) for c in measured]),
        html.Div("No runs match.", className="no-results", **{"data-filter-empty": "true"},
                 style={"display": "none"}),
    ]
    if empty:
        children.append(html.Details(className="empty-block", children=[
            html.Summary(f"{len(empty)} empty / debug campaigns"),
            html.Div(className="camp-list", children=[campaign_row(c) for c in empty]),
        ]))
    return html.Div(className="page", children=children)
