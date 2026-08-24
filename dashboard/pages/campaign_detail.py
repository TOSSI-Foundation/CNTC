"""Campaign detail: one run: a scorecard summary (verdict + stats), a sticky scorebar
(revealed on scroll by app.js), suite jump-links, the SUT card, and every suite with its
charts/tables/notes."""
from __future__ import annotations

import dash
from dash import dcc, html

from dashboard.components import pill, suite_section, sut_card
from dashboard.data import get_campaign


def _stimulus_banner(c):
    """Say plainly when a run never stimulated the RAN.

    Almost every requirement is judged on evidence from one UE attach. If that attach stopped
    short, the untested requirements record 'na' and the pass count collapses, which looks like
    a bad RAN but is really an unmeasured one. Without this the two are indistinguishable at a
    glance, and the run invites a conclusion the evidence does not support.
    """
    stim = (c.sut or {}).get("stimulus")
    if not stim or not str(stim).startswith("incomplete"):
        return None
    why = str(stim).partition(":")[2].strip() or "the UE attach did not complete"
    kids = [
        html.Div("Run does not measure this RAN", className="stimulus-head"),
        html.Div(f"The stimulus stopped short: {why}. Requirements past that point were never "
                 f"exercised and are recorded as not applicable, so the verdict below reflects "
                 f"the test rig, not the product. Fix the rig and re-run.",
                 className="stimulus-txt"),
    ]
    # The one fault underneath, quoted from the product that reported it. Every requirement
    # carries its own local reason for being unjudgeable; none of them names the actual cause.
    fault = (c.sut or {}).get("stimulus_fault")
    if fault:
        kids.append(html.Div(className="stimulus-fault", children=[
            html.Div("Root fault", className="stimulus-fault-label"),
            html.Div(str(fault), className="stimulus-fault-txt"),
        ]))
    return html.Div(className="stimulus-void", children=kids)


def _problems(c):
    """What is actually wrong with this deployment, above the per-test detail.

    One misconfiguration usually breaks several requirements, so failures are grouped by the
    underlying fault rather than listed one by one. An operator opening a run should see the
    problems to fix, not a list of symptoms to correlate by hand.
    """
    try:
        from ranbench import diagnostics
    except ImportError:
        return None
    rows = []
    for s in c.suites:
        for t in s.tests:
            if t.status not in ("fail", "error"):
                continue
            cause = (t.metrics or {}).get("probable_cause")
            if not cause and t.notes:
                cause = t.notes.split("  |  Probable cause:")[0].split("[TS")[0].strip()
            rows.append({"id": t.id, "outcome": "fail", "cause": cause})
    if not rows:
        return None
    lines = diagnostics.summary({}, rows)
    if not lines:
        return None
    items = []
    for line in lines:
        text, _, ids = line.partition("  (")
        items.append(html.Div(className="problem", children=[
            html.Span(ids.rstrip(")").split(":")[0] if ids else "", className="problem-n"),
            html.Div(children=[
                html.Div(text, className="problem-txt"),
                html.Div(ids.rstrip(")").partition(":")[2].strip() if ids else "",
                         className="problem-ids"),
            ]),
        ]))
    return html.Div(className="problems", children=[
        html.Div(f"Problems found  ({len(lines)})", className="problems-head"),
        *items,
    ])

dash.register_page(__name__, path_template="/campaign/<key>", name="Campaign")


def _stat(val, label, color=None, mono=False):
    cls = "stat-val" + (" mono" if mono else "")
    return html.Div(className="stat", children=[
        html.Div(val, className=cls, style={"color": color} if color else {}),
        html.Div(label, className="stat-label")])


