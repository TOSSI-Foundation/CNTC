"""Generate a self-contained STATIC HTML build of the dashboard for handoff to a
designer (or offline share).

Why: designers work on HTML/CSS/JS, not Python/Dash. This renders the real pages with
real campaign data, the actual style.css, and live Plotly charts — a renderable mini-site a
designer can open and redesign. It reuses the dashboard's own data loader and chart builders
so the static output matches the live app.

    python3 -m dashboard.export_static               # -> dashboard/static_export/
    python3 -m dashboard.export_static --out /path   # custom output dir

The markup intentionally uses the same CSS classes as the Dash components so the look is
identical and the design tokens live in one file (style.css).
"""
from __future__ import annotations

import argparse
import html
import shutil
from pathlib import Path

import plotly.io as pio

from . import theme as T  # registers the Plotly template
from .charts import charts_for
from .data import SUITE_LABEL, load_campaigns

HERE = Path(__file__).resolve().parent
CSS_SRC = HERE / "assets" / "style.css"

NAV = [("index.html", "Overview"), ("campaigns.html", "Campaigns"),
       ("compare.html", "Compare modes"), ("catalog.html", "Test catalog"),
       ("methodology.html", "Methodology")]


def esc(s) -> str:
    return html.escape(str(s))


# --- small HTML component builders (mirror components.py, same CSS classes) -------
def pill(text, color) -> str:
    return (f'<span class="pill" style="background:{color}22;color:{color};'
            f'border:1px solid {color}55">{esc(text)}</span>')


def status_pill(status) -> str:
    return pill((status or "—").upper(), T.status_color(status))


def kpi(label, value, sub="", accent=T.ACCENT) -> str:
    s = f'<div class="kpi-sub">{esc(sub)}</div>' if sub else ""
    return (f'<div class="kpi"><div class="kpi-val" style="color:{accent}">{esc(value)}</div>'
            f'<div class="kpi-label">{esc(label)}</div>{s}</div>')


def card(body, title=None, sub=None, cls="") -> str:
    h = f'<div class="card-title">{esc(title)}</div>' if title else ""
    h += f'<div class="card-sub">{esc(sub)}</div>' if sub else ""
    return f'<div class="card {cls}">{h}{body}</div>'


def table(rows, highlight="crashed_bessd") -> str:
    if not rows:
        return '<div class="muted">no rows</div>'
    cols = list(rows[0].keys())
    head = "".join(f"<th>{esc(c)}</th>" for c in cols)
    trs = []
    for r in rows:
        tds = []
        for c in cols:
            v = r.get(c)
            style = ""
            if c == highlight and str(v).upper() in ("YES", "TRUE"):
                style = ' style="color:#f85149;font-weight:600"'
            tds.append(f"<td{style}>{esc(_fmt(v))}</td>")
        trs.append(f"<tr>{''.join(tds)}</tr>")
    return (f'<table class="data-table"><thead><tr>{head}</tr></thead>'
            f'<tbody>{"".join(trs)}</tbody></table>')


def _fmt(v):
    if isinstance(v, float):
        return f"{v:.4g}"
    if isinstance(v, list):
        return ", ".join(str(x) for x in v) if v else "—"
    return str(v)


def chart_div(fig, first=False) -> str:
    return pio.to_html(fig, include_plotlyjs=("cdn" if first else False),
                       full_html=False, config={"displaylogo": False})


# --- page shell -----------------------------------------------------------------
def shell(active: str, title: str, body: str) -> str:
    nav = "".join(
        f'<a class="nav-link" href="{href}" '
        f'style="{"background:#161b22;color:#e6edf3" if href == active else ""}">{esc(label)}</a>'
        for href, label in NAV)
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{esc(title)} · upfbench</title>
<link rel="stylesheet" href="style.css">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&family=JetBrains+Mono:wght@500&display=swap" rel="stylesheet">
</head><body>
<div class="app">
  <aside class="sidebar">
    <div class="brand"><div class="brand-name">upfbench</div>
      <div class="brand-sub">UPF test framework</div></div>
    <nav class="nav">{nav}</nav>
    <div class="sidebar-foot"><div class="foot-org">coRAN Labs</div>
      <div class="foot-tag">performance · load · PFCP · N3 robustness</div></div>
  </aside>
  <main class="content"><div class="page">{body}</div></main>
