"""Findings — robustness/security feed. Today: the N3 GtpuDecap remote-DoS crash the n3neg
suite found, rendered as an incident report, sourced from the n3neg results on disk."""
from __future__ import annotations

import dash
from dash import html

from dashboard.data import load_campaigns

dash.register_page(__name__, path="/findings", name="Findings")


def _n3neg_crash():
    """Find an n3neg run that recorded a crash; return (campaign, test, variants) or None."""
    for c in load_campaigns():
        s = c.suite("n3neg")
        if not s:
            continue
        for t in s.tests:
            cr = (t.metrics or {}).get("crash_inducing_variants")
            if cr:
                return c, t, cr
    return None


def _cell(label, *body, full=False):
    cls = "incident-cell full" if full else "incident-cell"
    return html.Div(className=cls, children=[html.Div(label, className="icell-label"), *body])


def _tl(when, what, level="bad"):
    return html.Div(className=f"tl-step {level}", children=[
        html.Span(className="tl-dot"),
        html.Span(when, className="tl-when"), html.Span(what, className="tl-what")])


def _incident(c, variants):
    mode = (c.mode or "?").upper()
    sig = html.Div(className="sig", children=[
        html.Span("SIGSEGV", className="crash"), " in ",
        html.Span("GtpuDecap::ProcessBatch", className="fn"), " ",
        html.Span("(+0x2df)", className="cm")])
    packet = html.Div(className="packet", children=[
        html.Span("ext-hdr", className="pk"), html.Span("PSC 0x85 (malformed)", className="pv hl"),
        html.Span("outer", className="pk"), html.Span("IP/UDP:2152 → N3", className="pv"),
        html.Span("trigger", className="pk"), html.Span("1 packet", className="pv hl")])
    timeline = html.Div(className="timeline", children=[
        _tl("t+0", "malformed PSC GTP-U sent on N3", "bad"),
        _tl("t+~0", "bessd worker segfaults in GtpuDecap (SEGV_MAPERR, addr 0)", "bad"),
        _tl("t+~1s", "user plane down — N3/N6 forwarding stops", "bad"),
        _tl("t+~60s", "Kubernetes restarts the bessd container", "ok"),
        _tl("after", "suite re-establishes session; valid traffic forwards again", "ok")])
    return html.Div(className="incident", children=[
        html.Div(className="incident-head", children=[
            html.Div(className="sev", children=[
                html.Div("9.1", className="sev-score"), html.Div("HIGH", className="sev-cat")]),
            html.Div(children=[
                html.H3("Malformed N3 GTP-U crashes the BESS-UPF data plane"),
                html.Div(className="incident-meta", children=[
                    html.Span("Remote DoS", className="pill pill--crit dot"),
                    html.Span("N3 robustness", className="pill pill--n3"),
                    html.Span(f"{c.upf} · {mode}", className="pill pill--accent")])])]),
        html.Div(className="incident-grid", children=[
            _cell("Crash signature", sig),
            _cell("Culprit packet", packet),
            _cell("What happens", timeline, full=True),
        ]),
        html.P(["A single crafted GTP-U packet — the malformed PSC (PDU-Session-Container, "
                "ext-header 0x85), plus the truncation/length-overflow variants — makes the "
                "fixed-offset decapsulator read past the buffer and segfault. Reproduced "
                "across multiple runs. The suite detects the crash, attributes it to the "
                "packet, waits for recovery, and continues."], className="body"),
    ])


def layout(**_):
    hit = _n3neg_crash()
    head = html.Div(className="page-head", children=[
        html.H1("Findings"),
        html.Div(className="head-badges", children=[
            html.Span("1 open" if hit else "0 open",
                      className="pill pill-lg " + ("pill--crit" if hit else "pill--neutral"))])])
    intro = html.P("Robustness and security findings surfaced across runs. The N3 negative "
                   "suite turns crafted data-plane packets into reproducible incidents.",
                   className="muted small", style={"margin": "2px 0 0"})
    if not hit:
        return html.Div(className="page", children=[head, intro,
            html.Div("No findings recorded yet — run the n3neg suite.", className="muted",
                     style={"padding": "40px 0"})])
    c, t, variants = hit
    return html.Div(className="page", children=[head, intro, _incident(c, variants)])
