"""The test catalog — suites, test cases, and the standard each maps to.

Kept Dash-free so both the live page (pages/catalog.py) and the static export can import it
without instantiating a Dash app.

Two domains are shown, in release order:
  * control plane (cpbench) — AMF/SMF/NRF/AUSF/UDM, built LIVE from cntc/standards/*-conformance.yaml
    (single source of truth — the same catalogs the verdict engine grades against, so this page
    can never drift from what actually runs).
  * user plane   (upfbench) — the UPF suites, curated below.
"""
from __future__ import annotations

import re
from pathlib import Path

import yaml

from . import theme as T

# Standard → spec URL (opened in a new tab from the catalog). Combined strings like
# "RFC 2544 / ETSI TST009" are split on "/" and each token linked independently. Standards
# without a known URL render as a neutral (unlinked) pill.
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
    for tok in [t.strip() for t in standard.split("/") if t.strip()]:
        out.append((tok, STANDARD_URLS.get(tok)))
    return out


# --- control plane: built live from the requirement catalogs -----------------------------
# NF -> (section accent, section title). Order = the order NFs appear on the page.
_CP_NF = {
    "amf":  (T.ACCENT,   "AMF · Access & Mobility Management (N1/N2 + SBI)"),
    "smf":  (T.ACCENT2,  "SMF · Session Management (N4/PFCP + SBI)"),
    "ausf": (T.WARN,     "AUSF · Authentication Server (SBI)"),
    "udm":  ("#0e7490",  "UDM · Unified Data Management (SBI)"),
    "nrf":  (T.GOOD,     "NRF · NF Repository (SBI)"),
}
# One-line "what it measures" per test id (supplements the catalog name; falls back to the
# name if an id ever appears here without an entry — so a new test still shows, never hides).
_CP_WHAT = {
    "AMF-REG-01": "UE completes initial registration",
    "AMF-AUTH-01": "AMF runs 5G-AKA with the UE",
    "AMF-AUTH-02": "NAS security-mode command / complete",
    "AMF-DEREG-01": "UE-initiated deregistration handled",
    "AMF-NGAP-01": "gNB↔AMF NGAP association (NG Setup)",
    "AMF-NGAP-02": "Initial UE message / NAS transport",
    "AMF-NGAP-03": "Initial context setup toward the gNB",
    "AMF-NGAP-04": "UE context release",
    "AMF-SEC-01": "NAS integrity-protected after SMC (no cleartext)",
    "AMF-SEC-02": "NAS ciphered after SMC",
    "AMF-SEC-04": "Wrong RES* rejected — no auth bypass",
    "AMF-SEC-06": "SUPI concealed as SUCI on N2",
    "AMF-SEC-07": "Namf SBI requires TLS + OAuth2",
    "AMF-NEG-01": "Malformed NGAP rejected, no crash",
    "AUSF-AUTH-01": "Initiates 5G-AKA authentication",
    "AUSF-AUTH-02": "Confirms RES* on success",
    "AUSF-SEC-02": "Nausf SBI requires TLS + OAuth2",
    "AUSF-NEG-01": "Malformed auth request rejected, no crash",
    "NRF-REG-01": "An NF registers its profile",
    "NRF-DISC-01": "Discovery by NF type returns profiles",
    "NRF-DISC-02": "Discovery by service name",
    "NRF-SEC-01": "OAuth2 access-token grant",
    "NRF-SEC-02": "No token → 403 (authorization enforced)",
    "NRF-SEC-03": "SBI endpoint requires TLS",
    "NRF-NEG-01": "Malformed register → 400, no crash",
    "NRF-NEG-02": "Unknown NF type → empty / 404",
    "SMF-SESS-01": "PDU session established",
    "SMF-SESS-03": "PDU session released",
    "SMF-SESS-04": "Multiple sessions / multi-DNN",
    "SMF-SESS-05": "UE IP address allocated",
    "SMF-N4-01": "SMF programs the UPF over N4 (PFCP)",
    "SMF-N4-03": "N4 session deleted on release",
    "SMF-DP-01": "Traffic forwards through the programmed UPF",
    "SMF-SEC-01": "Nsmf SBI requires TLS + OAuth2",
    "SMF-SEC-02": "Missing / invalid mandatory IE rejected",
    "SMF-SEC-03": "Session-event logging present",
    "SMF-NEG-01": "Invalid session request rejected, no crash",
    "UDM-SDM-01": "Subscription data retrieved (SDM_Get)",
    "UDM-SDM-02": "Session-management subscription data",
    "UDM-UECM-01": "AMF registration recorded (UECM)",
    "UDM-AUTH-01": "Generates authentication vectors",
    "UDM-SEC-01": "SUCI de-concealment (SIDF) authorized",
    "UDM-SEC-02": "Nudm SBI requires TLS + OAuth2",
    "UDM-NEG-01": "Unknown SUPI → 404, no crash",
}