</div>
</body></html>"""


# --- pages ----------------------------------------------------------------------
def page_overview(camps) -> str:
    latest = next((c for c in camps if c.totals["tests"] and c.kpis), None)
    hero = (
        '<div class="hero"><h1>A portable benchmark &amp; robustness framework for 5G UPFs</h1>'
        '<p class="hero-sub">upfbench drives any open-source UPF black-box over its real '
        'interfaces — GTP-U on N3 (TRex) and PFCP on N4 (pfcpsim) — and reports '
        'standards-aligned performance, multi-UE load, N4 conformance, and data-plane '
        'robustness.</p><div class="hero-tags">'
        + pill("SD-Core BESS-UPF", T.ACCENT) + pill("DPDK · AF_XDP · CNDP · AF_PACKET", T.ACCENT2)
        + pill("TRex + pfcpsim", T.GOOD) + pill("RFC 2544 / 8219 / 9004 · TS 29.244", T.WARN)
        + "</div></div>")
    if latest:
        k = latest.kpis
        tiles = (kpi("Peak throughput", f"{k.get('pdr','—')} Mpps", "PDR @0.1% loss", T.ACCENT)
                 + kpi("In-pipeline latency", f"{k.get('lat','—')} µs", "avg / p99", T.ACCENT2)
                 + kpi("Max UE sessions", k.get("max_sessions", "—"), "concurrent", T.GOOD)
                 + kpi("Load aggregate", f"{k.get('load_aggregate_mpps','—')} Mpps", "multi-UE", T.WARN))
        headline = card(f'<div class="kpi-row">{tiles}</div>'
                        f'<div class="muted small">From <b>{esc(latest.campaign_id)}</b> · '
                        f'{latest.mode.upper()} · {latest.date}</div>',
                        title="Headline results", sub="most recent campaign with measured data")
    else:
        headline = card('<div class="muted">No campaigns with data.</div>', title="Headline")
    finding = card(
        f'<div class="finding-tag">{pill("REMOTE DoS", T.BAD)} N3 negative suite</div>'
        '<p><b>A single malformed GTP-U packet crashes the SD-Core BESS-UPF data plane</b> — '
        'a SIGSEGV in <code>GtpuDecap::ProcessBatch</code> (malformed PSC 0x85 and truncation '
        'variants). The user plane drops until Kubernetes restarts the container. The suite '
        'detects the crash, attributes it to the culprit packet, recovers the UPF, and reports it.</p>'
        '<a class="finding-link" href="campaigns.html">See the N3 robustness results →</a>',
        title="Headline robustness finding", cls="finding")
    suites = "".join(
        f'<div class="suite-card"><div class="suite-card-title">{esc(SUITE_LABEL.get(k,k))}</div>'
        f'<div class="suite-card-desc">{esc(d)}</div></div>'
        for k, d in [
            ("performance", "Throughput (NDR/PDR), latency, jitter, burst, multi-flow — RFC 2544 / 8219 / 9004, ETSI TST009."),
            ("load", "Multi-UE: max concurrent sessions, aggregate + per-UE throughput, latency under load."),
            ("pfcp", "TS 29.244 N4 conformance: association, establish, modify, delete, error handling."),
            ("n3neg", "N3 data-plane negative/robustness: malformed GTP-U, unknown TEID, PSC (0x85) ext-header.")])
    return shell("index.html", "Overview",
                 hero + f'<div class="grid-2">{headline}{finding}</div>'
                 + "<h2>What it tests</h2>" + f'<div class="suite-cards">{suites}</div>')


def _camp_row(c) -> str:
    t = c.totals
    badges = (pill(c.mode.upper(), T.mode_color(c.mode))
              + f'<span class="camp-date">{esc(c.date)}</span>')
    if t["good"]:
        badges += pill(f"{t['good']}✓", T.GOOD)
    if t["bad"]:
        badges += pill(f"{t['bad']}✗", T.BAD)
    suites = " · ".join(s.label for s in c.suites) or "no suites"
    return (f'<a class="camp-row" href="campaign-{esc(c.key)}.html">'
            f'<div class="camp-main"><div class="camp-name">{esc(c.campaign_id)}</div>'
            f'<div class="camp-suites">{esc(suites)}</div></div>'
            f'<div class="camp-meta">{badges}</div></a>')


def page_campaigns(camps) -> str:
    measured = [c for c in camps if c.totals["tests"]]
    empty = [c for c in camps if not c.totals["tests"]]
    body = (f'<div class="page-head"><h1>Campaigns</h1>'
            f'<div class="muted">{len(measured)} with results · {len(empty)} empty/stub</div></div>'
            f'<div class="camp-list">{"".join(_camp_row(c) for c in measured)}</div>')
    if empty:
        body += (f'<details class="empty-block"><summary>{len(empty)} empty / debug campaigns'
                 f'</summary><div class="camp-list">{"".join(_camp_row(c) for c in empty)}</div></details>')
    return shell("campaigns.html", "Campaigns", body)


def _sut_card(sut) -> str:
    fields = [("Mode", (sut.get("mode") or "?").upper()), ("UPF", sut.get("upf", "—")),
              ("Image", sut.get("upf_image", "—")), ("NIC", sut.get("nic", "—")),
              ("CPU", sut.get("cpu", "—")), ("Platform", sut.get("platform", "—")),
              ("N3 / N6", f"{sut.get('n3_iface','?')} / {sut.get('n6_iface','?')}"),
              ("UE pool", sut.get("ue_ip_pool", "—"))]
    rows = "".join(f'<div class="sut-row"><span class="sut-k">{esc(k)}</span>'
                   f'<span class="sut-v">{esc(v)}</span></div>' for k, v in fields)
    return card(f'<div class="sut-grid">{rows}</div>', title="System under test")


def _test_panel(test, first_chart) -> tuple[str, bool]:
    head = (f'<div class="test-head"><span class="test-id">{esc(test.id)}</span>'
            f'<span class="test-name">{esc(test.name)}</span>{status_pill(test.status)}</div>')
    metrics = ""
    items = [(k, v) for k, v in test.metrics.items() if not isinstance(v, dict)]
    if items:
        metrics = ('<div class="metric-grid">' + "".join(
            f'<div class="metric"><div class="metric-val">{esc(_fmt(v))}</div>'
            f'<div class="metric-key">{esc(k.replace("_"," "))}</div></div>' for k, v in items)
            + "</div>")
    charts = ""
    for title, fig in charts_for(test):
        charts += chart_div(fig, first=first_chart)
        first_chart = False
    tabs = ""
    for tname, rows in test.tables.items():
        tabs += f'<div class="table-cap">{esc(tname)}</div>' + table(rows)
    notes = f'<div class="test-notes">{esc(test.notes)}</div>' if test.notes else ""
    return (f'<div class="test-panel">{head}{metrics}{charts}{tabs}{notes}</div>', first_chart)


def page_campaign(c) -> str:
    t = c.totals
    badges = pill(c.mode.upper(), T.mode_color(c.mode)) + f'<span class="muted">{esc(c.date)}</span>'
    if t["good"]:
        badges += pill(f"{t['good']} ok", T.GOOD)
    if t["bad"]:
        badges += pill(f"{t['bad']} fail", T.BAD)
    body = ('<a class="back-link" href="campaigns.html">← campaigns</a>'
            f'<div class="page-head"><h1>{esc(c.campaign_id)}</h1>'
            f'<div class="head-badges">{badges}</div></div>' + _sut_card(c.sut))
    first = True
    for s in c.suites:
        cc = s.counts
        summ = ""
        if cc["good"]:
            summ += pill(f"{cc['good']} ok", T.GOOD)
        if cc["bad"]:
            summ += pill(f"{cc['bad']} fail", T.BAD)
        if cc["other"]:
            summ += pill(f"{cc['other']} other", T.WARN)
        body += (f'<div class="suite-section"><div class="suite-head"><h3>{esc(s.label)}</h3>'
                 f'<div class="suite-summary">{summ}</div></div>')
        for test in s.tests:
            panel, first = _test_panel(test, first)
            body += panel
        body += "</div>"
    return shell("campaigns.html", c.campaign_id, body)


def _run_chip(c, selected=False) -> str:
    w = f" · {c.workers}w" if c.workers else ""
    meta = f"{c.upf.replace('sd-core ','SD-Core ')} · {c.mode.upper()}{w} · {c.date[:10]}"
    cls = "run-chip selected" if selected else "run-chip"
    return (f'<button class="{cls}" data-group="{c.group}" data-upf="{esc(c.upf)}" '
            f'data-mode="{esc(c.mode)}" data-workers="{c.workers or ""}" data-date="{esc(c.date[:10])}">'
            f'<span class="run-chip-name" style="border-color:{T.mode_color(c.mode)}">{esc(c.campaign_id)}</span>'
            f'<span class="run-chip-meta">{esc(meta)}</span></button>')


def _run_picker(camps) -> str:
    """Grouped run selector: verified runs shown, experimental collapsed. Carries per-run
    metadata as data-* attributes so a designer can bind to group/upf/mode/workers/date."""
    perf = [c for c in camps if c.suite("performance")]
    verified = [c for c in perf if c.group == "verified"]
    experimental = [c for c in perf if c.group == "experimental"]
    sel = {c.key for c in verified[:3]}
    chips_v = "".join(_run_chip(c, c.key in sel) for c in verified) or '<span class="muted">none</span>'
    block = ('<div class="run-group-label">Verified runs</div>'
             f'<div class="run-chips">{chips_v}</div>')
    if experimental:
        chips_e = "".join(_run_chip(c) for c in experimental)
        block += ('<details class="run-experimental"><summary>Experimental / debug '
                  f'({len(experimental)})</summary><div class="run-chips">{chips_e}</div></details>')
    return card(block, title="Runs", sub=f"{len(sel)} of {len(verified)} verified selected")


def page_compare(camps) -> str:
    import plotly.graph_objects as go
    from .charts import _num, _rows
    perf = [c for c in camps if c.suite("performance") and c.group == "verified"][:4]
    fig = go.Figure()
    any_data = False
    for i, c in enumerate(perf):
        test = next((t for t in c.suite("performance").tests
                     for tn in t.tables if "frame size" in tn.lower()), None)
        if not test:
            continue
        rows = _rows(test, "frame size")
        if not rows:
            continue
        any_data = True
        fig.add_trace(go.Scatter(x=[r.get("frame_B") for r in rows],
                                 y=[_num(r.get("PDR_Mpps")) for r in rows],
                                 name=f"{c.mode.upper()} · {c.campaign_id}", mode="lines+markers",
                                 line=dict(color=T.mode_color(c.mode, i), width=3)))
    # generator/NIC ceiling reference line — so a viewer sees when a run is rig-limited.
    if any_data:
        fig.add_hline(y=14.88, line=dict(color=T.MUTE, width=1, dash="dash"),
                      annotation_text="generator / NIC VEB ceiling", annotation_position="top left",
                      annotation_font_color=T.MUTE)
    fig.update_layout(xaxis_title="Frame size (B)", yaxis_title="Mpps",
                      title="Throughput (PDR) vs frame size")
    T.apply(fig, height=420)
    chart = chart_div(fig, first=True) if any_data else '<div class="muted">no performance data</div>'
    tabs = ('<div class="metric-tabs"><button class="metric-tab active">Throughput</button>'
            '<button class="metric-tab">Latency</button>'
            '<button class="metric-tab">Mpps / core</button></div>')
    body = ('<div class="page-head"><h1>Compare runs</h1>'
            '<div class="muted">Overlay performance across UPFs and dataplane modes. '
            'Mpps-per-core is the fair cross-UPF metric.</div></div>'
            + _run_picker(camps) + tabs
            + card(chart, title="Throughput vs frame size — PDR @0.1% loss",
                   sub="converging lines = both runs at the test-rig ceiling, not the UPF limit"))
    return shell("compare.html", "Compare runs", body)


def page_catalog() -> str:
    from .catalog_data import CATALOG
    blocks = ""
    for name, accent, tests in CATALOG:
        rows = "".join(
            f'<tr><td><span class="cat-id" style="color:{accent}">{esc(tid)}</span></td>'
            f'<td><b>{esc(tn)}</b></td><td class="cat-what">{esc(what)}</td>'
            f'<td>{pill(std, T.MUTE)}</td></tr>' for tid, tn, what, std in tests)
        tbl = (f'<table class="data-table catalog-table"><thead><tr><th>ID</th><th>Test</th>'
               f'<th>What it measures</th><th>Standard</th></tr></thead><tbody>{rows}</tbody></table>')
        blocks += card(tbl, title=name, sub=f"{len(tests)} test cases")
    head = ('<div class="page-head"><h1>Test catalog</h1>'
            '<div class="muted">Four suites · 16 test cases · black-box over N3 (TRex) + N4 (pfcpsim)</div></div>')
    return shell("catalog.html", "Test catalog", head + blocks)


def page_methodology() -> str:
    steps = [("TRex", "gen VF on the access PF", T.ACCENT),
             ("NIC VEB", "on-chip switch hairpins by dst MAC", T.MUTE),
             ("UPF access VF", "DPDK/XDP-owned port in the pod", T.ACCENT2),
             ("BESS pipeline", "PDR → QER → FAR", T.GOOD),
             ("core TX", "counted black-box", T.WARN)]
    flow = ""
    for i, (ti, su, co) in enumerate(steps):
        flow += (f'<div class="flow-node"><div class="flow-title" style="color:{co}">{esc(ti)}</div>'
                 f'<div class="flow-sub">{esc(su)}</div></div>')
        if i < len(steps) - 1:
            flow += '<div class="flow-arrow">→</div>'
    plugins = [("Adapters", "deploy / configure / counters / teardown — per UPF", "sdcore_bess ▸ oai ▸ open5gs ▸ free5gc ▸ eupf", T.ACCENT),
               ("Control", "program the data plane", "pfcpsim (N4/PFCP) · pybess (white-box short-circuit)", T.ACCENT2),
               ("Traffic", "offer N3/N6 load", "TRex (DPDK/XDP/CNDP) · testpmd · tcpreplay", T.GOOD),
               ("Suites", "the test cases + pass/fail logic", "performance · load · pfcp · n3neg", T.WARN)]
    pg = "".join(
        f'<div class="plugin-card"><div class="plugin-head"><b>{esc(n)}</b>{pill("pool", c)}</div>'
        f'<div class="plugin-what">{esc(w)}</div><div class="plugin-items">{esc(it)}</div></div>'
        for n, w, it, c in plugins)
    body = ('<div class="page-head"><h1>Methodology</h1></div>'
            + card(f'<div class="flow">{flow}</div>'
                   '<p class="body">Frames whose destination MAC equals the UPF access-VF MAC are '
                   'hairpinned by the NIC internal switch (VEB) straight into the UPF — validated '
                   '1:1. Throughput is computed from the UPF\'s own port counters, so it is '
                   'black-box and comparable across modes.</p>',
                   title="N3 injection — kernel-bypass UPFs",
                   sub="no host kernel socket exists on a DPDK/XDP access port, so we hairpin")
            + card('<p class="body">A pybess splice (<code>executeFAR → ubench_sink → coreQSplit</code>) '
                   'rewrites the egress MAC and bypasses route lookup, so synthetic traffic reaches '
                   'core TX without a real next-hop — and breaks the VEB re-circulation loop. On '
                   'non-BESS UPFs this is a no-op.</p>', title="Egress short-circuit")
            + "<h2>Plugin architecture</h2>" + f'<div class="plugin-grid">{pg}</div>')
    return shell("methodology.html", "Methodology", body)


def build(out: Path) -> Path:
    out.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(CSS_SRC, out / "style.css")
    camps = load_campaigns()
    (out / "index.html").write_text(page_overview(camps))
    (out / "campaigns.html").write_text(page_campaigns(camps))
    (out / "compare.html").write_text(page_compare(camps))
    (out / "catalog.html").write_text(page_catalog())
    (out / "methodology.html").write_text(page_methodology())
    n_detail = 0
    for c in camps:
        if c.totals["tests"]:
            (out / f"campaign-{c.key}.html").write_text(page_campaign(c))
            n_detail += 1
    print(f"wrote {out} — 5 top pages + {n_detail} campaign detail pages")
    return out


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(HERE / "static_export"))
    build(Path(ap.parse_args().out))
