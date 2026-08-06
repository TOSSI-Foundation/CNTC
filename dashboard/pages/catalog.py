"""Test catalog — the full menu of what upfbench can measure, suite by suite, with the
standard each test maps to. Static showcase content (describes the framework, not a run)."""
from __future__ import annotations

import dash
from dash import html

from dashboard.components import card
from dashboard.catalog_data import (DOMAINS, SUITE_CATID, standard_links,
                                     catalog_summary, domain_summary)

dash.register_page(__name__, path="/catalog", name="Test catalog")


def _standard_cell(std):
    """Render each standard token as a new-tab link (if known) or a neutral pill."""
    items = []
    for label, url in standard_links(std):
        if url:
            items.append(html.A(label, href=url, target="_blank", rel="noopener",
                                className="pill pill--neutral std-link"))
        else:
            items.append(html.Span(label, className="pill pill--neutral"))
    return html.Td(html.Div(items, style={"display": "flex", "gap": "5px", "flexWrap": "wrap"}))


def _suite_block(name, accent, tests):
    catid = SUITE_CATID.get(name, "")
    rows = [html.Tr([
        html.Td(html.Span(tid, className=f"cat-id cat-id--{catid}" if catid else "cat-id")),
        html.Td(html.B(tname)),
        html.Td(what, className="cat-what"),
        _standard_cell(std),
    ]) for tid, tname, what, std in tests]
    return card(title=name, sub=f"{len(tests)} test cases", children=html.Table(
        className="data-table catalog-table",
        children=[html.Thead(html.Tr([html.Th("ID"), html.Th("Test"),
                                      html.Th("What it measures"), html.Th("Standard")])),
                  html.Tbody(rows)]))


def _domain_block(title, blurb, catalog):
    return html.Section(className="catalog-domain", children=[
        html.Div(className="domain-head", children=[
            html.H2(title, className="domain-title"),
            html.Span(domain_summary(catalog), className="pill pill--neutral"),
            html.Div(blurb, className="muted small domain-blurb")]),
        *[_suite_block(name, accent, tests) for name, accent, tests in catalog],
    ])


def layout(**_):
    return html.Div(className="page", children=[
        html.Div(className="page-head", children=[
            html.H1("Test catalog"),
            html.Div(catalog_summary(), className="muted")]),
        *[_domain_block(title, blurb, catalog) for title, blurb, catalog in DOMAINS],
    ])