_SPEC_RE = re.compile(r"\b(\d{2}\.\d{3})\b")
_CLAUSE_RE = re.compile(r"\s*\((?:[^)]*(?:§|\d{2}\.\d{3})[^)]*)\)\s*$")  # trailing spec clause only


def _clean_name(name: str) -> str:
    """Drop a trailing spec clause like '(24.501 §5.5.1.2)' — but keep descriptive parens."""
    return _CLAUSE_RE.sub("", name).strip()


def _standard_for(name: str, primary: str) -> str:
    """Per-test standard string: spec numbers mentioned in the name, else the NF's primary spec."""
    specs = []
    for s in _SPEC_RE.findall(name):
        tok = f"TS {s}"
        if tok not in specs:
            specs.append(tok)
    return " / ".join(specs) if specs else primary


def _primary_spec(standards: list[str]) -> str:
    """The NF's headline spec, as a 'TS xx.xxx' token, from the catalog's standards list."""
    for s in standards:
        m = _SPEC_RE.search(s)
        if m:
            return f"TS {m.group(1)}"
    return "3GPP"


def _standards_dir() -> Path:
    return Path(__file__).resolve().parents[1] / "cntc" / "standards"


def _build_cp_catalog():
    """Read the per-NF conformance catalogs and shape them like the UPF CATALOG sections."""
    out = []
    d = _standards_dir()
    for nf, (accent, title) in _CP_NF.items():
        f = d / f"{nf}-conformance.yaml"
        if not f.exists():
            continue
        cat = yaml.safe_load(f.read_text()) or {}
        primary = _primary_spec(cat.get("standards", []))
        rows = []
        for t in cat.get("tests", []):
            tid = t["id"]
            essential = t.get("class") == "essential"
            what = _CP_WHAT.get(tid) or _clean_name(t["name"])
            if essential:
                what += "  · essential"
            rows.append((tid, _clean_name(t["name"]), what, _standard_for(t["name"], primary)))
        if rows:
            out.append((title, accent, rows))
    return out


# (suite, accent, [(id, name, what it measures, standard)])
_UPF_CATALOG = [
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

# Built at import time. Exposed per-domain so the catalog page can group them under headers,
# and combined (control plane first, this release) for back-compatible callers.
CP_CATALOG = _build_cp_catalog()
UPF_CATALOG = _UPF_CATALOG
CATALOG = CP_CATALOG + UPF_CATALOG

# The two domains, each with a heading + one-line blurb, for a sectioned catalog page.
DOMAINS = [
    ("Control plane", "5G core network functions — driven over N1/N2 (NAS/NGAP) and the "
     "Service-Based Interface, graded against each NF's protocol + SCAS spec.", CP_CATALOG),
    ("User plane", "The UPF — black-box over N3 (GTP-U) and N4 (PFCP), plus optional white-box "
     "eBPF/XDP dataplane assurance.", UPF_CATALOG),
]


def domain_summary(catalog) -> str:
    """'N suites · M test cases (K essential)' for a domain's catalog list."""
    n_suites = len(catalog)
    n_tests = sum(len(tests) for _, _, tests in catalog)
    n_ess = sum(1 for _, _, tests in catalog for t in tests if "· essential" in t[2])
    ess = f" ({n_ess} essential)" if n_ess else ""
    return f"{n_suites} suites · {n_tests} test cases{ess}"


def catalog_summary() -> str:
    """One-line header sub: suite + test totals across both domains."""
    n_suites = len(CATALOG)
    n_tests = sum(len(tests) for _, _, tests in CATALOG)
    return (f"{n_suites} suites · {n_tests} test cases · "
            "5G control plane (N1/N2 + SBI) and user plane (N3/N4)")


def cp_nf_counts() -> dict[str, tuple[int, int]]:
    """{nf: (total_tests, essential_tests)} from the per-NF conformance catalogs."""
    out: dict[str, tuple[int, int]] = {}
    d = _standards_dir()
    for nf in _CP_NF:
        f = d / f"{nf}-conformance.yaml"
        if not f.exists():
            continue
        cat = yaml.safe_load(f.read_text()) or {}
        tests = cat.get("tests", [])
        out[nf] = (len(tests), sum(1 for t in tests if t.get("class") == "essential"))
    return out
