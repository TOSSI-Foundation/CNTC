"""The test catalog — suites, test cases, and the standard each maps to.

Kept Dash-free so both the live page (pages/catalog.py) and the static export can import it
without instantiating a Dash app.
"""
from __future__ import annotations

from . import theme as T

# Standard → spec URL (opened in a new tab from the catalog). Combined strings like
# "RFC 2544 / ETSI TST009" are split on "/" and each token linked independently.
STANDARD_URLS = {
    "RFC 2544": "https://www.rfc-editor.org/rfc/rfc2544",
    "RFC 8219": "https://www.rfc-editor.org/rfc/rfc8219",
    "RFC 9004": "https://www.rfc-editor.org/rfc/rfc9004",
    "ETSI TST009": "https://www.etsi.org/deliver/etsi_gs/NFV-TST/001_099/009/",
    "TS 29.244": "https://portal.3gpp.org/desktopmodules/Specifications/SpecificationDetails.aspx?specificationId=3111",
}
# suite key -> the cat-id--<suite> CSS modifier (colors the test id)
SUITE_CATID = {"Performance": "perf", "Multi-UE Load": "load",
               "PFCP Conformance": "pfcp", "N3 Robustness": "n3"}


def standard_links(standard: str):
    """Split a standard string into [(label, url_or_None), ...] for rendering as links."""
    out = []
    for tok in [t.strip() for t in standard.split("/")]:
        out.append((tok, STANDARD_URLS.get(tok)))
    return out


# (suite, accent, [(id, name, what it measures, standard)])
CATALOG = [
    ("Performance", T.ACCENT, [
        ("TC-01", "Throughput vs frame size", "NDR (0% loss) + PDR (0.1% loss) per frame size via TST009 binary search", "RFC 2544 / ETSI TST009"),
        ("TC-02", "Bidirectional throughput", "Simultaneous UL+DL throughput", "RFC 2544"),
        ("TC-03", "Latency / jitter", "In-pipeline latency distribution (min/p50/p90/p99/p99.9/max) + jitter", "RFC 8219"),
        ("TC-04", "Burst / back-to-back", "Saturation absorb/forward + drain tail, pipeline drops", "RFC 9004"),
        ("TC-08", "Multi-flow (RSS)", "Aggregate throughput with N flows spread across worker queues", "RFC 2544"),
    ]),
    ("Multi-UE Load", T.ACCENT2, [
        ("LT-01", "Max concurrent sessions", "Capacity ceiling — install N UE sessions, measure rate & ceiling", "—"),
        ("LT-02", "Aggregate + per-UE throughput", "Throughput under N UEs and per-UE forwarding fairness", "—"),
        ("LT-03", "Latency vs UE count", "In-pipeline latency as session count & offered load scale", "RFC 8219"),
    ]),
    ("PFCP Conformance", T.GOOD, [
        ("CF-01", "Association setup / release", "PFCP node association lifecycle", "TS 29.244"),
        ("CF-02", "Session establishment", "PDR/FAR/QER accepted on establish", "TS 29.244"),
        ("CF-03", "Session modification", "Modify an established session", "TS 29.244"),
        ("CF-04", "Session deletion", "Tear down a session cleanly", "TS 29.244"),
        ("CF-05", "Error handling", "Unknown SEID / missing IE → correct cause", "TS 29.244"),
    ]),
    ("N3 Robustness", T.BAD, [
        ("NT-01", "Unknown TEID", "GTP-U on a TEID with no PDR must be dropped, not leaked", "robustness"),
        ("NT-02", "Malformed GTP-U", "6 variants (bad type/version, 3 truncation cases) — must not crash", "robustness"),
        ("NT-03", "PSC (0x85) ext-header", "Valid vs malformed 5G PDU-Session-Container handling", "robustness"),
    ]),
]
