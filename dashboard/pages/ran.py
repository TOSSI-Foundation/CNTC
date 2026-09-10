"""RAN: the coverage registry for the split gNB: every product class the harness certifies
(O-CU-CP, O-CU-UP, O-DU), the interfaces and specs each is driven against, its Level-1 test
counts, and the latest live verdict for the ones that have campaigns.

The RAN counterpart to the Control plane and UPFs pages, same card layout, so the dashboard
tells the whole-stack story: core control plane, RAN, and user plane."""
from __future__ import annotations

import dash
from dash import html

from dashboard.data import load_campaigns
from dashboard.catalog_data import ran_target_counts

dash.register_page(__name__, path="/ran", name="RAN")

# Static per-class metadata (interfaces, driver, spec anchors). Test counts come live from the
# conformance catalogs; run results come live from campaigns. `key` = the target id used to match.
_TARGETS = [
    {"logo": "DU", "key": "du", "name": "O-DU", "full": "Distributed Unit",
     "iface": "F1-C (F1AP) · F1-U (GTP-U) · Uu (RLC/MAC/PHY)",
     "driver": "UE over the virtual radio + F1 capture",
     "specs": ["TS 38.473", "TS 38.425", "TS 38.331", "TS 33.523"]},
    {"logo": "CU-CP", "key": "cucp", "name": "O-CU-CP", "full": "Centralised Unit, control plane",
     "iface": "N2 (NGAP) · F1-C (F1AP) · E1 (E1AP) · RRC",
     "driver": "AMF + O-DU + UE, with F1AP/NGAP/E1AP capture",
     "specs": ["TS 38.413", "TS 38.473", "TS 38.463", "TS 38.331", "TS 33.511", "TS 33.523"]},
    {"logo": "CU-UP", "key": "cuup", "name": "O-CU-UP", "full": "Centralised Unit, user plane",
     "iface": "E1 (E1AP) · F1-U (GTP-U) · N3 (GTP-U)",
     "driver": "CU-CP over E1 + UE traffic, with F1-U/N3 capture",
     "specs": ["TS 38.463", "TS 38.425", "TS 38.415", "TS 33.523"]},
    # A second cut, at the FAPI boundary rather than F1. These two anchor to the Small Cell
    # Forum, not 3GPP: there is no SCAS product class for an L1, so the certificate says
    # "SCF interface conformance", not "3GPP SCAS product class".
    {"logo": "PNF", "key": "pnf", "name": "PNF", "full": "L1 / PHY over nFAPI",
     "iface": "nFAPI P5 (SCTP) · P7 (UDP)",
     "driver": "VNF drives the PHY; one nFAPI capture read PNF→VNF",
     "specs": ["SCF222", "SCF225"]},
    {"logo": "VNF", "key": "vnf", "name": "VNF", "full": "L2 driver over nFAPI",
     "iface": "nFAPI P5 (SCTP) · P7 (UDP)",
     "driver": "same capture read VNF→PNF",
     "specs": ["SCF222", "SCF225"]},
]

# Product classes that certify against an SCF interface spec rather than a 3GPP SCAS class. The
# distinction is stated on the certificate, so it is stated on the card too.
_SCF_CLASSES = {"pnf", "vnf"}

# verdict result -> (label, pill modifier)
_RESULT_PILL = {"PASS": ("PASS", "pass"), "FAIL": ("FAIL", "fail"),
                "INCOMPLETE": ("INCOMPLETE", "warn")}


def _target_runs(target: str, camps):
    """Campaigns carrying a verdict for this product class, newest first, as (result, campaign)."""
    out = []
    for c in camps:
        v = c.verdict or {}
        prof = v.get("profile") or ""
        per = v.get("per_target") or {}
        if prof == f"{target}-conformance":
            out.append((v.get("result", "?"), c))
        elif target in per:
            out.append((per[target], c))
    return out


def _fact(label, value, value_cls="fv", sub=None):
    kids = [html.Div(label, className="fk"), html.Div(value, className=value_cls)]
    if sub:
        kids.append(html.Div(sub, className="muted small",
                             style={"fontFamily": "var(--sans)", "marginTop": "3px"}))
    return html.Div(className="upf-fact", children=kids)


def _card(target, counts, runs):
    total, ess = counts.get(target["key"], (0, 0))
    latest_result, latest_camp = (runs[0] if runs else (None, None))
    if latest_result:
        _, mod = _RESULT_PILL.get(latest_result, (latest_result, "neutral"))
        status = html.Span(className=f"status {'live' if mod == 'pass' else 'planned'}",
                           children=[html.Span(className="dotc"),
                                     "Certified" if mod == "pass" else "Tested"])
    else:
        status = html.Span(className="status planned",
                           children=[html.Span(className="dotc"), "Ready"])

    specs = [html.Span(s, className="pill pill--neutral") for s in target["specs"]]
    body = []
    if target["key"] in _SCF_CLASSES:
        body.append(html.Div("SCF interface conformance, not a 3GPP SCAS product class",
                             className="muted small", style={"gridColumn": "1/-1",
                             "fontFamily": "var(--sans)", "fontStyle": "italic"}))
    body += [
        _fact("Interfaces", target["iface"]),
        _fact("Level-1 tests", f"{total}", sub=f"{ess} essential (gate the certificate)"),
        html.Div(className="upf-fact", style={"gridColumn": "1/-1"}, children=[
            html.Div("Standards", className="fk"),
            html.Div(specs, className="upf-modes")]),
        _fact("Driven via", target["driver"]),
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
            html.Div(target["logo"], className="upf-logo"),
            html.Div(className="upf-tt", children=[
                html.Div(f"{target['name']}, {target['full']}", className="upf-name"),
                html.Div(target["iface"], className="upf-dp")]),
            status]),
        html.Div(body, className="upf-body"),
        foot])


def layout(**_):
    camps = [c for c in load_campaigns() if c.totals["tests"]]
    counts = ran_target_counts()
    total_tests = sum(t for t, _ in counts.values())
    total_ess = sum(e for _, e in counts.values())
    cards = [_card(t, counts, _target_runs(t["key"], camps)) for t in _TARGETS]
    return html.Div(className="page", children=[
        html.Div(className="page-head", children=[
            html.H1("RAN"),
            html.Div(className="head-badges", children=[
                html.Span(f"{len(_TARGETS)} product classes", className="pill pill-lg pill--neutral"),
                html.Span(f"{total_tests} Level-1 tests", className="pill pill-lg pill--neutral"),
                html.Span(f"{total_ess} essential", className="pill pill-lg pill--pass")])]),
        html.P(["The NG-RAN node, cut two ways. The CU/DU split (O-CU-CP, O-CU-UP, O-DU) is "
                "certified the way 3GPP TS 33.523 decomposes it, one catalog and one certificate "
                "per product class, each driven over its own interfaces and graded against its "
                "protocol + SCAS spec. The L1/L2 split (PNF, VNF) cuts lower, at the FAPI "
                "boundary, and anchors to the Small Cell Forum (SCF222 / SCF225): 3GPP defines "
                "no product class for an L1, so those two certificates assert SCF interface "
                "conformance, not a SCAS product class. A class earns a certificate only when "
                "all of its essential tests pass, INCOMPLETE (never a pass) when a case cannot "
                "be judged."],
               className="muted small", style={"margin": "2px 0 0"}),
        html.Div(cards, className="upf-grid"),
    ])
