"""Methodology — how upfbench drives a UPF: the plugin architecture, the N3 injection
topology (TRex → NIC VEB → UPF access VF), and the egress short-circuit. Showcase content."""
from __future__ import annotations

import dash
from dash import html

from dashboard import theme as T
from dashboard.components import card, pill

dash.register_page(__name__, path="/methodology", name="Methodology")

_PLUGINS = [
    ("Adapters", "deploy / configure / counters / teardown — per UPF", "sdcore_bess ▸ oai ▸ open5gs ▸ free5gc ▸ eupf", T.ACCENT),
    ("Control", "program the data plane", "pfcpsim (N4/PFCP) · pybess (white-box short-circuit)", T.ACCENT2),
    ("Traffic", "offer N3/N6 load", "TRex (DPDK/XDP/CNDP) · testpmd · tcpreplay", T.GOOD),
    ("Suites", "the test cases + pass/fail logic", "performance · load · pfcp · n3neg", T.WARN),
]


def _flow():
    steps = [
        ("TRex", "gen VF on the access PF", T.ACCENT),
        ("NIC VEB", "on-chip switch hairpins by dst MAC", T.MUTE),
        ("UPF access VF", "DPDK/XDP-owned port in the pod", T.ACCENT2),
        ("BESS pipeline", "PDR → QER → FAR", T.GOOD),
        ("core TX", "counted black-box", T.WARN),
    ]
    nodes = []
    for i, (title, sub, color) in enumerate(steps):
        nodes.append(html.Div(className="flow-node", children=[
            html.Div(title, className="flow-title", style={"color": color}),
            html.Div(sub, className="flow-sub")]))
        if i < len(steps) - 1:
            nodes.append(html.Div("→", className="flow-arrow"))
    return html.Div(className="flow", children=nodes)


def layout(**_):
    return html.Div(className="page", children=[
        html.Div(className="page-head", children=[html.H1("Methodology")]),

        card(title="N3 injection — kernel-bypass UPFs",
             sub="no host kernel socket exists on a DPDK/XDP access port, so we hairpin",
             children=[
                 _flow(),
                 html.P(["Frames whose destination MAC equals the UPF's access-VF MAC are "
                         "hairpinned by the NIC's internal switch (VEB) straight into the "
                         "UPF — validated 1:1 (sent on the gen VF = counted at the UPF RX). "
                         "Throughput is computed from the UPF's own port counters, so the "
                         "measurement is black-box and comparable across modes."],
                        className="body"),
             ]),

        card(title="Egress short-circuit",
             sub="isolate the I/O backend, the same method the reference benchmarks use",
             children=html.P([
                 "A pybess splice (", html.Code("executeFAR → ubench_sink → coreQSplit"),
                 ") rewrites the egress MAC and bypasses route lookup, so synthetic traffic "
                 "reaches core TX without a real next-hop — and it breaks the VEB "
                 "re-circulation loop. On non-BESS UPFs this is a no-op."], className="body")),

        html.H2("Plugin architecture"),
        html.Div(className="plugin-grid", children=[
            html.Div(className="plugin-card", children=[
                html.Div([html.B(name), pill("pool", color)], className="plugin-head"),
                html.Div(what, className="plugin-what"),
                html.Div(items, className="plugin-items"),
            ]) for name, what, items, color in _PLUGINS]),

        card(title="Why black-box", className="note",
             children=html.P("Driving real GTP-U on N3 and real PFCP on N4 (instead of "
                              "vendor-specific gRPC) is what makes the framework portable: "
                              "the same suites run against any UPF that speaks the standard "
                              "interfaces. White-box probes (pybess / Prometheus) are an "
                              "optional accuracy tier where the UPF exposes them.", className="body")),
    ])
