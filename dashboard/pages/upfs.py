"""UPFs — the coverage registry: every UPF the harness can drive, by datapath, with live
run stats for the ones that have campaigns. The multi-UPF story, told honestly."""
from __future__ import annotations

import dash
from dash import html

from dashboard.data import load_campaigns

dash.register_page(__name__, path="/upfs", name="UPFs")

# One adapter per UPF. `match` is the substring used to attribute campaigns by sut.upf.
_REGISTRY = [
    {"logo": "SD", "name": "SD-Core BESS-UPF", "dp": "BESS pipeline · DPDK / kernel-bypass",
     "telemetry": "White-box", "tel_sub": "pybess port counters + black-box", "status": "live",
     "match": "sd-core", "modes": [("DPDK · data", "mode"), ("AF_XDP", "ghost"),
                                   ("CNDP", "ghost"), ("AF_PACKET", "ghost")]},
    {"logo": "OAI", "name": "OAI-UPF", "dp": "VPP graph nodes · DPDK",
     "telemetry": "Black-box", "tel_sub": "UPF port counters", "status": "live",
     "match": "oai", "modes": [("DPDK", "ghost")]},
    {"logo": "O5", "name": "Open5GS UPF", "dp": "TUN / kernel datapath",
     "telemetry": "Black-box", "tel_sub": "UPF port counters", "status": "live",
     "match": "open5gs", "modes": [("TUN", "ghost")]},
    {"logo": "F5", "name": "free5GC UPF-G", "dp": "gtp5g kernel module",
     "telemetry": "Black-box", "tel_sub": "—", "status": "planned",
     "match": "free5gc", "modes": []},
    {"logo": "eU", "name": "eUPF", "dp": "eBPF / XDP",
     "telemetry": "Prometheus", "tel_sub": "white-box XDP counters", "status": "planned",
     "match": "eupf", "modes": []},
]


def _fact(label, value, value_cls="fv", sub=None):
    kids = [html.Div(label, className="fk"), html.Div(value, className=value_cls)]
    if sub:
        kids.append(html.Div(sub, className="muted small",
                             style={"fontFamily": "var(--sans)", "marginTop": "3px"}))
    return html.Div(className="upf-fact", children=kids)


def _card(u, runs, latest):
    status = html.Span(className=f"status {u['status']}", children=[
        html.Span(className="dotc"), "Live" if u["status"] == "live" else "Planned"])
    modes = [html.Span(m, className=f"pill pill--{v}") for m, v in u["modes"]] or \
        [html.Span("adapter ready — no modes profiled", className="muted small")]
    body = [
        _fact("Telemetry", u["telemetry"], sub=u["tel_sub"]),
        _fact("Runs on record", str(runs) if runs else "none yet",
              value_cls="fv" if runs else "fv c-mute"),
        html.Div(className="upf-fact", style={"gridColumn": "1/-1"}, children=[
            html.Div("Dataplane modes", className="fk"),
            html.Div(modes, className="upf-modes")]),
    ]
    if latest:
        body += [_fact("Latest throughput", latest.get("tput", "—")),
                 _fact("Latest p99 latency", latest.get("lat", "—"))]
    foot = html.Div(className="upf-foot", children=[
        html.A(f"View {runs} run{'s' if runs != 1 else ''} →" if runs else "No runs yet",
               href="/campaigns", className="finding-link",
               style={"margin": "0", "marginLeft": "auto"})])
    return html.Div(className="upf-card", children=[
        html.Div(className="upf-head", children=[
            html.Div(u["logo"], className="upf-logo"),
            html.Div(className="upf-tt", children=[
                html.Div(u["name"], className="upf-name"),
                html.Div(u["dp"], className="upf-dp")]),
            status]),
        html.Div(body, className="upf-body"),
        foot])


def layout(**_):
    camps = [c for c in load_campaigns() if c.totals["tests"]]
    cards = []
    for u in _REGISTRY:
        mine = [c for c in camps if u["match"] in (c.upf or "").lower()]
        latest = None
        if mine:
            top = mine[0]
            k = top.kpis or {}
            latest = {"tput": f"{k.get('pdr', '—')} Mpps",
                      "lat": f"{(k.get('lat') or '—').split('/')[-1].strip()} µs"
                             if k.get("lat") else "—"}
        cards.append(_card(u, len(mine), latest))
    n_live = sum(1 for u in _REGISTRY if u["status"] == "live")
    return html.Div(className="page", children=[
        html.Div(className="page-head", children=[
            html.H1("UPFs"),
            html.Div(className="head-badges", children=[
                html.Span(f"{len(_REGISTRY)} in registry", className="pill pill-lg pill--neutral"),
                html.Span(f"{n_live} live", className="pill pill-lg pill--pass")])]),
        html.P(["Every UPF the harness can drive, by datapath. One adapter per UPF "
                "(deploy · configure · counters · teardown) makes them comparable on the "
                "same yardstick. This checkout's campaigns are SD-Core; OAI/Open5GS adapters "
                "exist and populate as their run folders are added."],
               className="muted small", style={"margin": "2px 0 0"}),
        html.Div(cards, className="upf-grid"),
    ])
