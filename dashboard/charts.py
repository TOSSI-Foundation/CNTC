"""Plotly figure builders for each test's data.

Each builder takes a ``Test`` and returns a list of ``(title, figure)`` — a test can yield
more than one chart. ``charts_for(test)`` dispatches by test id; unknown tests fall back to
a generic table render (handled by the page, not here). All numeric coercion is defensive
because results.json values are JSON scalars (ints, floats, strings, lists).
"""
from __future__ import annotations

import plotly.graph_objects as go

from . import theme as T
from .data import Test


def _num(v, default=0.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _rows(test: Test, name_contains: str):
    for tname, rows in test.tables.items():
        if name_contains.lower() in tname.lower() and rows:
            return rows
    return []


# --- performance ----------------------------------------------------------------
def _tc01(test: Test):
    rows = _rows(test, "frame size")
    if not rows:
        return []
    x = [r.get("frame_B") for r in rows]
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=x, y=[_num(r.get("PDR_Mpps")) for r in rows],
                             name="PDR (0.1% loss)", mode="lines+markers",
                             line=dict(color=T.ACCENT, width=3)))
    fig.add_trace(go.Scatter(x=x, y=[_num(r.get("NDR_Mpps")) for r in rows],
                             name="NDR (0% loss)", mode="lines+markers",
                             line=dict(color=T.ACCENT2, width=3, dash="dot")))
    fig.add_trace(go.Scatter(x=x, y=[_num(r.get("fwd_Mpps@PDR")) for r in rows],
                             name="Forwarded @PDR", mode="lines+markers",
                             line=dict(color=T.GOOD, width=2)))
    fig.update_layout(xaxis_title="Frame size (B)", yaxis_title="Mpps")
    return [("Throughput vs frame size", T.apply(fig))]


def _tc03(test: Test):
    rows = _rows(test, "latency")
    if not rows:
        return []
    r = rows[0]
    pcts = [("min", "min_us"), ("p50", "p50_us"), ("p90", "p90_us"),
            ("p99", "p99_us"), ("p99.9", "p99.9_us"), ("max", "max_us")]
    labels = [lbl for lbl, k in pcts if k in r]
    vals = [_num(r.get(k)) for lbl, k in pcts if k in r]
    fig = go.Figure(go.Bar(x=labels, y=vals, marker_color=T.ACCENT,
                           text=[f"{v:.1f}" for v in vals], textposition="outside"))
    fig.update_layout(xaxis_title="percentile", yaxis_title="latency (µs)")
    return [("In-pipeline latency distribution", T.apply(fig))]


def _saturation(test: Test, contains: str, title: str):
    rows = _rows(test, contains)
    if not rows:
        return []
    r = rows[0]
    cats = ["offered", "absorbed", "forwarded"]
    vals = [_num(r.get(f"{c}_Mpps")) for c in cats]
    fig = go.Figure(go.Bar(x=[c.title() for c in cats], y=vals,
                           marker_color=[T.MUTE, T.ACCENT2, T.GOOD],
                           text=[f"{v:.2f}" for v in vals], textposition="outside"))
    drops = _num(r.get("pipeline_drops"))
    fig.update_layout(yaxis_title="Mpps",
                      title=f"{title} — pipeline drops: {int(drops)}")
    return [(title, T.apply(fig))]


# --- load -----------------------------------------------------------------------
def _lt01(test: Test):
    rows = _rows(test, "capacity ramp")
    if not rows:
        return []
    x = [r.get("ue_count") for r in rows]
    fig = go.Figure(go.Scatter(x=x, y=[_num(r.get("sessions_per_s")) for r in rows],
                               mode="lines+markers", line=dict(color=T.ACCENT, width=3),
                               name="sessions/s"))
    fig.update_layout(xaxis_title="UE sessions installed", yaxis_title="install rate (sessions/s)",
                      xaxis_type="log")
    return [("Session install rate vs scale", T.apply(fig))]