def _scorecard(c):
    t = c.totals
    cv = c.verdict or {}
    if c.is_running:   # LIVE: campaign still executing, show a pulsing RUNNING state
        sub = f"running {c.running_suite}…" if c.running_suite else "running…"
        verdict = html.Div(className="running-pill", style={"fontSize": "13px", "padding": "6px 14px"},
                           children=[html.Span(className="running-dot"), html.Span("RUNNING"),
                                     html.Span(sub, style={"opacity": .75, "fontWeight": 500,
                                                           "marginLeft": "4px", "letterSpacing": "0"})])
    elif cv:   # prefer the CNTC graded verdict (profile PASS/FAIL/INCOMPLETE + essential gate)
        res = cv.get("result", "?")
        ico = {"PASS": "✓", "FAIL": "✗"}.get(res, "◐")
        verdict = html.Div(className="verdict" + (" fail" if res == "FAIL" else ""), children=[
            html.Span(ico, className="verdict-ico"),
            html.Span(res, className="verdict-txt")])
    else:    # fallback: raw pass/fail from test counts (runs graded before the CNTC layer)
        ok = t["bad"] == 0
        verdict = html.Div(className="verdict" + ("" if ok else " fail"), children=[
            html.Span("✓" if ok else "✗", className="verdict-ico"),
            html.Span("PASS" if ok else "FAIL", className="verdict-txt")])
    head_children = [
        html.H1(c.campaign_id),
        html.Div(className="sc-headmeta", children=[
            pill((c.mode or "?").upper(), variant="mode"),
            pill(c.upf, variant="neutral"),
            html.Span(c.date, className="muted mono", style={"fontSize": "12px"})])]
    if cv and not c.is_running:   # profile + essential gate, given room to breathe in the header
        e = cv.get("essential", {})
        p, tot = e.get("passed", 0), e.get("total", 0)
        pass_all = p >= tot and tot > 0
        head_children.append(html.Div(className="sc-gate", children=[
            html.Span(f"{(cv.get('profile') or '').upper()} profile", className="sc-gate-profile"),
            html.Span("essential gate", className="sc-gate-dot"),
            html.Span([html.B(f"{p}/{tot}"),
                       " essential tests passed" if pass_all else " essential passed"],
                      className="sc-gate-ess" + ("" if pass_all else " sc-gate-ess--fail"))]))
    head = html.Div(className="sc-head", children=head_children)
    coverage = " · ".join(s.label for s in c.suites)
    stats = html.Div(className="sc-stats", children=[
        _stat(str(t["good"]), "tests ok", "var(--good-fg)"),
        _stat(str(t["bad"]), "failed", "var(--bad-fg)" if t["bad"] else "var(--mute)"),
        _stat(str(t["tests"]), "tests run"),
        _stat(str(len(c.suites)), "suites"),
        _stat(coverage, "coverage", "var(--ink-2)", mono=True),
    ])
    return html.Div(className="scorecard", children=[
        html.Div(className="sc-top", children=[verdict, head]), stats])


def _scorebar(c):
    return html.Div(className="scorebar", children=[
        html.Span(c.campaign_id, className="sb-name"),
        pill((c.mode or "?").upper(), variant="mode"),
        html.Div(className="sb-stats", children=[
            html.Span(f"{c.totals['good']} ok", className="tnum",
                      style={"color": "var(--good-fg)"}),
            dcc.Link("All runs", href="/campaigns", className="btn btn-ghost",
                     style={"padding": "5px 11px"})])])


def _certificate_banner(c):
    """Show CNTC certificate status for the run: issued (PASS) or withheld (why)."""
    cv = c.verdict or {}
    if not cv:
        return None
    try:
        from cntc.certification import certificate as _cert
        crt = _cert.issue(cv, c.sut, c.date)
        reason = None if crt else _cert.refusal_reason(cv)
    except Exception:
        crt, reason = None, f"verdict {cv.get('result')}"
    if crt:
        e = crt.get("essential", {})
        meta = [html.Span(f"{crt['profile']} profile"), html.Span("·"),
                html.Span(f"catalog v{crt['catalog_version']}"), html.Span("·"),
                html.Span(f"{e.get('passed', 0)} of {e.get('total', 0)} essential")]
        return html.Div(className="credential credential--issued", children=[
            html.Div(className="credential-seal"),
            html.Div(className="credential-body", children=[
                html.Div(className="credential-status", children=[
                    html.Span("Certified", className="credential-label"),
                    html.Span(crt["certificate_id"], className="credential-id")]),
                html.Div(crt.get("subject", "UPF"), className="credential-subject"),
                html.Div(meta, className="credential-meta"),
                html.Div(", ".join(crt.get("standards", [])), className="credential-standards"),
            ])])
    return html.Div(className="credential credential--withheld", children=[
        html.Div(className="credential-seal credential-seal--withheld"),
        html.Div(className="credential-body", children=[
            html.Span("Not certified", className="credential-label credential-label--withheld"),
            html.Div(reason or "verdict not passing", className="credential-subject credential-subject--sm"),
            html.Div("A certificate is issued only when every essential test passes.",
                     className="credential-standards")])])


def layout(key: str = None, **_):
    c = get_campaign(key) if key else None
    if c is None:
        return html.Div(className="page", children=[
            html.H1("Campaign not found"),
            dcc.Link("← back to runs", href="/campaigns", className="back-link")])
    jump = html.Div(className="suite-jump", children=[
        html.A(s.label, href=f"#suite-{i}", **{"data-jump": "true"})
        for i, s in enumerate(c.suites)])
    return html.Div(children=[
        _scorebar(c),
        html.Div(className="page", children=[
            dcc.Link("← runs", href="/campaigns", className="back-link"),
            _scorecard(c),
            _stimulus_banner(c) or html.Div(),
            _problems(c) or html.Div(),
            _certificate_banner(c),
            # LIVE: reload this page every 5s while the campaign is still running
            dcc.Interval(id="_detail_live", interval=5000, disabled=not c.is_running),
            jump,
            sut_card(c.sut),
            *[suite_section(s, i) for i, s in enumerate(c.suites)],
        ]),
    ])
