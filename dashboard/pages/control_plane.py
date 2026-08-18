"""Control plane, the coverage registry for the 5G core network functions: every NF the
harness certifies (AMF/SMF/NRF/AUSF/UDM), the interfaces and specs each is driven against, its
Level-1 test counts, and the latest live verdict for the ones that have campaigns.

The control-plane counterpart to the UPFs page, same card layout, so the dashboard tells the
whole-core story (user plane AND control plane), not just the UPF."""
from __future__ import annotations

import dash
from dash import html

from dashboard.data import load_campaigns
from dashboard.catalog_data import cp_nf_counts

dash.register_page(__name__, path="/control-plane", name="Control plane")

# Static per-NF metadata (interfaces, driver, spec anchors). Test counts come live from the
# conformance catalogs; run results come live from campaigns. `key` = the NF id used to match.
_NF = [
    {"logo": "AMF", "key": "amf", "name": "AMF", "full": "Access & Mobility Management",
     "iface": "N1/N2 (NAS · NGAP) · SBI (Namf)", "driver": "UERANSIM + SBI client",
     "specs": ["TS 24.501", "TS 38.413", "TS 33.501", "TS 33.512"]},
    {"logo": "SMF", "key": "smf", "name": "SMF", "full": "Session Management",
     "iface": "N4/PFCP · SBI (Nsmf)", "driver": "UERANSIM + N4 capture + SBI",
     "specs": ["TS 29.502", "TS 29.244", "TS 24.501", "TS 33.515"]},
    {"logo": "NRF", "key": "nrf", "name": "NRF", "full": "NF Repository",
     "iface": "SBI (Nnrf)", "driver": "SBI client",
     "specs": ["TS 29.510", "TS 33.518"]},
    {"logo": "AUSF", "key": "ausf", "name": "AUSF", "full": "Authentication Server",
     "iface": "SBI (Nausf)", "driver": "SBI client + transitive (via 5G-AKA)",
     "specs": ["TS 29.509", "TS 33.501", "TS 33.516"]},
    {"logo": "UDM", "key": "udm", "name": "UDM", "full": "Unified Data Management",
     "iface": "SBI (Nudm)", "driver": "SBI client + transitive (via registration)",
     "specs": ["TS 29.503", "TS 33.501", "TS 33.514"]},
]

# verdict result -> (label, pill modifier)
_RESULT_PILL = {"PASS": ("PASS", "pass"), "FAIL": ("FAIL", "fail"),
                "INCOMPLETE": ("INCOMPLETE", "warn")}


def _nf_runs(nf: str, camps):
    """Campaigns that carry a verdict for this NF, newest first, as (result, campaign)."""
    out = []
    for c in camps:
        v = c.verdict or {}
        prof = v.get("profile") or ""
        per = v.get("per_nf") or {}
        if prof == f"{nf}-conformance":
            out.append((v.get("result", "?"), c))
        elif nf in per:
            out.append((per[nf], c))
    return out


def _fact(label, value, value_cls="fv", sub=None):
    kids = [html.Div(label, className="fk"), html.Div(value, className=value_cls)]
    if sub:
        kids.append(html.Div(sub, className="muted small",
                             style={"fontFamily": "var(--sans)", "marginTop": "3px"}))
    return html.Div(className="upf-fact", children=kids)


def _card(nf, counts, runs):
    total, ess = counts.get(nf["key"], (0, 0))
    latest_result, latest_camp = (runs[0] if runs else (None, None))
    if latest_result:
        label, mod = _RESULT_PILL.get(latest_result, (latest_result, "neutral"))
        status = html.Span(className=f"status {'live' if mod == 'pass' else 'planned'}",
                           children=[html.Span(className="dotc"),
                                     "Certified" if mod == "pass" else "Tested"])
    else:
        status = html.Span(className="status planned",
                           children=[html.Span(className="dotc"), "Ready"])

    specs = [html.Span(s, className="pill pill--neutral") for s in nf["specs"]]
    body = [
        _fact("Interfaces", nf["iface"]),
        _fact("Level-1 tests", f"{total}", sub=f"{ess} essential (gate the certificate)"),
        html.Div(className="upf-fact", style={"gridColumn": "1/-1"}, children=[
            html.Div("Standards", className="fk"),
            html.Div(specs, className="upf-modes")]),
        _fact("Driven via", nf["driver"]),
    ]
    if latest_result:
        rlabel, rmod = _RESULT_PILL.get(latest_result, (latest_result, "neutral"))
        body.append(_fact("Latest verdict",
                          html.Span(rlabel, className=f"pill pill--{rmod}"),
                          sub=f"{latest_camp.key} · {latest_camp.date}"))
    foot = html.Div(className="upf-foot", children=[
        html.A(f"View {len(runs)} run{'s' if len(runs) != 1 else ''} →" if runs else "No runs yet",
               href="/campaigns", className="finding-link",
               style={"margin": "0", "marginLeft": "auto"})])
    return html.Div(className="upf-card", children=[
        html.Div(className="upf-head", children=[
            html.Div(nf["logo"], className="upf-logo"),
            html.Div(className="upf-tt", children=[
                html.Div(f"{nf['name']}, {nf['full']}", className="upf-name"),
                html.Div(nf["iface"], className="upf-dp")]),
            status]),
        html.Div(body, className="upf-body"),
        foot])


def layout(**_):
    camps = [c for c in load_campaigns() if c.totals["tests"]]
    counts = cp_nf_counts()
    total_tests = sum(t for t, _ in counts.values())
    total_ess = sum(e for _, e in counts.values())
    cards = [_card(nf, counts, _nf_runs(nf["key"], camps)) for nf in _NF]
    return html.Div(className="page", children=[
        html.Div(className="page-head", children=[
            html.H1("Control plane"),
            html.Div(className="head-badges", children=[
                html.Span(f"{len(_NF)} network functions", className="pill pill-lg pill--neutral"),
                html.Span(f"{total_tests} Level-1 tests", className="pill pill-lg pill--neutral"),
                html.Span(f"{total_ess} essential", className="pill pill-lg pill--pass")])]),
        html.P(["Every 5G-core network function the harness certifies, driven over its real "
                "interfaces (N1/N2 signalling and the Service-Based Interface) and graded against "
                "its own 3GPP protocol + SCAS security spec. An NF earns a certificate only when "
                "all of its essential tests pass, INCOMPLETE (not a pass) when a case can't be "
                "judged on a deployment."],
               className="muted small", style={"margin": "2px 0 0"}),
        html.Div(cards, className="upf-grid"),
    ])