def _lt02(test: Test):
    out = []
    perue = _rows(test, "Per-UE")
    if perue:
        ues = [str(r.get("ue")) for r in perue]
        fig = go.Figure()
        fig.add_trace(go.Bar(x=ues, y=[_num(r.get("sent")) for r in perue], name="sent",
                             marker_color=T.MUTE))
        fig.add_trace(go.Bar(x=ues, y=[_num(r.get("forwarded")) for r in perue],
                             name="forwarded", marker_color=T.GOOD))
        fig.update_layout(barmode="overlay", xaxis_title="verified UE",
                          yaxis_title="packets")
        out.append(("Per-UE forwarding (sent vs forwarded)", T.apply(fig)))
    return out


def _lt03(test: Test):
    rows = _rows(test, "Latency vs UE")
    if not rows:
        return []
    x = [r.get("ues") for r in rows]
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=x, y=[_num(r.get("p99_us")) for r in rows], name="p99",
                             mode="lines+markers", line=dict(color=T.ACCENT2, width=3)))
    fig.add_trace(go.Scatter(x=x, y=[_num(r.get("avg_us")) for r in rows], name="avg",
                             mode="lines+markers", line=dict(color=T.ACCENT, width=3)))
    fig.update_layout(xaxis_title="UE count", yaxis_title="latency (µs)", xaxis_type="log")
    return [("Latency under load vs UE count", T.apply(fig))]


# --- n3neg ----------------------------------------------------------------------
def _nt02(test: Test):
    rows = _rows(test, "variants")
    if not rows:
        return []
    labels = [r.get("variant") for r in rows]
    fwd = [_num(r.get("forwarded")) for r in rows]
    colors = [T.BAD if str(r.get("crashed_bessd", "")).upper() == "YES" else T.ACCENT
              for r in rows]
    fig = go.Figure(go.Bar(x=labels, y=fwd, marker_color=colors,
                           text=[("CRASH" if str(r.get("crashed_bessd","")).upper()=="YES"
                                  else f"{int(f)}") for r, f in zip(rows, fwd)],
                           textposition="outside"))
    fig.update_layout(xaxis_title="malformed variant", yaxis_title="packets forwarded")
    return [("Malformed GTP-U: forwarded vs dropped (red = crashed bessd)", T.apply(fig))]


def _tc02(test: Test):
    rows = _rows(test, "Bidirectional")
    if not rows:
        return []
    x = [r.get("frame_B") for r in rows]
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=x, y=[_num(r.get("aggregate_Mpps")) for r in rows],
                             name="Aggregate (UL+DL)", mode="lines+markers",
                             line=dict(color=T.GOOD, width=3)))
    fig.add_trace(go.Scatter(x=x, y=[_num(r.get("UL_fwd_Mpps")) for r in rows],
                             name="Uplink (N3→core)", mode="lines+markers",
                             line=dict(color=T.ACCENT, width=2)))
    fig.add_trace(go.Scatter(x=x, y=[_num(r.get("DL_fwd_Mpps")) for r in rows],
                             name="Downlink (N6→access)", mode="lines+markers",
                             line=dict(color=T.ACCENT2, width=2, dash="dot")))
    fig.update_layout(xaxis_title="Frame size (B)", yaxis_title="Forwarded Mpps")
    return [("Bidirectional throughput (UL + DL + aggregate)", T.apply(fig))]


_BUILDERS = {
    "TC-01": _tc01, "TC-02": _tc02, "TC-03": _tc03,
    "TC-04": lambda t: _saturation(t, "Burst", "Burst / back-to-back"),
    "TC-08": lambda t: _saturation(t, "Multi-flow", "Multi-flow (RSS)"),
    "LT-01": _lt01, "LT-02": _lt02, "LT-03": _lt03,
    "NT-02": _nt02,
}


def charts_for(test: Test):
    """Return [(title, figure), ...] for a test, or [] if it has no dedicated chart."""
    fn = _BUILDERS.get(test.id)
    try:
        return fn(test) if fn else []
    except Exception:
        return []
